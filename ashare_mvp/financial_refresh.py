"""Refresh observed financial dependencies before the same-period as-of join.

Current provider responses are new observations, not historical original vintages.
Keep the earlier normalized dependencies and merge versions before joining them.
"""
from pathlib import Path
from datetime import datetime,timezone
import argparse,json
import pandas as pd
from .workspace_paths import WorkspacePaths
from .phase2_protocol import sha256,day
from .disk_market import code_hashes,write_manifest
from .financial_batch import download_financials,get_financial_page
from .balance_batch import download_balance_batch,normalize_balance_batch,get_balance_page
from .observed_financials import normalize_observed_financials
from .financial_snapshot_history import merge_dependency_observations
from .financial_dependencies import combine_financial_files
from .network_retry import fetch_with_retry

CODES=['financial_refresh.py','financial_batch.py','balance_batch.py','observed_financials.py','financial_snapshot_history.py','financial_dependencies.py','security_identity.py','workspace_paths.py','network_retry.py']


def refresh(project,work_root,inputs,out,earnings_fetch=None,balance_fetch=None):
    paths=WorkspacePaths(project,work_root);config_file=paths.resolve(inputs);target=paths.resolve(out)
    config=json.loads(config_file.read_text(encoding='utf8'))
    if config.get('schema')!='observed_financial_refresh_inputs_v1' or not config.get('history'):raise ValueError('explicit observed dependency history required')
    signal=day(config['signal_date']);started=pd.Timestamp.now(tz='UTC')
    if signal!=started.tz_convert('Asia/Shanghai').tz_localize(None).normalize():raise ValueError('refresh signal must be actual observation day; never backdate a new refresh')
    first,last=day(config['report_start']),day(config['report_end'])
    if first>last or last>=signal or first not in pd.date_range(first,last,freq='QE') or last not in pd.date_range(first,last,freq='QE'):raise ValueError('completed quarter-end refresh bounds required')
    calendar=paths.resolve(config['calendar']);identity=paths.resolve(config['code_changes'])
    histories=[{k:paths.resolve(row[k]) for k in ['earnings','balances']} for row in config['history']]
    flattened=[p for row in histories for p in row.values()]
    if len(flattened)!=len(set(flattened)):raise ValueError('repeated dependency snapshot')
    if target.exists() or not target.is_relative_to(paths.work) or any(target.is_relative_to(p) or p.is_relative_to(target) for p in [config_file,calendar,identity]+flattened):raise ValueError('fresh separate external financial output required')
    dates=pd.read_csv(calendar,dtype=str)
    if not {'calendar_date','is_trading_day'}.issubset(dates) or not dates.is_trading_day.isin(['0','1']).all():raise ValueError('known exchange calendar required')
    all_dates=pd.DatetimeIndex(pd.to_datetime(dates.calendar_date));sessions=all_dates[dates.is_trading_day.eq('1').to_numpy()]
    if all_dates.hasnans or all_dates.has_duplicates or not all_dates.is_monotonic_increasing or signal not in sessions or not (sessions>signal).any():raise ValueError('ordered calendar must include signal and a later session')
    sources={paths.name(p):sha256(p) for p in [config_file,calendar,identity]};panels={'earnings':[],'balances':[]}
    for row in histories:
        for family,name in [('earnings','financial_panel.csv'),('balances','balance_panel.csv')]:
            directory=row[family];meta=json.loads((directory/'summary.json').read_text(encoding='utf8'))
            if meta.get('partial_source') is not False or sha256(directory/name)!=meta['panel_sha256']:raise ValueError('complete unchanged financial dependency snapshots required')
            sources[paths.name(directory/name)]=meta['panel_sha256'];sources[paths.name(directory/'summary.json')]=sha256(directory/'summary.json')
            panels[family].append(pd.read_csv(directory/name,low_memory=False,float_precision='round_trip'))
    # Validate all prior clocks and conflicts before network activity.
    for family in panels:merge_dependency_observations(panels[family])
    codes=code_hashes(CODES);target.mkdir(parents=True)
    proof=dict(schema='observed_financial_refresh_v1',status='acquiring',signal_date=str(signal.date()),started_at_utc=started.isoformat(),source_hashes=sources,code_hashes=codes,
               source_requests={},certified=False,model_ready=False,original_vintages_verified=False,model_selection_performed=False,prediction_generated=False,
               limitations=['Complete requested provider pages do not mean every issuer has reported or that sources are original vintages.',
                            'This intraday observation does not assert a final daily financial snapshot.',
                            'Company-action review, raw-market quality and daily recipe preparation remain separate.'])
    def save():write_manifest(target/'refresh.json',proof)
    save()
    fetchers={'earnings':earnings_fetch or get_financial_page,'balances':balance_fetch or get_balance_page}
    try:
        for family,download in [('earnings',download_financials),('balances',download_balance_batch)]:
            try:
                fetch=fetchers[family]
                raw=download(target/'raw'/family,str(first.date()),str(last.date()),fetch=lambda d,p:fetch_with_retry(lambda:fetch(d,p)))
                proof['source_requests'][family]=dict(status=raw['status'],manifest_sha256=sha256(target/'raw'/family/'manifest.json'))
            except Exception as error:proof['source_requests'][family]=dict(status='failed',error=str(error))
            save()
        if any(r['status']!='downloaded_raw' for r in proof['source_requests'].values()):
            proof.update(status='partial_raw_no_normalization',completed_at_utc=datetime.now(timezone.utc).isoformat());save();return proof
        normalize_observed_financials(target/'raw/earnings',sessions,target/'normalized/earnings')
        normalize_balance_batch(target/'raw/balances',calendar,target/'normalized/balances')
        for family,name in [('earnings','financial_panel.csv'),('balances','balance_panel.csv')]:
            new=target/'normalized'/family
            panels[family].append(pd.read_csv(new/name,low_memory=False,float_precision='round_trip'))
            combined=merge_dependency_observations(panels[family]);directory=target/'history'/family;directory.mkdir(parents=True)
            combined.to_csv(directory/name,index=False)
            write_manifest(directory/'summary.json',dict(schema='observed_dependency_history_v1',partial_source=False,panel_sha256=sha256(directory/name),records=len(combined),
                          source_records=[len(f) for f in panels[family]],built_at_utc=datetime.now(timezone.utc).isoformat(),certified=False,source_hashes=sources|{paths.name(new/'summary.json'):sha256(new/'summary.json'),paths.name(new/name):sha256(new/name)}))
        result=combine_financial_files(target/'history/earnings',target/'history/balances',target/'history/combined',code_changes=pd.read_json(identity,convert_dates=False))
        # Existing join arithmetic is reused unchanged; its descriptive source
        # directories become portable references before the summary is sealed.
        for row in result['sources']:row['directory']=paths.name(Path(row['directory']))
        write_manifest(target/'history/combined/summary.json',result)
        panel=pd.read_csv(target/'history/combined/financial_panel.csv',parse_dates=['report_date','available_date'])
        observed=pd.to_datetime(panel.first_observed_at_utc,utc=True)
        if observed.isna().any() or observed.gt(pd.Timestamp.now(tz='UTC')).any() or panel.available_date.lt(observed.dt.tz_convert('Asia/Shanghai').dt.tz_localize(None).dt.normalize()).any():raise ValueError('combined financial clocks missing, future or backdated')
        for name in ['earnings_available_date','balance_available_date']:
            dependency=pd.to_datetime(panel[name]);known=dependency.notna()
            if dependency[known].gt(panel.loc[known,'available_date']).any():raise ValueError('future financial dependency')
        for name,digest in sources.items():
            if sha256(paths.resolve(name))!=digest:raise ValueError('prior financial source changed during refresh')
        if code_hashes(CODES)!=codes:raise ValueError('financial implementation changed during refresh')
        proof.update(status='dependencies_refreshed_before_join',completed_at_utc=datetime.now(timezone.utc).isoformat(),
                     combined={k:result[k] for k in ['records','symbols','debt_records','panel_sha256']},
                     versions_visible_by_signal=int(panel.available_date.le(signal).sum()),versions_available_after_signal=int(panel.available_date.gt(signal).sum()),
                     output_hashes={p.relative_to(target).as_posix():sha256(p) for p in target.rglob('*') if p.is_file() and p!=target/'refresh.json'})
        save();return proof
    except BaseException as error:
        proof.update(status='failed',error=str(error),completed_at_utc=datetime.now(timezone.utc).isoformat());save();raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project',default='.')
    for name in ['work-root','inputs','out']:parser.add_argument('--'+name,required=True)
    result=refresh(**vars(parser.parse_args()))
    print(json.dumps(dict(status=result['status'],combined=result.get('combined'),prediction_generated=False)))
    if result['status']!='dependencies_refreshed_before_join':raise SystemExit(2)

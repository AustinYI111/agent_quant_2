"""Replay frozen horizon forecasts with reviewed reference-only evidence."""
import argparse,hashlib,json,shutil
from pathlib import Path
from datetime import datetime,timezone
import pandas as pd
import ashare_mvp
from ashare_mvp.public_conversion import load_public_data,_public_event_inputs
from ashare_mvp.reference_overrides import load_reference_evidence,apply_reference_overrides
from ashare_mvp.horizon_research import backtest_horizon_rankings,load_horizon_model
from ashare_mvp.causal_quality import IncompleteHorizonBacktests


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b''):value.update(block)
    return value.hexdigest()


def reference_only(actions,evidence,calendar):
    updated=apply_reference_overrides(actions,evidence,calendar)
    allowed={'reference_cash_per_share','reference_share_multiplier','reference_source_url','reference_source_sha256','reference_source_page'}
    columns=sorted(set(actions)-allowed)
    pd.testing.assert_frame_equal(actions[columns],updated[columns],check_exact=True)
    reference=sorted(allowed&set(updated))
    before=actions.reindex(columns=reference);after=updated[reference]
    same=before.eq(after)|(before.isna()&after.isna())
    changed=updated.loc[~same.all(axis=1),['symbol','ex_date']]
    permitted=set(zip(evidence.symbol,pd.to_datetime(evidence.ex_date)))
    if not set(zip(changed.symbol,changed.ex_date)).issubset(permitted):raise ValueError('reference changed outside reviewed keys')
    return updated,changed


def compare_prefixes(experiment,portfolios):
    # CSV may infer integer zeros in a short prefix and float zeros in a longer
    # file. Ignore that storage dtype only; numeric values must still be exact.
    checks=[]
    for horizon in ['5d','20d','12m']:
        source=experiment/'portfolios'/horizon/'partial_valid_nav.csv'
        if not source.exists():continue
        old=pd.read_csv(source,parse_dates=['date'],float_precision='round_trip')
        if old.empty:continue
        folder=portfolios/horizon
        nav=folder/'daily_nav.csv' if (folder/'daily_nav.csv').exists() else folder/'partial_valid_nav.csv'
        new=pd.read_csv(nav,parse_dates=['date'],float_precision='round_trip')
        new=new[new.date<=old.date.max()].reset_index(drop=True)
        # A completed horizon is padded to the shared comparison start with
        # untouched initial cash. Failed raw-account prefixes start at entry.
        leading=new[~new.date.isin(old.date)]
        if not leading.empty:
            untouched=(leading.date.lt(old.date.min()).all() and leading.nav.eq(1).all()
                and leading.cash.eq(1).all() and leading.positions.eq(0).all()
                and ('dividend_receivable' not in leading or leading.dividend_receivable.eq(0).all()))
            if not untouched:raise ValueError('unmatched valuation rows are not untouched initial cash')
        new=new[new.date.isin(old.date)].reset_index(drop=True)
        pd.testing.assert_frame_equal(old,new[old.columns],check_exact=True,check_dtype=False)
        old_orders=pd.read_csv(source.with_name('partial_orders.csv'),parse_dates=['date'],float_precision='round_trip')
        orders=folder/'orders.csv' if (folder/'orders.csv').exists() else folder/'partial_orders.csv'
        current=pd.read_csv(orders,parse_dates=['date'],float_precision='round_trip')
        current=current[current.date<=old.date.max()].reset_index(drop=True)
        pd.testing.assert_frame_equal(old_orders,current[old_orders.columns],check_exact=True,check_dtype=False)
        checks.append(dict(horizon=horizon,nav_rows=len(old),orders=len(old_orders),through=str(old.date.max().date()),
            leading_cash_sessions=len(leading),exact_match=True))
    return checks


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['experiment','data','benchmark','reference-overrides','out']:p.add_argument('--'+name,required=True)
    p.add_argument('--exploratory',action='store_true');args=p.parse_args()
    if not args.exploratory:p.error('explicit exploratory replay required')
    experiment,data_path,benchmark,out=(Path(name).resolve() for name in [args.experiment,args.data,args.benchmark,args.out])
    if out.exists() and any(out.iterdir()):raise ValueError('new empty valuation output required')
    protocol_raw=(experiment/'protocol.json').read_bytes();protocol=json.loads(protocol_raw)
    snapshot=json.loads((experiment/'code_snapshot_manifest.json').read_text(encoding='utf8'))
    if hashlib.sha256(protocol_raw).hexdigest()!=snapshot['protocol_sha256']:raise ValueError('frozen protocol changed')
    if digest(data_path/'metadata.json')!=snapshot['dataset_metadata_sha256']:raise ValueError('frozen training dataset changed')
    package=Path(ashare_mvp.__file__).resolve().parent
    names=['pipeline.py','horizon_research.py','horizon_labels.py','model.py','costs.py','causal_quality.py',
           'price_action_audit.py','price_reference.py','public_conversion.py','data.py','reference_overrides.py']
    code={name:digest(package/name) for name in names}
    if any(value!=snapshot['files']['ashare_mvp/'+name] for name,value in code.items()):raise ValueError('account or schedule implementation differs from frozen experiment')
    models=pd.read_csv(experiment/'models/models.csv')
    if set(models.horizon)!=set(['5d','20d','12m']):raise ValueError('all three original horizon models required')
    tracked={name:experiment/name for name in ['models/rankings.csv','models/models.csv','models/summary.json','protocol.json','code_snapshot_manifest.json']}
    for path in (experiment/'portfolios').glob('*/partial*.csv'):
        tracked[path.relative_to(experiment).as_posix()]=path
    if (experiment/'portfolios/incomplete.json').exists():
        tracked['portfolios/incomplete.json']=experiment/'portfolios/incomplete.json'
    for row in models.itertuples():
        model=(experiment/'models'/row.relative_model_path).resolve()
        if not model.is_relative_to((experiment/'models').resolve()):raise ValueError('model path escapes experiment')
        folder=model.parent;meta=json.loads((folder/'metadata.json').read_text(encoding='utf8'))
        load_horizon_model(folder,row.horizon,meta['features'])
        for name in ['model.txt','metadata.json','feature_input_policy.json']:
            tracked[(folder/name).relative_to(experiment).as_posix()]=folder/name
    initial={name:digest(path) for name,path in tracked.items()}
    original=json.loads((experiment/'models/summary.json').read_text(encoding='utf8'))
    for name in ['validation_start','test_start','test_end']:
        if original[name]!=protocol[name]:raise ValueError('model boundaries differ from protocol')
    for name in ['top_k','seed','embargo_sessions','train_months','retrain_months','min_train_dates','min_group_size','candidate_estimators']:
        if original['config'][name]!=protocol[name]:raise ValueError('strategy configuration differs from frozen protocol')
    print('Loading sealed original dataset and forecasts; no retraining or rescoring',flush=True)
    data=load_public_data(data_path,protocol['test_end']);raw,actions,tick=_public_event_inputs(data_path,data)
    if raw is None or data.metadata.get('quality_policy',{}).get('schema')!='causal_quality_research_v1':raise ValueError('sealed causal research raw inputs required')
    evidence=load_reference_evidence(args.reference_overrides)
    effective,changed=reference_only(actions,evidence,data.calendar)
    bench_meta=json.loads((benchmark/'summary.json').read_text(encoding='utf8'))
    if digest(benchmark/'summary.json')!=protocol['source_metadata_sha256']['benchmark_summary'] or digest(benchmark/'raw_prices.csv')!=bench_meta['raw_sha256']:raise ValueError('frozen benchmark changed')
    ranks=pd.read_csv(experiment/'models/rankings.csv',parse_dates=['date','available_date'],float_precision='round_trip')
    bench=pd.read_csv(benchmark/'raw_prices.csv')
    out.mkdir(parents=True,exist_ok=True)
    effective.to_csv(out/'effective_valuation_actions.csv',index=False)
    shutil.copyfile(args.reference_overrides,out/'valuation_reference_evidence.json')
    (out/'frozen_strategy_protocol.json').write_bytes(protocol_raw)
    audit=dict(schema='fixed_forecast_valuation_replay_v1',created_at_utc=datetime.now(timezone.utc).isoformat(),
        original_experiment_files_sha256=initial,dataset_metadata_sha256=snapshot['dataset_metadata_sha256'],
        code_sha256=code,driver_sha256=digest(__file__),
        sealed_dataset_files_sha256=data.metadata['converted_hashes'],benchmark_sha256=bench_meta['raw_sha256'],
        base_actions_sha256=data.metadata['converted_hashes']['event_corporate_actions.csv'],
        effective_actions_sha256=digest(out/'effective_valuation_actions.csv'),
        overlay_sha256=digest(out/'valuation_reference_evidence.json'),
        changed_reference_events=json.loads(changed.to_json(orient='records',date_format='iso')),
        forecasts_reused_without_changes=True,models_retrained=False,actual_economic_terms_changed=False,
        feature_policy='original causal feature resets and missing-label training subset remain frozen; overlay only validates account reference prices',
        certified=False,model_ready=False,status='running')
    def save():(out/'valuation_replay_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf8')
    save();failure=None
    try:
        print('Evaluating three unchanged forecast schedules with reviewed account references',flush=True)
        backtest_horizon_rankings(data,ranks,bench,bench_meta['return_kind'],out/'portfolios',protocol['top_k'],
            protocol['commission_bps'],protocol['slippage_bps'],None,raw,effective,tick)
    except IncompleteHorizonBacktests as exc:
        failure=[{key:r[key] for key in ['horizon','symbol','date','reason']} for r in exc.errors]
        exc.__traceback__=None
    audit['original_valid_prefix_checks']=compare_prefixes(experiment,out/'portfolios')
    if any(digest(path)!=initial[name] for name,path in tracked.items()):raise ValueError('original experiment mutated during replay')
    audit['status']='incomplete_actual_holdings' if failure else 'completed_all_three_portfolios'
    audit['errors']=failure or [];audit['finished_at_utc']=datetime.now(timezone.utc).isoformat();save()
    print(json.dumps(dict(status=audit['status'],errors=audit['errors'],prefix_checks=audit['original_valid_prefix_checks'])),flush=True)
    if failure:raise SystemExit(2)


if __name__=='__main__':main()

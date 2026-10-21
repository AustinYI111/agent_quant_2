"""Run frozen baseline research inference from sealed, dated local inputs.

No training, parameter selection, portfolio valuation or model promotion occurs.
Unresolved reference gaps use the already frozen feature segmentation/NaN rule.
"""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json,sqlite3
import numpy as np
import pandas as pd
from .data import Dataset
from .factors import default_library
from .public_conversion import public_snapshot
from .public_data import is_ashare
from .security_identity import code_change_maps,stitch_code_prices
from .phase2_protocol import day,sha256,load_seal,completed_date_limit
from .raw_feature_store import _verified_connection
from .streaming_feature_index import ExchangeCalendar
from .streaming_snapshot import FEATURE_COLUMNS,iter_seed_bars,advance_feature_snapshot
from .source_timing import audit_market_observations
from .horizon_inference import check_bundle
from .prediction_ledger import record_predictions,read_predictions,backup_ledger


def _utc_now():return datetime.now(timezone.utc).isoformat()


def run_frozen_daily(project,inputs,signal,out,exploratory=False):
    if not exploratory:raise ValueError('explicit exploratory mode required for unverified source vintages')
    root=Path(project).resolve()
    def path(value):
        p=Path(value)
        if p.is_absolute() or '..' in p.parts or ':' in str(p):raise ValueError('portable project-relative input/output paths required')
        result=(root/p).resolve()
        if not result.is_relative_to(root):raise ValueError('daily path escapes project')
        return result
    config_path=path(inputs);target=path(out);signal=day(signal)
    if target.exists():raise ValueError('new daily research snapshot output required')
    if signal>completed_date_limit(datetime.now(timezone.utc)):raise ValueError('signal has not passed the frozen close-release cutoff')
    config=json.loads(config_path.read_text(encoding='utf8'))
    if config.get('schema')!='frozen_daily_inputs_v1':raise ValueError('daily input recipe schema differs')
    protocol,seal=load_seal(path(config['seal']),root)
    if signal<day(protocol['independent_start']):raise ValueError('daily baseline inference requires independent-period signal')
    source_hashes={}
    def bind(file,expected=None):
        digest=sha256(file)
        if expected is not None and digest!=expected:raise ValueError('daily input hash differs: '+str(file.relative_to(root)))
        source_hashes[file.relative_to(root).as_posix()]=digest
        return digest
    bind(config_path)
    baseline=path(config['baseline']);store=path(config['raw_store']);seed=path(config['seed'])
    for directory in [baseline,store,seed,path(config['financials']),path(config['actions'])]:
        if directory==target or directory in target.parents or target in directory.parents:raise ValueError('daily output must be separate from input datasets')
    meta=json.loads((baseline/'metadata.json').read_text(encoding='utf8'));store_meta=json.loads((store/'manifest.json').read_text(encoding='utf8'));seed_meta=json.loads((seed/'manifest.json').read_text(encoding='utf8'))
    if meta.get('synthetic') is not False or store_meta.get('synthetic') is not False or meta.get('missing_policy')!='native_nan' or meta.get('quality_policy',{}).get('schema')!='causal_quality_research_v1':raise ValueError('frozen real native-NaN causal baseline required')
    code_names=['frozen_daily.py','streaming_snapshot.py','streaming_feature_index.py','factors.py','public_conversion.py','data.py','prediction_ledger.py','horizon_inference.py']
    def code_hash(name):return hashlib.sha256((root/'ashare_mvp'/name).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest()
    code_hashes={name:code_hash(name) for name in code_names}
    bind(baseline/'metadata.json',store_meta['baseline_metadata_sha256'])
    bind(store/'manifest.json');bind(seed/'manifest.json')
    if seed_meta.get('status')!='baseline_seed_verified' or seed_meta.get('synthetic') is not False or seed_meta['protocol_sha256']!=seal['protocol_sha256'] or signal<=day(seed_meta['asof']):raise ValueError('verified prior frozen feature seed required')
    if seed_meta['source_database_sha256']!=store_meta['database_sha256']:raise ValueError('seed/raw store lineage differs')
    bind(seed/'states.sqlite',seed_meta['state_database_sha256']);bind(seed/'feature_seed_bars.csv',seed_meta['feature_seed_sha256'])
    bind(store/'calendar.csv',store_meta['calendar_sha256'])
    old_cal=pd.DatetimeIndex(pd.to_datetime(pd.read_csv(store/'calendar.csv').date))
    authority=path(config['calendar']);bind(authority)
    authority_frame=pd.read_csv(authority,dtype=str)
    future_cal=pd.DatetimeIndex(pd.to_datetime(authority_frame.loc[authority_frame.is_trading_day.eq('1'),'calendar_date']))
    contract=ExchangeCalendar(future_cal)
    if not future_cal[future_cal<=old_cal[-1]].equals(old_cal[old_cal>=future_cal[0]]):raise ValueError('authoritative calendar revises known sessions')
    complete_cal=old_cal.append(future_cal[future_cal>old_cal[-1]])
    contract=ExchangeCalendar(complete_cal)
    if signal not in contract.positions:raise ValueError('signal not an exchange session')
    changes_file=path(config['code_changes']);bind(changes_file,store_meta['code_changes_sha256']);changes=pd.read_json(changes_file,convert_dates=False);roots,_,_=code_change_maps(changes)
    market_frames=[];seen=set();outside=[];market_dirs=[path(value) for value in config['markets']]
    for directory in market_dirs:
        bind(directory/'manifest.json');m=json.loads((directory/'manifest.json').read_text(encoding='utf8'))
        if m.get('schema')!='market_daily_raw_v1' or m.get('status')!='downloaded_raw' or m.get('synthetic') is not False:raise ValueError('complete real market captures required')
        bind(directory/'calendar.csv',m['files']['calendar.csv']['sha256'])
        for name,info in m['files'].items():
            if not name.startswith('daily_'):continue
            if Path(name).name!=name:raise ValueError('raw market basename required')
            bind(directory/name,info['sha256']);frame=pd.read_csv(directory/name,parse_dates=['date'],dtype={'code':str,'tradestatus':str,'isST':str,'adjustflag':str})
            d=frame.date.iloc[0] if len(frame) else pd.NaT
            if len(frame)!=info['rows'] or pd.isna(d) or not frame.date.eq(d).all() or d<=old_cal[-1] or d>signal or d in seen or frame.code.duplicated().any() or not frame.adjustflag.eq('3').all() or not frame.isST.isin(['0','1']).all():raise ValueError('new market date, basis or identity differs')
            seen.add(d);outside.append(frame.loc[~frame.code.map(is_ashare),['date','code']])
            active,rejected=stitch_code_prices(frame.loc[frame.code.map(is_ashare)].rename(columns={'code':'symbol'}),changes)
            if not rejected.empty:raise ValueError('daily quote has an unverified/inactive alias')
            active['is_st']=active.isST.astype(int)
            active['pe_ttm']=pd.to_numeric(active.peTTM,errors='raise');active['pb_mrq']=pd.to_numeric(active.pbMRQ,errors='raise')
            market_frames.append(active)
    if seen!=set(future_cal[(future_cal>old_cal[-1])&(future_cal<=signal)]):raise ValueError('missing new exchange sessions in daily recipe')
    if not market_frames:raise ValueError('new closed market input required')
    additional=pd.concat(market_frames,ignore_index=True)
    if additional.duplicated(['symbol','date']).any():raise ValueError('duplicate new issuer/date')
    latest_raw=additional[additional.date.eq(signal)].copy()
    if latest_raw.empty:raise ValueError('latest signal lacks raw quotes')
    additions={symbol:group.sort_values('date') for symbol,group in additional.groupby('symbol',sort=False)}
    financial=path(config['financials']);bind(financial/'summary.json');fm=json.loads((financial/'summary.json').read_text(encoding='utf8'));bind(financial/'financial_panel.csv',fm['panel_sha256'])
    fund_columns=['symbol','report_date','available_date','roe','revenue_growth','earnings_growth','debt_ratio']
    bind(baseline/'fundamentals.csv',meta['converted_hashes']['fundamentals.csv'])
    old_f=pd.read_csv(baseline/'fundamentals.csv',parse_dates=['report_date','available_date'],usecols=fund_columns).assign(_source_order=0)
    new_f=pd.read_csv(financial/'financial_panel.csv',parse_dates=['report_date','available_date'])
    observed=pd.to_datetime(new_f.first_observed_at_utc,utc=True)
    visible=new_f.available_date.notna()
    if observed[visible].isna().any() or new_f.loc[visible,'available_date'].lt(observed[visible].dt.tz_convert('Asia/Shanghai').dt.tz_localize(None).dt.normalize()).any() or observed.max()>pd.Timestamp.now(tz='UTC'):raise ValueError('financial availability precedes first observation or uses future input')
    funds=pd.concat([old_f,new_f[fund_columns].assign(_source_order=1)],ignore_index=True)
    if (funds.available_date.notna()&funds.report_date.gt(funds.available_date)).any():raise ValueError('financial report period is in its availability future')
    funds=funds.sort_values(['symbol','report_date','available_date','_source_order']).drop_duplicates(['symbol','report_date','available_date'],keep='last').drop(columns='_source_order')
    master=[]
    for item in config['masters']:
        file=path(item['file']);bind(file,item['sha256']);frame=pd.read_csv(file,dtype={'code':str});frame['symbol']=frame.code.map(lambda code:roots.get(code,code));frame['listing_date']=pd.to_datetime(frame.ipoDate);master.append(frame[['symbol','listing_date']])
    listing=pd.concat(master,ignore_index=True).groupby('symbol').listing_date.min().to_dict()
    actions_dir=path(config['actions']);bind(actions_dir/'summary.json');am=json.loads((actions_dir/'summary.json').read_text(encoding='utf8'));bind(actions_dir/'actions.csv',am['output_hashes']['actions.csv'])
    events=pd.read_csv(actions_dir/'actions.csv',float_precision='round_trip',low_memory=False)
    for column in ['ex_date','available_date','pay_date']:events[column]=pd.to_datetime(events[column],format='mixed')
    if events.duplicated(['symbol','ex_date']).any():raise ValueError('ambiguous daily action keys')
    event_groups={symbol:{row['ex_date']:row for row in group.to_dict('records')} for symbol,group in events.groupby('symbol',sort=False)}
    models={}
    for horizon,item in protocol['models'].items():
        models[horizon]=check_bundle(root/item['relative_path'])
        _,model,policy,training=models[horizon]
        if training['synthetic'] is not False or policy.get('price_basis')!=meta['price_basis'] or policy.get('quality_policy')!=meta['quality_policy']:raise ValueError('daily inputs differ from the frozen feature semantics')
    target.mkdir(parents=True)
    proof=dict(schema='frozen_daily_research_snapshot_v1',status='building',signal_date=str(signal.date()),started_at_utc=_utc_now(),protocol_sha256=seal['protocol_sha256'],issuers=0,raw_bars=0,reference_faults=0,certified=False,model_ready=False,purpose='feature_only_never_a_portfolio',model_selection_performed=False,financial_policy='newest visible period/revision; new observed panel wins exact same-day keys; unverified original vintages')
    def save():(target/'manifest.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    save();observations=audit_market_observations(market_dirs,target/'market_observations.json')
    seed_connection=sqlite3.connect((seed/'states.sqlite').resolve().as_uri()+'?mode=ro',uri=True);destination=sqlite3.connect(target/'states.sqlite')
    seed_connection.backup(destination)
    tail_iter=iter(iter_seed_bars(seed/'feature_seed_bars.csv'));current=next(tail_iter,None);latest_rows=[];break_rows=[];volume_audit=[]
    try:
        with _verified_connection(store) as connection:
            source_hashes[(store/'quotes.sqlite').relative_to(root).as_posix()]=store_meta['database_sha256']
            symbols=sorted({row[0] for row in connection.execute('SELECT DISTINCT symbol FROM quotes WHERE date>? AND date<=?',(seed_meta['asof'],str(signal.date())))}|set(additions))
            for symbol in symbols:
                while current is not None and current[0]<symbol:current=next(tail_iter,None)
                old=current[1] if current is not None and current[0]==symbol else pd.DataFrame(columns=FEATURE_COLUMNS)
                saved=seed_connection.execute('SELECT state_json FROM states WHERE symbol=?',(symbol,)).fetchone();state=json.loads(saved[0]) if saved else None
                if state is not None and old.empty:raise ValueError('persisted issuer lacks sealed seed bars')
                prior_listing=old.listing_date.dropna() if not old.empty else pd.Series(dtype='datetime64[ns]')
                ipo=prior_listing.iloc[-1] if len(prior_listing) else listing.get(symbol,pd.NaT)
                raw=pd.read_sql_query('SELECT * FROM quotes WHERE symbol=? AND date>? AND date<=? ORDER BY date',connection,params=(symbol,seed_meta['asof'],str(signal.date())),parse_dates=['date'])
                if symbol in additions:raw=pd.concat([raw,additions[symbol]],ignore_index=True)
                updated,latest,breaks=advance_feature_snapshot(symbol,old,state,raw,event_groups.get(symbol,{}),contract,ipo,signal,volume_audit=volume_audit)
                destination.execute('INSERT OR REPLACE INTO states VALUES (?,?,?)',(symbol,updated['last_date'],json.dumps(updated,allow_nan=False)))
                if not latest.empty:latest_rows.append(latest)
                if not breaks.empty:break_rows.append(breaks)
                proof['issuers']+=1;proof['raw_bars']+=len(raw);proof['reference_faults']+=int(breaks.reference_fault.sum()) if not breaks.empty else 0
                if proof['issuers']%500==0:
                    destination.commit();save();print(json.dumps({k:proof[k] for k in ['status','issuers','raw_bars','reference_faults']}),flush=True)
        destination.commit();destination.close();destination=None;seed_connection.close();seed_connection=None
        technical=pd.concat(latest_rows,ignore_index=True)
        if technical.symbol.duplicated().any() or set(technical.symbol)!=set(latest_raw.symbol):raise ValueError('latest feature rows do not cover the quoted A-share universe')
        data=Dataset(pd.DataFrame(),funds,contract.dates[contract.dates<=signal],meta)
        snap=public_snapshot(data,technical,signal);factors=default_library().compute(snap)
        if len(factors)<20:raise ValueError('fewer than 20 frozen-rule eligible candidates')
        universe=technical[['symbol','date','trading_symbol','listing_date','is_st','eligible','volume','quality_segment']].copy();universe['included_by_frozen_rules']=universe.symbol.isin(factors.index)
        factors.rename_axis('symbol').to_csv(target/'features.csv');universe.to_csv(target/'universe.csv',index=False)
        pd.DataFrame(volume_audit,columns=['symbol','date','tradestatus','raw_volume_missing','handling','ranking_eligible']).to_csv(target/'suspended_volume_audit.csv',index=False)
        proof['suspended_missing_volume_bars']=len(volume_audit)
        if break_rows:pd.concat(break_rows,ignore_index=True).to_csv(target/'quality_breaks.csv',index=False)
        pd.concat(outside,ignore_index=True).to_csv(target/'excluded_instruments.csv',index=False)
        pd.DataFrame({'date':contract.dates}).to_csv(target/'forecast_calendar.csv',index=False)
        for name,expected in source_hashes.items():
            if sha256(root/name)!=expected:raise ValueError('daily source changed during feature construction: '+name)
        if any(code_hash(name)!=expected for name,expected in code_hashes.items()):raise ValueError('inference implementation changed during construction')
        built=_utc_now()
        feature_receipt=dict(schema='frozen_daily_features_v1',signal_date=str(signal.date()),created_at_utc=built,quoted_ashares=len(universe),eligible=len(factors),reference_faults=proof['reference_faults'],factor_missing_counts={name:int(factors[name].isna().sum()) for name in factors},source_hashes=source_hashes,code_hashes=code_hashes,certified=False,model_ready=False,semantics='Frozen causal segments/native NaN; reference faults retained, not inferred away; no holdings valued.')
        (target/'feature_receipt.json').write_text(json.dumps(feature_receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        sources=[dict(path=(target/name).relative_to(root).as_posix(),sha256=sha256(target/name),observed_at_utc=built) for name in ['features.csv','universe.csv','feature_receipt.json','suspended_volume_audit.csv']]
        receipts=[]
        for horizon,(booster,model,_,_) in models.items():
            if list(factors.columns)!=model['features']:raise ValueError('frozen feature order differs')
            scores=snap[['trading_symbol','is_st','eligible']].copy();scores['score']=booster.predict(factors[model['features']]);scores['date']=signal
            scores=scores.rename_axis('symbol').reset_index().sort_values(['score','symbol'],ascending=[False,True]).reset_index(drop=True)
            scores.to_csv(target/f'scores_{horizon}.csv',index=False);scores.head(20).to_csv(target/f'top20_{horizon}.csv',index=False)
            receipt=record_predictions(target/'predictions.sqlite',project=root,seal_directory=path(config['seal']),horizon=horizon,signal_date=signal,scores=scores,expected_symbols=factors.index.tolist(),sources=sources,calendar=contract.dates,synthetic=False)
            saved,roundtrip=read_predictions(target/'predictions.sqlite',receipt['batch_id'])
            if roundtrip.symbol.tolist()!=scores.symbol.tolist():raise ValueError('saved score order differs')
            np.testing.assert_array_equal(roundtrip.score.to_numpy(),scores.score.to_numpy())
            receipts.append(saved)
        backup=backup_ledger(target/'predictions.sqlite',target/'prediction_backup.sqlite')
        proof.update(status='research_snapshot_saved',eligible=len(factors),quoted_ashares=len(universe),receipts=receipts,ledger_backup=backup,source_hashes=source_hashes,feature_receipt_sha256=sha256(target/'feature_receipt.json'),state_database_sha256=sha256(target/'states.sqlite'),completed_at_utc=_utc_now(),limitations=['Research baseline only; unresolved reference faults use original feature segmentation and native missing values.','Score is a ranking value, not an expected return.','Actual forecast timing is recorded; a backfill is never presented as a prospective paper forecast.','No portfolio tracking, return attribution, live daily service or factor optimization is verified by this run.'])
        save();print(json.dumps({k:proof[k] for k in ['status','signal_date','eligible','reference_faults']},ensure_ascii=False),flush=True)
        return proof
    except BaseException as exc:
        proof.update(status='failed',error=str(exc));save();raise
    finally:
        if destination is not None:destination.close()
        if seed_connection is not None:seed_connection.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',default='.');p.add_argument('--inputs',required=True);p.add_argument('--signal',required=True);p.add_argument('--out',required=True);p.add_argument('--exploratory',action='store_true')
    p.add_argument('--archive',help='append the completed snapshot to this project-relative prediction archive')
    args=vars(p.parse_args());archive=args.pop('archive');root=Path(args['project']).resolve()
    def local(value):
        relative=Path(value)
        if relative.is_absolute() or '..' in relative.parts or ':' in value:raise ValueError('portable project-relative daily archive paths required')
        result=(root/relative).resolve()
        if not result.is_relative_to(root):raise ValueError('daily archive path escapes project')
        return result
    if archive is not None:
        from .prediction_archive import archive_daily_snapshot,validate_archive_destination
        archive_file=local(archive);snapshot=local(args['out'])
        if archive_file.is_relative_to(snapshot) or snapshot.is_relative_to(archive_file):raise ValueError('daily archive must be separate from its snapshot')
        validate_archive_destination(archive_file)
    run_frozen_daily(**args)
    # Delivery failure must not rewrite a successfully completed inference run.
    if archive is not None:
        config=json.loads(local(args['inputs']).read_text(encoding='utf8'))
        result=archive_daily_snapshot(snapshot,sha256(snapshot/'manifest.json'),archive_file,root,local(config['seal']),pd.to_datetime(pd.read_csv(snapshot/'forecast_calendar.csv').date))
        print(json.dumps(dict(archive_import=result),ensure_ascii=False),flush=True)


if __name__=='__main__':main()

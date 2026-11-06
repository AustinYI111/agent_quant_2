"""Full eligible-pool control using bounded-memory quotes and disk order history."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json
import numpy as np
import pandas as pd
from .disk_market import QuoteStore,ReferenceAudit,code_hashes,iter_groups,write_manifest,exchange_dates
from .disk_account import simulate_disk_account,disk_account_metrics,DiskHeldValuationError
from .phase2_protocol import sha256,load_seal
from .valuation_overlay import reviewed_valuation_references
from .public_data import is_ashare
from .model import Config


def eligible_key_hashes(path,calendar,minimum_members=1,equal_weights=False):
    columns=['date','symbol','available_date']+(['target_weight'] if equal_weights else [])
    signatures={};cal=exchange_dates(calendar)
    for value,frame in iter_groups(path,'date',columns):
        date=exchange_dates([value])[0];available=pd.to_datetime(frame.available_date,format='mixed')
        if date not in cal or frame.symbol.isna().any() or frame.symbol.duplicated().any() or not frame.symbol.map(is_ashare).all() or available.isna().any() or available.dt.tz is not None or not available.eq(available.dt.normalize()).all() or available.gt(date).any():raise ValueError('original visible eligible keys required')
        if len(frame)<minimum_members:raise ValueError('complete eligible pool required')
        if equal_weights:
            weights=pd.to_numeric(frame.target_weight,errors='raise')
            if not np.isfinite(weights).all() or not np.allclose(weights,1/len(frame),rtol=0,atol=1e-15):raise ValueError('all original eligible names must have equal target weight')
        ordered=frame.assign(_available=available).sort_values('symbol');digest=hashlib.sha256()
        for s,a in zip(ordered.symbol,ordered._available):digest.update((s+'\t'+str(a.date())+'\n').encode())
        signatures[str(date.date())]=dict(rows=len(frame),sha256=digest.hexdigest())
    if not signatures:raise ValueError('nonempty eligibility schedule required')
    return signatures


def evaluate_streamed_equal(project,work_root,original_control,quote_store,reference_audit,ranking_accounts,dataset,experiment,seal_directory,reference_evidence,out):
    root=Path(project).resolve();work=Path(work_root).resolve()
    def under(base,name):
        p=Path(name)
        if p.is_absolute() or '..' in p.parts or ':' in str(p):raise ValueError('relative input/output paths required')
        p=(base/p).resolve()
        if not p.is_relative_to(base):raise ValueError('path escapes its explicit root')
        return p
    old,stored,reviewed,target=[under(work,n) for n in [original_control,quote_store,reference_audit,out]]
    source,data,seed,sealed=[under(root,n) for n in [ranking_accounts,dataset,experiment,seal_directory]]
    if target.exists() or any(target.is_relative_to(p) or p.is_relative_to(target) for p in [old,stored,reviewed,source,data,seed,sealed]):raise ValueError('fresh output outside immutable sources required')
    protocol,seal=load_seal(sealed,root);prior=json.loads((old/'manifest.json').read_text(encoding='utf8'))
    if prior.get('schema')!='eligible_equal_weight_control_v1' or prior.get('status')!='eligible_equal_weight_attempts_finished' or prior.get('protocol_sha256')!=seal['protocol_sha256'] or len(prior.get('accounts',[]))!=3 or {r['horizon'] for r in prior['accounts']}!=set(protocol['horizons']) or any(prior.get(k) is not False for k in ['independent_data_used','model_selection_performed','formal_model_promoted']):raise ValueError('terminal original sealed three-horizon control required')
    projects={};external={}
    def bind_project(p,digest=None):
        value=sha256(p)
        if digest is not None and value!=digest:raise ValueError('original research input changed: '+p.name)
        projects[p.relative_to(root).as_posix()]=value
    def bind_work(p,digest=None):
        value=sha256(p)
        if digest is not None and value!=digest:raise ValueError('control/cache input changed: '+p.name)
        external[p.relative_to(work).as_posix()]=value
    bind_work(old/'manifest.json')
    for name,digest in prior['source_hashes'].items():bind_project(under(root,name),digest)
    if code_hashes(list(prior['code_hashes']))!=prior['code_hashes']:raise ValueError('original control code changed')
    for name,digest in prior['output_hashes'].items():bind_work(under(old,name),digest)
    for p in [source/'manifest.json',data/'metadata.json',data/'calendar.csv',data/'event_raw_prices.csv',seed/'factor_samples.csv']:
        bind_project(p,prior['source_hashes'].get(p.relative_to(root).as_posix(),'missing'))
    family=json.loads((source/'manifest.json').read_text(encoding='utf8'));metadata=json.loads((data/'metadata.json').read_text(encoding='utf8'))
    if family.get('status')!='full_family_ranking_accounts_evaluated_selection_pending' or len(family.get('accounts',[]))!=30 or metadata.get('synthetic') is not False:raise ValueError('original complete real research family required')
    calendar=exchange_dates(pd.read_csv(data/'calendar.csv').date)
    if calendar.max()>pd.Timestamp(protocol['research_end']):raise ValueError('independent sessions cannot enter research control')
    originals=eligible_key_hashes(seed/'factor_samples.csv',calendar)
    store=QuoteStore(stored);reference=None
    try:
        reference=ReferenceAudit(reviewed,store)
        if not store.calendar.equals(calendar) or store.manifest['raw_sha256']!=sha256(data/'event_raw_prices.csv'):raise ValueError('cache differs from original quotes/calendar')
        actions=pd.read_csv(old/'effective_valuation_actions.csv',float_precision='round_trip',low_memory=False)
        for n in ['ex_date','available_date','pay_date']:actions[n]=pd.to_datetime(actions[n],format='mixed')
        effective,overlay=reviewed_valuation_references(actions,reference_evidence,root,calendar,protocol['research_end'])
        pd.testing.assert_frame_equal(effective.reset_index(drop=True),reference.actions.reset_index(drop=True),check_dtype=False,check_exact=True)
        for name,digest in overlay['source_hashes'].items():bind_project(under(root,name),digest)
        for folder,m,names in [(stored,store.manifest,['manifest.json','calendar.csv','quotes.sqlite']),(reviewed,reference.manifest,['manifest.json','actions.csv','faults.sqlite'])]:
            for name in names:bind_work(folder/name)
        # Baseline index identity is part of the already verified original run.
        benchmark_keys=[]
        for n in prior['source_hashes']:
            summary=Path(n).with_name('summary.json').as_posix()
            if n.endswith('/raw_prices.csv') and summary in prior['source_hashes'] and json.loads(under(root,summary).read_text(encoding='utf8')).get('return_kind')=='price_index':benchmark_keys.append(n)
        if len(benchmark_keys)!=1:raise ValueError('unambiguous original price index required')
        benchmark=pd.read_csv(under(root,benchmark_keys[0]),parse_dates=['date'],float_precision='round_trip')
        if 'code' not in benchmark or not benchmark.code.eq('sh.000300').all():raise ValueError('original CSI300 price index required')
        names=['streamed_equal_weight.py','disk_account.py','disk_market.py','causal_quality.py','price_action_audit.py','price_reference.py','costs.py','valuation_overlay.py','reference_overrides.py','phase2_protocol.py'];codes=code_hashes(names)
        target.mkdir(parents=True);proof=dict(schema='bounded_eligible_equal_weight_control_v1',status='running',started_at_utc=datetime.now(timezone.utc).isoformat(),protocol_sha256=seal['protocol_sha256'],project_source_hashes=projects,work_source_hashes=external,code_hashes=codes,accounts=[],reference_overlay=overlay,actual_holder_terms_changed=False,independent_data_used=False,model_selection_performed=False,formal_model_promoted=False,certified=False,definition='Identical original eligible keys, equal weights, matched complete horizon endpoints, original raw-share costs/entitlements; per-session reads and disk order sink.',limitations=['Computational change only; not a new strategy or investor-fill certification.','Unknown positive holdings stop without full-period metrics.','Original historical vintages, executable limit states, rights and terminal settlements remain separately subject to evidence.'])
        write_manifest(target/'manifest.json',proof)
        try:
            for h in protocol['horizons']:
                original=next(r for r in prior['accounts'] if r['horizon']==h);picks=old/h/'scheduled_eligible_pool.csv'
                hashes=eligible_key_hashes(picks,calendar,protocol['execution']['top_k'],True)
                for date,signature in hashes.items():
                    if originals.get(date)!=signature:raise ValueError('streaming control changed original eligible membership or availability')
                dates=pd.DatetimeIndex(pd.to_datetime(list(hashes)));last=pd.Timestamp(original['requested_end'])
                record=dict(horizon=h,first_signal=str(dates[0].date()),requested_end=str(last.date()),scheduled_rebalances=len(dates),eligible_date_hashes=hashes)
                for trees in [30,60]:
                    folder=source/h/str(trees)/'baseline'
                    for name in ['scheduled_top20.csv','daily_nav.csv']:bind_project(folder/name,family['output_hashes'][(folder/name).relative_to(source).as_posix()])
                    base_dates=pd.DatetimeIndex(pd.read_csv(folder/'scheduled_top20.csv',usecols=['date'],parse_dates=['date']).date.unique());base=next(r for r in family['accounts'] if (r['horizon'],r['n_estimators'],r['group'])==(h,trees,'baseline'))
                    if not base_dates.equals(dates) or pd.Timestamp(base['requested_end'])!=last or base['status']!='complete_calendar_mature_ranking_account':raise ValueError('control must match both original baseline schedules and full endpoints')
                folder=target/h
                print(json.dumps(dict(stage='streamed_equal_account',horizon=h,requested_end=str(last.date()))),flush=True)
                try:
                    result=simulate_disk_account(store,reference,picks,calendar[calendar<=last],Config(top_k=max(v['rows'] for v in hashes.values()),commission_bps=3.,slippage_bps=5.),folder)
                except DiskHeldValuationError as e:
                    for before,after in [('daily_nav.csv','partial_valid_nav.csv'),('orders.csv','partial_orders.csv'),('daily_execution_totals.csv','partial_execution_totals.csv')]:
                        (folder/before).rename(folder/after)
                    record.update(status='incomplete_held_valuation',symbol=e.symbol,date=str(e.date.date()),reason=e.reason,holdings=e.holdings,valid_sessions=e.nav_rows,orders=e.order_rows,partial_prefix_only=True)
                    e.__traceback__=None
                else:
                    nav=pd.read_csv(folder/'daily_nav.csv',usecols=['date'],parse_dates=['date'])
                    for trees in [30,60]:
                        expected=pd.read_csv(source/h/str(trees)/'baseline/daily_nav.csv',usecols=['date'],parse_dates=['date'])
                        if not nav.date.equals(expected.date):raise ValueError('full valuation calendars differ')
                    daily,metrics=disk_account_metrics(folder,benchmark);daily.to_csv(folder/'index_comparison.csv',index=False)
                    record.update(status='complete_calendar_mature_equal_weight_control',execution=result,metrics=metrics)
                (folder/'summary.json').write_text(json.dumps(record,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8');proof['accounts'].append(record);write_manifest(target/'manifest.json',proof);print(json.dumps({k:record[k] for k in ['horizon','status']}),flush=True)
            for name,digest in projects.items():
                if sha256(under(root,name))!=digest:raise ValueError('project source changed during execution')
            for name,digest in external.items():
                if sha256(under(work,name))!=digest:raise ValueError('work source changed during execution')
            if code_hashes(names)!=codes:raise ValueError('streamed account code changed during execution')
            proof.update(status='streamed_equal_weight_attempts_finished',completed_at_utc=datetime.now(timezone.utc).isoformat(),output_hashes={p.relative_to(target).as_posix():sha256(p) for p in target.rglob('*') if p.is_file() and p.name!='manifest.json'});write_manifest(target/'manifest.json',proof);return proof
        except BaseException as e:proof.update(status='failed',error=str(e));write_manifest(target/'manifest.json',proof);raise
    finally:
        if reference is not None:reference.close()
        store.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['project','work-root','original-control','quote-store','reference-audit','ranking-accounts','dataset','experiment','seal-directory','reference-evidence','out']:p.add_argument('--'+name,required=True)
    evaluate_streamed_equal(**vars(p.parse_args()))

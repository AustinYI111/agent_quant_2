"""Prespecified eligible-pool raw-share control; never discard unknown holdings."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json
import numpy as np
import pandas as pd
from .public_data import is_ashare
from .phase2_protocol import sha256,load_seal
from .candidate_portfolios import account_quotes
from .data import Dataset
from .model import Config
from .pipeline import simulate_monthly
from .causal_quality import HeldValuationError
from .horizon_research import compare_index


def equal_weight_picks(samples,signals,calendar,research_end,minimum_members=20):
    """Use all original eligible keys, irrespective of factors, labels or scores."""
    if not {'date','symbol','available_date'}.issubset(samples):raise ValueError('original eligible sample keys and availability required')
    cal=pd.DatetimeIndex(pd.to_datetime(calendar));dates=pd.DatetimeIndex(pd.to_datetime(signals));end=pd.Timestamp(research_end)
    for index in [cal,dates]:
        if index.empty or index.hasnans or index.has_duplicates or not index.is_monotonic_increasing or index.tz is not None or not index.equals(index.normalize()):raise ValueError('ordered unique normalized exchange dates required')
    if not dates.isin(cal).all() or cal.max()>end or dates.max()>end:raise ValueError('independent or non-session dates cannot enter control')
    if isinstance(minimum_members,bool) or not isinstance(minimum_members,int) or minimum_members<1:raise ValueError('positive minimum membership required')
    pool=samples[['date','symbol','available_date']].copy()
    pool['date']=pd.to_datetime(pool.date,format='mixed');pool['available_date']=pd.to_datetime(pool.available_date,format='mixed')
    if pool[['date','symbol','available_date']].isna().any().any() or pool.duplicated(['date','symbol']).any() or not pool.date.isin(cal).all() or pool.date.gt(end).any() or pool.available_date.gt(pool.date).any() or not pool.symbol.map(is_ashare).all():raise ValueError('unique visible original A-share eligibility required')
    selected=pool[pool.date.isin(dates)].sort_values(['date','symbol']).reset_index(drop=True)
    sizes=selected.groupby('date').size()
    if not pd.DatetimeIndex(sizes.index).equals(dates) or sizes.lt(minimum_members).any():raise ValueError('complete eligible pool required on every fixed decision date')
    selected['target_weight']=1/selected.groupby('date').symbol.transform('size')
    return selected


def evaluation_windows(calendar,first,last):
    """Expand computation only; never shorten the requested holding period."""
    dates=pd.DatetimeIndex(calendar);dates=dates[(dates>=pd.Timestamp(first))&(dates<=pd.Timestamp(last))]
    if dates.empty or dates[-1]!=pd.Timestamp(last):raise ValueError('full requested control endpoint must be a session')
    size=63;ends=[]
    while size<len(dates):ends.append(dates[size-1]);size*=2
    return ends+[dates[-1]]


def evaluate_equal_weight(project,accounts,dataset,experiment,benchmark,seal_directory,out,output_root=None,reference_evidence=None):
    root=Path(project).resolve()
    def local(value):
        p=Path(value)
        if p.is_absolute() or '..' in p.parts or ':' in str(p):raise ValueError('project-relative control paths required')
        p=(root/p).resolve()
        if not p.is_relative_to(root):raise ValueError('control path escapes project')
        return p
    source,data,old,index,sealed=map(local,[accounts,dataset,experiment,benchmark,seal_directory])
    if output_root is None:target=local(out)
    else:
        relative=Path(out)
        if relative.is_absolute() or '..' in relative.parts or ':' in str(relative):raise ValueError('output must be relative to the explicit output root')
        base=Path(output_root).resolve();target=(base/relative).resolve()
        if not target.is_relative_to(base):raise ValueError('control output escapes output root')
    if target.exists() or any(target.is_relative_to(p) or p.is_relative_to(target) for p in [source,data,old,index,sealed]):raise ValueError('fresh output outside sealed inputs required')
    family=json.loads((source/'manifest.json').read_text(encoding='utf8'));protocol,seal=load_seal(sealed,root)
    if family.get('status')!='full_family_ranking_accounts_evaluated_selection_pending' or len(family.get('accounts',[]))!=30 or family.get('protocol_sha256')!=seal['protocol_sha256'] or any(family.get(k) is not False for k in ['independent_data_used','model_selection_performed','formal_model_promoted']):raise ValueError('sealed terminal fixed research family required')
    expected={(h,t,g) for h in protocol['horizons'] for t in [30,60] for g in ['baseline','market_state','oneil_box','industry','combined']}
    if {(r['horizon'],r['n_estimators'],r['group']) for r in family['accounts']}!=expected:raise ValueError('all thirty distinct research accounts required')
    sources={}
    def bind(p,digest=None):
        actual=sha256(p)
        if digest is not None and actual!=digest:raise ValueError('control input changed: '+p.name)
        sources[p.relative_to(root).as_posix()]=actual
    bind(source/'manifest.json')
    for name,digest in family['source_hashes'].items():bind(local(name),digest)
    for name,digest in family['code_hashes'].items():
        if hashlib.sha256((root/'ashare_mvp'/name).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest()!=digest:raise ValueError('ranking producer code changed')
    summary=json.loads((old/'models/summary.json').read_text(encoding='utf8'))
    bind(old/'models/summary.json',family['source_hashes'].get((old/'models/summary.json').relative_to(root).as_posix(),'missing'))
    bind(old/'factor_samples.csv',summary['input_frame_sha256']['samples'])
    if family['source_hashes'].get((old/'factor_samples.csv').relative_to(root).as_posix())!=summary['input_frame_sha256']['samples']:raise ValueError('identical original eligible sample keys required')
    metadata=json.loads((data/'metadata.json').read_text(encoding='utf8'));bind(data/'metadata.json')
    if metadata.get('synthetic') is not False or metadata.get('quality_policy',{}).get('schema')!='causal_quality_research_v1':raise ValueError('real causal-quality original source required')
    for name in ['calendar.csv','event_raw_prices.csv']:bind(data/name,metadata['converted_hashes'][name])
    for name in ['calendar.csv','event_raw_prices.csv']:
        if family['source_hashes'].get((data/name).relative_to(root).as_posix())!=sha256(data/name):raise ValueError('identical ranking quotes and calendar required')
    bind(source/'effective_valuation_actions.csv',family['output_hashes']['effective_valuation_actions.csv'])
    benchmark_meta=json.loads((index/'summary.json').read_text(encoding='utf8'))
    if benchmark_meta.get('return_kind')!='price_index':raise ValueError('explicit price-index benchmark required')
    bind(index/'summary.json');bind(index/'raw_prices.csv',benchmark_meta['raw_sha256'])
    if family['source_hashes'].get((index/'raw_prices.csv').relative_to(root).as_posix())!=sha256(index/'raw_prices.csv'):raise ValueError('identical ranking price benchmark required')
    calendar=pd.DatetimeIndex(pd.to_datetime(pd.read_csv(data/'calendar.csv').date))
    samples=pd.read_csv(old/'factor_samples.csv',usecols=['date','symbol','available_date'],dtype={'symbol':str},parse_dates=['date','available_date'])
    actions=pd.read_csv(source/'effective_valuation_actions.csv',float_precision='round_trip',low_memory=False)
    for name in ['ex_date','available_date','pay_date']:actions[name]=pd.to_datetime(actions[name],format='mixed')
    overlay=None
    if reference_evidence is not None:
        from .valuation_overlay import reviewed_valuation_references
        actions,overlay=reviewed_valuation_references(actions,reference_evidence,root,calendar,protocol['research_end'])
        for name,digest in overlay['source_hashes'].items():bind(local(name),digest)
    benchmark_frame=pd.read_csv(index/'raw_prices.csv',parse_dates=['date'],float_precision='round_trip')
    code_names=['eligible_equal_weight.py','candidate_portfolios.py','pipeline.py','corporate_actions.py','causal_quality.py','costs.py','public_data.py','valuation_overlay.py','reference_overrides.py','phase2_protocol.py']
    def codes():return {n:hashlib.sha256((root/'ashare_mvp'/n).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest() for n in code_names}
    initial_codes=codes();target.mkdir(parents=True)
    actions.to_csv(target/'effective_valuation_actions.csv',index=False)
    proof=dict(schema='eligible_equal_weight_control_v1',status='running',started_at_utc=datetime.now(timezone.utc).isoformat(),protocol_sha256=seal['protocol_sha256'],source_hashes=sources,code_hashes=initial_codes,accounts=[],independent_data_used=False,model_selection_performed=False,formal_model_promoted=False,certified=False,definition='All original eligible keys at the matched ranking decision dates; equal target weight; same raw shares, gross dividend entitlement, dated costs and no final liquidation.',limitations=['Not a Top20 strategy or investor-fill certification.','Blocked old holdings remain; unknown positive holdings stop without full-period metrics.','Control comparisons are descriptive, not additional candidate-family significance tests.'])
    proof['reference_overlay']=overlay
    proof['actual_holder_terms_changed']=False
    def save():
        temporary=target/'manifest.next.json';temporary.write_text(json.dumps(proof,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8');temporary.replace(target/'manifest.json')
    save()
    try:
        for horizon in protocol['horizons']:
            records=[r for r in family['accounts'] if r['horizon']==horizon and r['group']=='baseline']
            if len(records)!=2 or any(r['status']!='complete_calendar_mature_ranking_account' for r in records):raise ValueError('both complete matched baselines required')
            schedules=[]
            for trees in [30,60]:
                folder=source/horizon/str(trees)/'baseline'
                for name in ['scheduled_top20.csv','daily_nav.csv']:
                    p=folder/name;bind(p,family['output_hashes'][p.relative_to(source).as_posix()])
                schedules.append(pd.DatetimeIndex(pd.read_csv(folder/'scheduled_top20.csv',usecols=['date'],parse_dates=['date']).date.unique()))
            if not schedules[0].equals(schedules[1]) or records[0]['requested_end']!=records[1]['requested_end']:raise ValueError('tree variants must have identical control schedules')
            picks=equal_weight_picks(samples,schedules[0],calendar,protocol['research_end'],protocol['execution']['top_k'])
            first=schedules[0][0];last=pd.Timestamp(records[0]['requested_end']);symbols=set(picks.symbol)
            folder=target/horizon;folder.mkdir();picks.to_csv(folder/'scheduled_eligible_pool.csv',index=False)
            print(json.dumps(dict(stage='loading_equal_pool_quotes',horizon=horizon,symbols=len(symbols),rebalance_dates=len(schedules[0]))),flush=True)
            record=dict(horizon=horizon,scheduled_rebalances=len(schedules[0]),first_signal=str(first.date()),requested_end=str(last.date()),symbols=len(symbols),minimum_pool_size=int(picks.groupby('date').size().min()),maximum_pool_size=int(picks.groupby('date').size().max()),evaluation_windows=[],costs=dict(commission_bps=3.,slippage_bps=5.,sell_stamp_duty='dated existing schedule'))
            for endpoint in evaluation_windows(calendar,first,last):
                raw=account_quotes(data/'event_raw_prices.csv',symbols,first,endpoint)
                tradable=raw.tradestatus.eq('1')&raw.open.gt(0);prices=raw[['date','symbol']].copy()
                prices['total_return_open']=raw.open.where(tradable,raw.preclose);prices['can_buy']=tradable;prices['can_sell']=tradable
                events=actions[actions.symbol.isin(symbols)&actions.ex_date.between(first,endpoint)]
                dates=calendar[calendar<=endpoint];book=Dataset(prices,pd.DataFrame(),dates,metadata)
                record['evaluation_windows'].append(dict(end=str(endpoint.date()),rows=len(raw),requested_full_endpoint=bool(endpoint==last)))
                print(json.dumps(dict(stage='equal_pool_window',horizon=horizon,end=str(endpoint.date()),rows=len(raw))),flush=True)
                try:
                    nav,orders=simulate_monthly(book,picks,Config(top_k=int(picks.groupby('date').size().max()),commission_bps=3.,slippage_bps=5.),raw,events,.01,causal_quality_research=True)
                except HeldValuationError as error:
                    error.nav.to_csv(folder/'partial_valid_nav.csv',index=False);error.trades.to_csv(folder/'partial_orders.csv',index=False)
                    record.update(status='incomplete_held_valuation',symbol=error.symbol,date=str(error.date.date()),reason=error.reason,holdings=error.holdings)
                    error.__traceback__=None;break
                else:
                    if endpoint<last:
                        del raw,prices,events,book,nav,orders
                        continue
                    for trees in [30,60]:
                        baseline=pd.read_csv(source/horizon/str(trees)/'baseline/daily_nav.csv',parse_dates=['date'],float_precision='round_trip')
                        if not pd.DatetimeIndex(nav.date).equals(pd.DatetimeIndex(baseline.date)):raise ValueError('equal-weight and ranking sessions differ')
                    daily,metrics=compare_index(nav,orders,benchmark_frame,'price_index')
                    nav.to_csv(folder/'daily_nav.csv',index=False);orders.to_csv(folder/'orders.csv',index=False);daily.to_csv(folder/'index_comparison.csv',index=False)
                    record.update(status='complete_calendar_mature_equal_weight_control',metrics=metrics)
            (folder/'summary.json').write_text(json.dumps(record,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
            proof['accounts'].append(record);save();print(json.dumps(dict(horizon=horizon,status=record['status'])),flush=True)
            del raw,prices,events,book,picks
        for name,digest in sources.items():
            if sha256(root/name)!=digest:raise ValueError('control source changed during evaluation')
        if codes()!=initial_codes:raise ValueError('control code changed during evaluation')
        proof.update(status='eligible_equal_weight_attempts_finished',completed_at_utc=datetime.now(timezone.utc).isoformat(),output_hashes={p.relative_to(target).as_posix():sha256(p) for p in target.rglob('*.csv')})
        save();return proof
    except BaseException as error:
        proof.update(status='failed',error=str(error),failed_at_utc=datetime.now(timezone.utc).isoformat());save();raise



if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['project','accounts','dataset','experiment','benchmark','seal-directory','out']:p.add_argument('--'+name,required=True)
    p.add_argument('--output-root',help='explicit separate drive/directory for control outputs; input paths stay project-relative')
    p.add_argument('--reference-evidence',help='additional visually reviewed primary-notice references; holder terms cannot change')
    evaluate_equal_weight(**vars(p.parse_args()))

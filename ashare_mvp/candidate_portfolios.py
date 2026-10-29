"""Research-only continuous ranking accounts for fixed-fold candidate models.

No fold-end liquidation, no shortened 12-month hold, no automatic selection.
Timing variants, industry and equal-weight control are separate pending work.
"""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json
import numpy as np
import pandas as pd
from .phase2_protocol import sha256,load_seal
from .horizon_labels import HORIZONS,label_interval
from .horizon_research import rebalance_signals,compare_index
from .data import Dataset
from .model import Config
from .pipeline import simulate_monthly
from .causal_quality import HeldValuationError


def mature_schedule(signal_dates,calendar,horizon):
    """Use only calendar-mature holding cycles, regardless of stock outcomes."""
    scheduled=rebalance_signals(signal_dates,calendar,horizon)
    pairs=[(date,*label_interval(calendar,date,HORIZONS[horizon])) for date in scheduled]
    complete=[(date,entry,end) for date,entry,end in pairs if pd.notna(entry) and pd.notna(end)]
    if not complete:raise ValueError('no complete calendar-mature holding cycle')
    return pd.DatetimeIndex([x[0] for x in complete]),complete[-1][2]


def account_quotes(path,symbols,first,last):
    """Explicit terminal-date dtype avoids chunk-inference failures on sparse dates."""
    parts=[]
    for chunk in pd.read_csv(path,usecols=['date','symbol','open','close','preclose','tradestatus','terminal_date'],
            dtype={'symbol':str,'tradestatus':str,'terminal_date':'string'},parse_dates=['date'],
            float_precision='round_trip',low_memory=False,chunksize=200000):
        subset=chunk[chunk.symbol.isin(symbols)&chunk.date.between(first,last)]
        if not subset.empty:parts.append(subset)
    if not parts:raise ValueError('no requested raw account quotes')
    raw=pd.concat(parts,ignore_index=True)
    raw['terminal_date']=pd.to_datetime(raw.terminal_date,format='mixed')
    return raw


def paired_net_returns(candidate,baseline,block=63,repetitions=2000,seed=42):
    """Prespecified calendar blocks on paired daily net returns, initial fee included."""
    for frame in [candidate,baseline]:
        if frame.empty or frame.date.duplicated().any() or not frame.date.is_monotonic_increasing or not np.isfinite(frame.nav).all() or frame.nav.le(0).any():
            raise ValueError('positive complete ordered NAV required for paired inference')
    if not pd.DatetimeIndex(candidate.date).equals(pd.DatetimeIndex(baseline.date)):
        raise ValueError('paired accounts must cover identical sessions; no survivor-prefix truncation')
    a=candidate.nav.to_numpy(float);b=baseline.nav.to_numpy(float)
    excess=a/np.r_[1.,a[:-1]]-b/np.r_[1.,b[:-1]]
    result=dict(sessions=len(excess),net_total_return_difference=float(a[-1]-b[-1]),mean_daily_net_return_difference=float(excess.mean()),
        block_sessions=block,repetitions=repetitions,seed=seed,family_comparisons=24,familywise_alpha=.05,
        interpretation='paired research diagnostic; no selection or promotion; market/industry exposure is not causal alpha')
    if len(excess)<2*block:
        result['uncertainty_status']='insufficient_calendar_history';return excess,result
    rng=np.random.default_rng(seed);draws=np.empty(repetitions);blocks=int(np.ceil(len(excess)/block))
    for i in range(repetitions):
        starts=rng.integers(0,len(excess),size=blocks)
        indices=((starts[:,None]+np.arange(block))%len(excess)).ravel()[:len(excess)]
        draws[i]=excess[indices].mean()
    alpha=.05/24
    result.update(uncertainty_status='descriptive_circular_calendar_block_bootstrap',
        family_adjusted_mean_daily_interval=np.quantile(draws,[alpha/2,1-alpha/2]).tolist(),
        tail_resolution='2000 fixed repetitions; extreme adjusted tails have limited Monte Carlo resolution')
    return excess,result


def evaluate_candidate_rankings(project,training,dataset,replay,benchmark,seal_directory,out,reference_evidence=None,missing_payment_evidence=None):
    root=Path(project).resolve()
    def local(value):
        p=Path(value)
        if p.is_absolute() or '..' in p.parts or ':' in str(p):raise ValueError('project-relative evaluation paths required')
        result=(root/p).resolve()
        if not result.is_relative_to(root):raise ValueError('evaluation path escapes project')
        return result
    trained,data,original,index,sealed,target=map(local,[training,dataset,replay,benchmark,seal_directory,out])
    if target.exists():raise ValueError('new candidate account output required')
    if any(target.is_relative_to(p) or p.is_relative_to(target) for p in [trained,data,original,index,sealed]):raise ValueError('evaluation output overlaps inputs')
    protocol,seal=load_seal(sealed,root)
    fitting=json.loads((trained/'manifest.json').read_text(encoding='utf8'))
    if fitting.get('status')!='candidate_fits_complete_selection_pending' or fitting.get('independent_data_used') is not False or fitting.get('model_selection_performed') is not False or fitting.get('protocol_sha256')!=seal['protocol_sha256']:
        raise ValueError('completed sealed research-only candidate fits required')
    sources={}
    def bind(path,expected=None):
        digest=sha256(path)
        if expected is not None and digest!=expected:raise ValueError('candidate account input changed: '+path.name)
        sources[path.relative_to(root).as_posix()]=digest
    bind(trained/'manifest.json')
    meta=json.loads((data/'metadata.json').read_text(encoding='utf8'))
    bind(data/'metadata.json',fitting['source_hashes'].get((data/'metadata.json').relative_to(root).as_posix(),'missing'))
    for name in ['calendar.csv','event_raw_prices.csv']:bind(data/name,meta['converted_hashes'][name])
    if meta.get('synthetic') is not False or meta.get('quality_policy',{}).get('schema')!='causal_quality_research_v1':raise ValueError('real causal research source required')
    audit=json.loads((original/'valuation_replay_audit.json').read_text(encoding='utf8'))
    if audit['status']!='completed_all_three_portfolios' or audit['dataset_metadata_sha256']!=sha256(data/'metadata.json') or audit['models_retrained'] or audit['actual_economic_terms_changed']:
        raise ValueError('unchanged reviewed valuation source required')
    bind(original/'valuation_replay_audit.json');bind(original/'effective_valuation_actions.csv',audit['effective_actions_sha256'])
    bmeta=json.loads((index/'summary.json').read_text(encoding='utf8'))
    if bmeta.get('return_kind')!='price_index':raise ValueError('explicit price benchmark required')
    bind(index/'summary.json');bind(index/'raw_prices.csv',bmeta['raw_sha256'])
    calendar=pd.DatetimeIndex(pd.to_datetime(pd.read_csv(data/'calendar.csv').date))
    if calendar.max()>pd.Timestamp(protocol['research_end']):raise ValueError('independent prices cannot enter research selection')
    benchmark_frame=pd.read_csv(index/'raw_prices.csv',parse_dates=['date'],float_precision='round_trip')
    actions=pd.read_csv(original/'effective_valuation_actions.csv',float_precision='round_trip',low_memory=False)
    for column in ['ex_date','available_date','pay_date']:actions[column]=pd.to_datetime(actions[column],format='mixed')
    payment_supplement=None
    if missing_payment_evidence is not None:
        from .valuation_payments import supplement_missing_valuation_cash
        actions,payment_supplement=supplement_missing_valuation_cash((original/'effective_valuation_actions.csv').relative_to(root).as_posix(),missing_payment_evidence,root,calendar,protocol['research_end'])
        for name,digest in payment_supplement['source_hashes'].items():bind(local(name),digest)
    overlay=None
    if reference_evidence is not None:
        from .valuation_overlay import reviewed_valuation_references
        actions,overlay=reviewed_valuation_references(actions,reference_evidence,root,calendar,protocol['research_end'])
        for name,digest in overlay['source_hashes'].items():bind(local(name),digest)
    groups=['baseline','market_state','oneil_box'];validation=protocol['validation']
    expected={(h,g,t,s) for h in protocol['horizons'] for g in groups for t in validation['candidate_estimators'] for s,e in validation['research_validation_windows']}
    actual=[(r['horizon'],r['group'],r['n_estimators'],r['validation_start']) for r in fitting['models']]
    if len(actual)!=len(set(actual)) or set(actual)!=expected:raise ValueError('complete matched fixed-fold family required')
    for record in fitting['models']:
        model=local((trained/record['relative_model_path']).relative_to(root).as_posix())
        bind(model,record['model_sha256']);bind(model.with_name('metadata.json'),record['metadata_sha256']);bind(model.with_name('rankings.csv'),record['rankings_sha256'])
    target.mkdir(parents=True)
    code_names=['candidate_portfolios.py','pipeline.py','corporate_actions.py','causal_quality.py','price_action_audit.py','price_reference.py','horizon_research.py','horizon_labels.py','costs.py','valuation_overlay.py','reference_overrides.py','valuation_payments.py']
    code_hashes={n:hashlib.sha256((root/'ashare_mvp'/n).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest() for n in code_names}
    proof=dict(schema='phase2_candidate_ranking_accounts_v1',status='evaluating',started_at_utc=datetime.now(timezone.utc).isoformat(),
        protocol_sha256=seal['protocol_sha256'],source_hashes=sources,code_hashes=code_hashes,independent_data_used=False,
        model_selection_performed=False,formal_model_promoted=False,certified=False,accounts=[],paired_diagnostics=[],reference_overlay=overlay,payment_supplement=payment_supplement,
        design='continuous research accounts across fixed refit windows; only full calendar-mature cycles; no fold reset or final liquidation',
        pending=['industry historical evidence','industry and combined candidate fits','timing accounts','eligible equal-weight control','final selection review','independent mature validation'],
        limitations=['original source vintages and executable limit states unverified','fractional shares and gross dividend entitlements','CSI300 price index excludes dividends'])
    def save():(target/'manifest.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
    actions.to_csv(target/'effective_valuation_actions.csv',index=False)
    save()
    try:
        for horizon in protocol['horizons']:
            for trees in validation['candidate_estimators']:
                paired={};reference_schedule=None
                for group in groups:
                    records=[r for r in fitting['models'] if r['horizon']==horizon and r['group']==group and r['n_estimators']==trees]
                    pieces=[]
                    for record in records:
                        model=trained/record['relative_model_path']
                        r=pd.read_csv(model.with_name('rankings.csv'),parse_dates=['date','available_date'],float_precision='round_trip')
                        if len(r)!=record['prediction_rows'] or not r.date.between(record['validation_start'],record['validation_end']).all() or r.available_date.gt(r.date).any() or not r.horizon.eq(horizon).all():raise ValueError('research rankings differ from fold contract')
                        pieces.append(r)
                    rankings=pd.concat(pieces,ignore_index=True).sort_values(['date','rank'])
                    if rankings.duplicated(['date','symbol']).any() or not np.isfinite(rankings.score).all():raise ValueError('ambiguous candidate rankings')
                    signals,last=mature_schedule(rankings.date.unique(),calendar,horizon)
                    signature=(tuple(signals),last)
                    if reference_schedule is not None and signature!=reference_schedule:raise ValueError('candidate/control calendar schedules differ')
                    reference_schedule=signature
                    picks=rankings[rankings.date.isin(signals)].sort_values(['date','score','symbol'],ascending=[True,False,True]).groupby('date',sort=True,observed=True).head(protocol['execution']['top_k']).copy()
                    if not picks.groupby('date',observed=True).size().eq(protocol['execution']['top_k']).all():raise ValueError('full Top20 required at every rebalance')
                    picks['target_weight']=1/protocol['execution']['top_k']
                    symbols=set(picks.symbol);first=signals[0]
                    raw=account_quotes(data/'event_raw_prices.csv',symbols,first,last)
                    prices=raw[['date','symbol']].copy();tradable=raw.tradestatus.eq('1')&raw.open.gt(0)
                    prices['can_buy']=tradable;prices['can_sell']=tradable;prices['total_return_open']=raw.open.where(tradable,raw.preclose)
                    dates=calendar[calendar<=last]
                    events=actions[actions.symbol.isin(symbols)&actions.ex_date.between(first,last)].copy()
                    book=Dataset(prices,pd.DataFrame(),dates,meta)
                    folder=target/horizon/str(trees)/group;folder.mkdir(parents=True)
                    picks.to_csv(folder/'scheduled_top20.csv',index=False)
                    record=dict(horizon=horizon,n_estimators=trees,group=group,scheduled_rebalances=len(signals),first_signal=str(first.date()),requested_end=str(last.date()),
                        raw_symbols=int(raw.symbol.nunique()),costs=dict(commission_bps=3.,slippage_bps=5.,sell_stamp_duty='dated existing schedule'))
                    try:
                        nav,orders=simulate_monthly(book,picks,Config(top_k=20,commission_bps=3.,slippage_bps=5.),raw,events,reference_tick_size=.01,causal_quality_research=True)
                    except HeldValuationError as error:
                        error.nav.to_csv(folder/'partial_valid_nav.csv',index=False);error.trades.to_csv(folder/'partial_orders.csv',index=False)
                        record.update(status='incomplete_held_valuation',symbol=error.symbol,date=str(error.date.date()),reason=error.reason,holdings=error.holdings,
                            limitation='no full-period performance or paired comparison from valid prefix')
                        error.__traceback__=None
                    else:
                        expected_dates=dates[dates>=label_interval(calendar,first,HORIZONS[horizon])[0]]
                        if not pd.DatetimeIndex(nav.date).equals(expected_dates):raise ValueError('candidate account missing requested valuation sessions')
                        daily,metrics=compare_index(nav,orders,benchmark_frame,'price_index')
                        nav.to_csv(folder/'daily_nav.csv',index=False);orders.to_csv(folder/'orders.csv',index=False);daily.to_csv(folder/'index_comparison.csv',index=False)
                        record.update(status='complete_calendar_mature_ranking_account',metrics=metrics)
                        paired[group]=nav
                    (folder/'summary.json').write_text(json.dumps(record,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
                    proof['accounts'].append(record);save();print(json.dumps({k:record[k] for k in ['horizon','n_estimators','group','status']}),flush=True)
                    del raw,prices,events,book,rankings,pieces,picks
                for group in groups[1:]:
                    if group in paired and 'baseline' in paired:
                        excess,diagnostic=paired_net_returns(paired[group],paired['baseline'],seed=validation['seed'])
                        diagnostic.update(horizon=horizon,group=group,n_estimators=trees)
                        pd.DataFrame(dict(date=paired[group].date,paired_net_return_difference=excess)).to_csv(target/horizon/str(trees)/group/'paired_daily.csv',index=False)
                    else:diagnostic=dict(horizon=horizon,group=group,n_estimators=trees,status='incomplete_account_pair_no_inference')
                    proof['paired_diagnostics'].append(diagnostic);save()
        for name,digest in sources.items():
            if sha256(root/name)!=digest:raise ValueError('candidate account input changed during evaluation')
        for name,digest in code_hashes.items():
            if hashlib.sha256((root/'ashare_mvp'/name).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest()!=digest:raise ValueError('candidate account code changed during evaluation')
        proof.update(status='ranking_accounts_evaluated_selection_pending',completed_at_utc=datetime.now(timezone.utc).isoformat(),
            output_hashes={p.relative_to(target).as_posix():sha256(p) for p in target.rglob('*') if p.is_file() and p.name!='manifest.json'})
        save();return proof
    except BaseException as error:proof.update(status='failed',error=str(error));save();raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',default='.')
    for name in ['training','dataset','replay','benchmark','seal-directory','out']:p.add_argument('--'+name,required=True)
    p.add_argument('--reference-evidence')
    p.add_argument('--missing-payment-evidence')
    evaluate_candidate_rankings(**vars(p.parse_args()))


if __name__=='__main__':main()

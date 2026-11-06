"""Sealed research accounts with daily technical timing on fixed Top20 refreshes."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json
import pandas as pd
from .phase2_protocol import load_seal,sha256
from .candidate_portfolios import account_quotes,paired_net_returns
from .data import Dataset
from .model import Config
from .horizon_research import compare_index
from .timing_policy import CachedTimingSignals
from .timing_account import simulate_timing
from .causal_quality import HeldValuationError


def timing_buy_permissions(path,symbols,first,last):
    """Current session provider ST flags gate buys only, never remove holdings.

    This is an execution status, not a return predictor. Original historical
    byte vintages are uncertified. Missing status blocks buys; no forward fill.
    """
    parts=[]
    for chunk in pd.read_csv(path,usecols=['date','symbol','is_st'],dtype={'symbol':str,'is_st':'string'},parse_dates=['date'],chunksize=200000):
        selected=chunk[chunk.symbol.isin(symbols)&chunk.date.between(first,last)].copy()
        if not selected.empty:parts.append(selected)
    if not parts:raise ValueError('dated ST execution flags required')
    flags=pd.concat(parts,ignore_index=True)
    if flags.duplicated(['date','symbol']).any() or not flags.is_st.dropna().isin(['True','False','0','1']).all():raise ValueError('unique binary or unknown ST statuses required')
    flags['new_buy_allowed']=flags.is_st.isin(['False','0'])
    return flags[['date','symbol','new_buy_allowed']]


def evaluate_timing(project,accounts,cache,dataset,benchmark,seal_directory,clarification,out,output_root=None,reference_evidence=None,groups=None):
    root=Path(project).resolve()
    def local(value):
        p=Path(value)
        if p.is_absolute() or '..' in p.parts or ':' in str(p):raise ValueError('project-relative input required')
        p=(root/p).resolve()
        if not p.is_relative_to(root):raise ValueError('input escapes project')
        return p
    source,signals,data,index,sealed,rules=map(local,[accounts,cache,dataset,benchmark,seal_directory,clarification])
    relative=Path(out)
    if relative.is_absolute() or '..' in relative.parts or ':' in str(relative):raise ValueError('relative output required')
    base=root if output_root is None else Path(output_root).resolve();target=(base/relative).resolve()
    if not target.is_relative_to(base) or target.exists() or any(target.is_relative_to(p) or p.is_relative_to(target) for p in [source,signals,data,index,sealed,rules]):raise ValueError('fresh output outside inputs required')
    protocol,seal=load_seal(sealed,root);family=json.loads((source/'manifest.json').read_text(encoding='utf8'));cache_meta=json.loads((signals/'manifest.json').read_text(encoding='utf8'))
    for m in [family,cache_meta]:
        if m.get('protocol_sha256')!=seal['protocol_sha256'] or m.get('independent_data_used') is not False:raise ValueError('same sealed research inputs required')
    expected={(h,t,g) for h in protocol['horizons'] for t in [30,60] for g in ['baseline','market_state','oneil_box','industry','combined']}
    if family.get('status')!='full_family_ranking_accounts_evaluated_selection_pending' or len(family.get('accounts',[]))!=30 or {(r['horizon'],r['n_estimators'],r['group']) for r in family['accounts']}!=expected or any(r['status']!='complete_calendar_mature_ranking_account' for r in family['accounts']) or family.get('model_selection_performed') is not False or family.get('formal_model_promoted') is not False:raise ValueError('complete unselected thirty-account source required')
    if cache_meta.get('status')!='research_candidates_built':raise ValueError('complete original timing cache required')
    declared=['market_state','oneil_box','combined'];groups=declared if groups is None else list(groups)
    if not groups or len(set(groups))!=len(groups) or not set(groups).issubset(declared):raise ValueError('only original technical timing groups; industry rule unspecified')
    started=datetime.now(timezone.utc);clarified=json.loads(rules.read_text(encoding='utf8'))
    if clarified.get('schema')!='phase2_timing_operational_clarification_v1' or clarified.get('implementation')!='close_timing_policy_v1' or clarified.get('original_protocol_sha256')!=seal['protocol_sha256'] or clarified.get('originally_prespecified') is not False or clarified.get('performance_used_for_clarification') is not False or datetime.fromisoformat(clarified['recorded_at_utc'])>started:raise ValueError('dated explicit operational clarification required before evaluation')
    sources={}
    def bind(p,expected=None):
        actual=sha256(p)
        if expected is not None and actual!=expected:raise ValueError('timing input changed: '+p.name)
        sources[p.relative_to(root).as_posix()]=actual
    for p in [source/'manifest.json',signals/'manifest.json',rules]:bind(p)
    for name,digest in family['source_hashes'].items():bind(local(name),digest)
    for name,digest in cache_meta['source_hashes'].items():bind(local(name),digest)
    def code_hash(name):return hashlib.sha256((root/'ashare_mvp'/name).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest()
    for m in [family,cache_meta]:
        for name,digest in m['code_hashes'].items():
            if code_hash(name)!=digest:raise ValueError('original producer changed: '+name)
    for name in ['daily_timing.sqlite','market_states.csv']:bind(signals/name,cache_meta['output_hashes'][name])
    metadata=json.loads((data/'metadata.json').read_text(encoding='utf8'));bind(data/'metadata.json')
    if metadata.get('synthetic') is not False or metadata.get('quality_policy',{}).get('schema')!='causal_quality_research_v1':raise ValueError('real causal-quality source required')
    for name in ['calendar.csv','event_raw_prices.csv','prices.csv']:
        bind(data/name,metadata['converted_hashes'][name])
        if name!='prices.csv' and family['source_hashes'].get((data/name).relative_to(root).as_posix())!=sources[(data/name).relative_to(root).as_posix()]:raise ValueError('ranking/timing source mismatch')
    bind(source/'effective_valuation_actions.csv',family['output_hashes']['effective_valuation_actions.csv'])
    bm=json.loads((index/'summary.json').read_text(encoding='utf8'))
    if bm.get('return_kind')!='price_index':raise ValueError('price index benchmark required')
    bind(index/'summary.json');bind(index/'raw_prices.csv',bm['raw_sha256'])
    if family['source_hashes'].get((index/'raw_prices.csv').relative_to(root).as_posix())!=bm['raw_sha256']:raise ValueError('same ranking benchmark required')
    calendar=pd.DatetimeIndex(pd.to_datetime(pd.read_csv(data/'calendar.csv').date))
    states=pd.read_csv(signals/'market_states.csv',parse_dates=['date'],float_precision='round_trip')
    if calendar.max()>pd.Timestamp(protocol['research_end']) or not states.date.isin(calendar).all():raise ValueError('independent sessions cannot enter timing research')
    actions=pd.read_csv(source/'effective_valuation_actions.csv',float_precision='round_trip',low_memory=False)
    for name in ['ex_date','available_date','pay_date']:actions[name]=pd.to_datetime(actions[name],format='mixed')
    overlay=None
    if reference_evidence:
        from .valuation_overlay import reviewed_valuation_references
        actions,overlay=reviewed_valuation_references(actions,reference_evidence,root,calendar,protocol['research_end'])
        for name,digest in overlay['source_hashes'].items():bind(local(name),digest)
    benchmark_frame=pd.read_csv(index/'raw_prices.csv',parse_dates=['date'],float_precision='round_trip')
    names=['timing_portfolios.py','timing_policy.py','timing_account.py','pipeline.py','candidate_portfolios.py','corporate_actions.py','causal_quality.py','costs.py','technical_candidates.py','valuation_overlay.py','reference_overrides.py']
    codes={n:code_hash(n) for n in names};target.mkdir(parents=True);actions.to_csv(target/'effective_valuation_actions.csv',index=False)
    proof=dict(schema='technical_timing_accounts_v1',status='running',started_at_utc=started.isoformat(),protocol_sha256=seal['protocol_sha256'],source_hashes=sources,code_hashes=codes,groups_attempted=groups,accounts=[],paired_diagnostics=[],reference_overlay=overlay,independent_data_used=False,model_selection_performed=False,formal_model_promoted=False,operational_clarification_originally_prespecified=False,family_comparisons=24,pending=['industry standalone timing rule unspecified','eligible equal-weight full valuation','final fixed-family selection','independent mature validation'],limitations=['Historical reconstructed source vintages and executable limit states unverified.','Operational clarification dated after original seal; original entry/exit conditions unchanged.','Fractional raw shares, gross dividends, dated costs; no terminal forced liquidation.'])
    def save():
        p=target/'manifest.next.json';p.write_text(json.dumps(proof,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8');p.replace(target/'manifest.json')
    save();reader=CachedTimingSignals(signals/'daily_timing.sqlite',states)
    try:
        for h in protocol['horizons']:
            for trees in [30,60]:
                for group in groups:
                    parent=source/h/str(trees)/group;record=next(r for r in family['accounts'] if (r['horizon'],r['n_estimators'],r['group'])==(h,trees,group))
                    for name in ['scheduled_top20.csv','daily_nav.csv']:bind(parent/name,family['output_hashes'][(parent/name).relative_to(source).as_posix()])
                    baseline=source/h/str(trees)/'baseline/daily_nav.csv';bind(baseline,family['output_hashes'][baseline.relative_to(source).as_posix()])
                    picks=pd.read_csv(parent/'scheduled_top20.csv',parse_dates=['date','available_date'],float_precision='round_trip')
                    first=picks.date.min();last=pd.Timestamp(record['requested_end']);symbols=set(picks.symbol)
                    print(json.dumps(dict(stage='timing_quotes',horizon=h,n_estimators=trees,group=group,symbols=len(symbols))),flush=True)
                    raw=account_quotes(data/'event_raw_prices.csv',symbols,first,last)
                    prices=raw[['date','symbol']].copy();tradable=raw.tradestatus.eq('1')&raw.open.gt(0)
                    prices['total_return_open']=raw.open.where(tradable,raw.preclose);prices['can_buy']=tradable;prices['can_sell']=tradable
                    permissions=timing_buy_permissions(data/'prices.csv',symbols,first,last)
                    prices=prices.merge(permissions,on=['date','symbol'],how='left',validate='one_to_one')
                    prices['can_buy'] &= prices.new_buy_allowed.fillna(False).astype(bool)
                    dates=calendar[calendar<=last];book=Dataset(prices,pd.DataFrame(),dates,metadata);events=actions[actions.symbol.isin(symbols)&actions.ex_date.between(first,last)]
                    folder=target/h/str(trees)/group;folder.mkdir(parents=True);picks.to_csv(folder/'scheduled_top20.csv',index=False)
                    row=dict(horizon=h,n_estimators=trees,group=group,first_signal=str(first.date()),requested_end=str(last.date()),scheduled_refreshes=int(picks.date.nunique()))
                    try:nav,orders,decisions=simulate_timing(book,picks,Config(top_k=protocol['execution']['top_k'],commission_bps=3.,slippage_bps=5.),reader,group,raw,events)
                    except HeldValuationError as e:
                        e.nav.to_csv(folder/'partial_valid_nav.csv',index=False);e.trades.to_csv(folder/'partial_orders.csv',index=False);e.timing_decisions.to_json(folder/'partial_decisions.json',orient='records',indent=2)
                        row.update(status='incomplete_held_valuation',reason=e.reason,symbol=e.symbol,date=str(e.date.date()),holdings=e.holdings);e.__traceback__=None
                    else:
                        rank_nav=pd.read_csv(parent/'daily_nav.csv',parse_dates=['date'],float_precision='round_trip');base_nav=pd.read_csv(baseline,parse_dates=['date'],float_precision='round_trip')
                        if not pd.DatetimeIndex(nav.date).equals(pd.DatetimeIndex(rank_nav.date)):raise ValueError('full identical ranking/timing valuation calendar required')
                        daily,metrics=compare_index(nav,orders,benchmark_frame,'price_index')
                        nav.to_csv(folder/'daily_nav.csv',index=False);orders.to_csv(folder/'orders.csv',index=False);daily.to_csv(folder/'index_comparison.csv',index=False);decisions.to_json(folder/'decisions.json',orient='records',indent=2)
                        row.update(status='complete_calendar_mature_timing_account',metrics=metrics)
                        for label,control in [('same_candidate_ranking',rank_nav),('matched_original_factor_baseline',base_nav)]:
                            excess,diag=paired_net_returns(nav,control);diag.update(horizon=h,n_estimators=trees,group=group,control=label)
                            pd.DataFrame(dict(date=nav.date,paired_net_return_difference=excess)).to_csv(folder/('paired_'+label+'.csv'),index=False);proof['paired_diagnostics'].append(diag)
                    (folder/'summary.json').write_text(json.dumps(row,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8');proof['accounts'].append(row);save();print(json.dumps({k:row[k] for k in ['horizon','n_estimators','group','status']}),flush=True)
                    del raw,prices,events,book,picks,permissions
        for name,digest in sources.items():
            if sha256(root/name)!=digest:raise ValueError('timing input changed during run')
        if {n:code_hash(n) for n in names}!=codes:raise ValueError('timing code changed during run')
        proof.update(status='technical_timing_attempts_finished_selection_pending',completed_at_utc=datetime.now(timezone.utc).isoformat(),output_hashes={p.relative_to(target).as_posix():sha256(p) for p in target.rglob('*') if p.is_file() and p.name not in ['manifest.json','manifest.next.json']});save();return proof
    except BaseException as error:proof.update(status='failed',error=str(error),failed_at_utc=datetime.now(timezone.utc).isoformat());save();raise
    finally:reader.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['project','accounts','cache','dataset','benchmark','seal-directory','clarification','out']:p.add_argument('--'+n,required=True)
    p.add_argument('--output-root');p.add_argument('--reference-evidence');p.add_argument('--groups',nargs='+',choices=['market_state','oneil_box','combined'])
    evaluate_timing(**vars(p.parse_args()))

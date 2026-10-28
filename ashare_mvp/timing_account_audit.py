"""Independent shares/cash/intent/paired reconstruction of technical timing runs."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json,sqlite3
import numpy as np
import pandas as pd
from .phase2_protocol import sha256
from .candidate_portfolios import account_quotes,paired_net_returns
from .accounting_attribution import attribute_open_account
from .timing_verification import verify_timing_intents


def verify_st_fills(orders,path):
    if orders.empty:return 0
    buys=orders[orders.side.eq('buy')&orders.status.eq('filled')&orders.notional.gt(1e-12)][['date','symbol']].drop_duplicates()
    if buys.empty:return 0
    parts=[];symbols=set(buys.symbol)
    for p in pd.read_csv(path,usecols=['date','symbol','is_st'],parse_dates=['date'],dtype={'symbol':str,'is_st':'string'},chunksize=200000):
        parts.append(p[p.symbol.isin(symbols)&p.date.between(buys.date.min(),buys.date.max())])
    flags=pd.concat(parts,ignore_index=True);joined=buys.merge(flags,on=['date','symbol'],how='left',validate='one_to_one')
    if not joined.is_st.isin(['False','0']).all():raise ValueError('filled new buy has ST or unknown execution status')
    return len(joined)


def audit_timing(project,source,source_root,ranking_accounts,dataset,cache,benchmark,out,output_root):
    root=Path(project).resolve()
    def under(base,value):
        p=Path(value)
        if p.is_absolute() or '..' in p.parts or ':' in str(p):raise ValueError('relative audited path required')
        result=(base/p).resolve()
        if not result.is_relative_to(base):raise ValueError('audit path escapes root')
        return result
    src=under(Path(source_root).resolve(),source);target=under(Path(output_root).resolve(),out)
    ranking,data,signals,index=[under(root,v) for v in [ranking_accounts,dataset,cache,benchmark]]
    if target.exists() or any(target.is_relative_to(p) or p.is_relative_to(target) for p in [src,ranking,data,signals,index]):raise ValueError('fresh non-overlapping audit output required')
    m=json.loads((src/'manifest.json').read_text(encoding='utf8'));source_hash=sha256(src/'manifest.json')
    groups=m.get('groups_attempted',[]);expected={(h,t,g) for h in ['5d','20d','12m'] for t in [30,60] for g in groups}
    keys=[(r['horizon'],r['n_estimators'],r['group']) for r in m.get('accounts',[])]
    if m.get('status')!='technical_timing_attempts_finished_selection_pending' or not groups or not set(groups).issubset({'market_state','oneil_box','combined'}) or len(keys)!=len(expected) or set(keys)!=expected or m.get('family_comparisons')!=24 or any(m.get(k) is not False for k in ['independent_data_used','model_selection_performed','formal_model_promoted']):raise ValueError('terminal unselected fixed technical timing family required')
    def code(n):return hashlib.sha256((root/'ashare_mvp'/n).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest()
    for name,digest in m['source_hashes'].items():
        if sha256(under(root,name))!=digest:raise ValueError('timing source changed')
    for name,digest in m['code_hashes'].items():
        if code(name)!=digest:raise ValueError('timing producer changed')
    for name,digest in m['output_hashes'].items():
        if sha256(under(src,name))!=digest:raise ValueError('timing output changed')
    for p in [ranking/'manifest.json',data/'event_raw_prices.csv',data/'prices.csv',data/'calendar.csv',signals/'daily_timing.sqlite',signals/'market_states.csv',index/'raw_prices.csv']:
        if m['source_hashes'].get(p.relative_to(root).as_posix())!=sha256(p):raise ValueError('audit input not bound to producer')
    calendar=pd.DatetimeIndex(pd.to_datetime(pd.read_csv(data/'calendar.csv').date));states=pd.read_csv(signals/'market_states.csv',parse_dates=['date'],float_precision='round_trip');bench=pd.read_csv(index/'raw_prices.csv',parse_dates=['date'],float_precision='round_trip')
    if calendar.max()>pd.Timestamp('2025-12-31'):raise ValueError('independent sessions in historical research audit')
    actions=pd.read_csv(src/'effective_valuation_actions.csv',float_precision='round_trip',low_memory=False)
    for name in ['ex_date','available_date','pay_date']:actions[name]=pd.to_datetime(actions[name],format='mixed')
    audit_code={n:code(n) for n in ['timing_account_audit.py','timing_verification.py','accounting_attribution.py','candidate_portfolios.py']}
    target.mkdir(parents=True);proof=dict(schema='independent_technical_timing_verification_v1',status='reconstructing',started_at_utc=datetime.now(timezone.utc).isoformat(),source_manifest_sha256=source_hash,verification_code_sha256=audit_code,accounts=[],paired_checks=[],independent_data_used=False,model_selection_performed=False,formal_model_promoted=False,certified=False,industry_attribution='pending separate calculation',interpretation='Independent arithmetic and preceding-close intent replay; not certification of historical vintages or real fills.')
    def save():
        p=target/'manifest.next.json';p.write_text(json.dumps(proof,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8');p.replace(target/'manifest.json')
    save();con=sqlite3.connect((signals/'daily_timing.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
    try:
        for r in m['accounts']:
            h,t,g=r['horizon'],r['n_estimators'],r['group'];key=f'{h}/{t}/{g}';folder=src/key
            if r['status']!='complete_calendar_mature_timing_account':
                proof['accounts'].append(dict(horizon=h,n_estimators=t,group=g,status='incomplete_no_full_verification'));save();continue
            nav=pd.read_csv(folder/'daily_nav.csv',parse_dates=['date'],float_precision='round_trip')
            try:orders=pd.read_csv(folder/'orders.csv',parse_dates=['date'],float_precision='round_trip')
            except pd.errors.EmptyDataError:orders=pd.DataFrame(columns=['date','symbol','side','status','notional'])
            picks=pd.read_csv(folder/'scheduled_top20.csv',parse_dates=['date'],float_precision='round_trip')
            raw=account_quotes(data/'event_raw_prices.csv',set(picks.symbol)|set(orders.symbol),picks.date.min(),pd.Timestamp(r['requested_end']))
            events=actions[actions.symbol.isin(raw.symbol.unique())&actions.ex_date.between(nav.date.min(),nav.date.max())]
            daily,positions,assets=attribute_open_account(nav,orders,raw,events,bench)
            decisions=pd.read_json(folder/'decisions.json')
            if not pd.DatetimeIndex(pd.to_datetime(decisions.execution_date)).equals(pd.DatetimeIndex(nav.date)):raise ValueError('decisions do not cover every account open')
            intent=verify_timing_intents(calendar,picks,decisions,orders,positions,states,con,g);buy_keys=verify_st_fills(orders,data/'prices.csv')
            dest=target/key;dest.mkdir(parents=True)
            for frame,name in [(daily,'daily_attribution.csv'),(positions,'positions.csv'),(assets,'asset_attribution.csv')]:frame.to_csv(dest/name,index=False)
            row=dict(horizon=h,n_estimators=t,group=g,status='complete_account_and_intents_independently_verified',sessions=len(nav),max_absolute_reconciliation_error=float(daily.reconciliation_error.abs().max()),verified_non_st_buy_keys=buy_keys,**intent)
            proof['accounts'].append(row);save();print(json.dumps(row),flush=True)
            del raw,events,daily,positions,assets,nav,orders,picks,decisions
        for item in m['paired_diagnostics']:
            h,t,g,label=item['horizon'],item['n_estimators'],item['group'],item['control'];folder=src/h/str(t)/g
            control=ranking/h/str(t)/(g if label=='same_candidate_ranking' else 'baseline')/'daily_nav.csv'
            if label not in ['same_candidate_ranking','matched_original_factor_baseline'] or m['source_hashes'].get(control.relative_to(root).as_posix())!=sha256(control):raise ValueError('unknown or unbound paired control')
            nav=pd.read_csv(folder/'daily_nav.csv',parse_dates=['date'],float_precision='round_trip');baseline=pd.read_csv(control,parse_dates=['date'],float_precision='round_trip');excess,diag=paired_net_returns(nav,baseline)
            stored=pd.read_csv(folder/('paired_'+label+'.csv'),parse_dates=['date'],float_precision='round_trip')
            if not stored.date.equals(nav.date) or not np.array_equal(stored.paired_net_return_difference.to_numpy(),excess) or any(item[k]!=v for k,v in diag.items()):raise ValueError('paired diagnostic did not independently reproduce')
            proof['paired_checks'].append(dict(horizon=h,n_estimators=t,group=g,control=label,exact=True))
        if sha256(src/'manifest.json')!=source_hash or {n:code(n) for n in audit_code}!=audit_code:raise ValueError('source or verifier changed during audit')
        for name,digest in m['source_hashes'].items():
            if sha256(under(root,name))!=digest:raise ValueError('source changed during verification')
        proof.update(status='available_complete_timing_accounts_verified',completed_at_utc=datetime.now(timezone.utc).isoformat(),completed_accounts=sum(r['status']=='complete_account_and_intents_independently_verified' for r in proof['accounts']),output_hashes={p.relative_to(target).as_posix():sha256(p) for p in target.rglob('*.csv')});save();return proof
    except BaseException as e:proof.update(status='failed',error=str(e),failed_at_utc=datetime.now(timezone.utc).isoformat());save();raise
    finally:con.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['project','source','source-root','ranking-accounts','dataset','cache','benchmark','out','output-root']:p.add_argument('--'+name,required=True)
    audit_timing(**vars(p.parse_args()))

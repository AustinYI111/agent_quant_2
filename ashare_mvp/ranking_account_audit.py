"""Resumable independent reconstruction of the complete fixed ranking family."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json
import numpy as np
import pandas as pd
from .phase2_protocol import sha256
from .candidate_portfolios import account_quotes,paired_net_returns
from .accounting_attribution import attribute_open_account


def verify_checkpoint(proof,source_hash,code_hashes,target,expected):
    if proof.get('schema')!='resumable_full_family_account_audit_v1' or proof.get('source_manifest_sha256')!=source_hash or proof.get('verification_code_sha256')!=code_hashes:
        raise ValueError('audit checkpoint source or verification code changed')
    if proof.get('status') not in ['reconstructing','all_available_complete_accounts_reconciled']:
        raise ValueError('only known audit checkpoint states can resume')
    seen=set()
    for row in proof.get('accounts',[]):
        key=(row['horizon'],row['n_estimators'],row['group'])
        if key not in expected or key in seen:raise ValueError('duplicate or unexpected audited account')
        seen.add(key)
        if row['status']=='complete_account_independently_reconciled':
            paths={f'{key[0]}/{key[1]}/{key[2]}/{name}' for name in ['daily_attribution.csv','asset_attribution.csv','positions.csv']}
            if set(row.get('output_hashes',{}))!=paths:raise ValueError('all three complete audit outputs required')
            for name,digest in row['output_hashes'].items():
                if sha256(target/name)!=digest:raise ValueError('completed audit output changed')
        elif row['status']!='incomplete_no_full_account_attribution':raise ValueError('unknown audit account state')
    return seen


def audit_ranking_accounts(project,source,dataset,benchmark,out,resume=False):
    root=Path(project).resolve()
    def local(value):
        p=Path(value)
        if p.is_absolute() or '..' in p.parts or ':' in str(p):raise ValueError('project-relative audit paths required')
        p=(root/p).resolve()
        if not p.is_relative_to(root):raise ValueError('audit path escapes project')
        return p
    source,data,index,target=map(local,[source,dataset,benchmark,out])
    if any(target.is_relative_to(p) or p.is_relative_to(target) for p in [source,data,index]):raise ValueError('audit output overlaps immutable input')
    def read(p):return json.loads(p.read_text(encoding='utf8'))
    manifest=read(source/'manifest.json');source_hash=sha256(source/'manifest.json')
    expected={(h,t,g) for h in ['5d','20d','12m'] for t in [30,60] for g in ['baseline','market_state','oneil_box','industry','combined']}
    keys=[(r['horizon'],r['n_estimators'],r['group']) for r in manifest.get('accounts',[])]
    if manifest.get('status')!='full_family_ranking_accounts_evaluated_selection_pending' or len(keys)!=30 or set(keys)!=expected or any(manifest.get(k) is not False for k in ['independent_data_used','model_selection_performed','formal_model_promoted']):raise ValueError('terminal fixed thirty-account research family required')
    for name,digest in manifest['source_hashes'].items():
        if sha256(local(name))!=digest:raise ValueError('account source changed: '+name)
    for name,digest in manifest['code_hashes'].items():
        if hashlib.sha256((root/'ashare_mvp'/name).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest()!=digest:raise ValueError('account producer code changed: '+name)
    for name,digest in manifest['output_hashes'].items():
        path=local((source/name).relative_to(root).as_posix())
        if sha256(path)!=digest:raise ValueError('account output changed: '+name)
    for path in [data/'event_raw_prices.csv',index/'raw_prices.csv']:
        if manifest['source_hashes'].get(path.relative_to(root).as_posix())!=sha256(path):raise ValueError('identical original quote/benchmark source required')
    code_names=['ranking_account_audit.py','accounting_attribution.py','candidate_portfolios.py']
    def code():return {n:hashlib.sha256((root/'ashare_mvp'/n).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest() for n in code_names}
    codes=code()
    if target.exists():
        if not resume:raise ValueError('fresh audit output or explicit resume required')
        proof=read(target/'manifest.json');done=verify_checkpoint(proof,source_hash,codes,target,expected)
        if proof['status']=='all_available_complete_accounts_reconciled':return proof
    else:
        target.mkdir(parents=True)
        proof=dict(schema='resumable_full_family_account_audit_v1',status='reconstructing',started_at_utc=datetime.now(timezone.utc).isoformat(),source_manifest_sha256=source_hash,verification_code_sha256=codes,accounts=[],paired_checks=[],models_retrained=False,model_selection_performed=False,independent_data_used=False,formal_model_promoted=False,certified=False,market_reference='same-open unit-beta CSI300 price reference; residual is not causal alpha',industry_attribution='pending historical industry contribution calculation')
        done=set()
    def save():
        temp=target/'manifest.next.json'
        temp.write_text(json.dumps(proof,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8');temp.replace(target/'manifest.json')
    save()
    complete=[r for r in manifest['accounts'] if r['status']=='complete_calendar_mature_ranking_account' and (r['horizon'],r['n_estimators'],r['group']) not in done]
    symbols=set();first=None;last=None
    for r in complete:
        folder=source/r['horizon']/str(r['n_estimators'])/r['group']
        picks=pd.read_csv(folder/'scheduled_top20.csv',usecols=['symbol'],dtype={'symbol':str});symbols.update(picks.symbol)
        first=min(first,pd.Timestamp(r['first_signal'])) if first is not None else pd.Timestamp(r['first_signal'])
        last=max(last,pd.Timestamp(r['requested_end'])) if last is not None else pd.Timestamp(r['requested_end'])
    quotes=account_quotes(data/'event_raw_prices.csv',symbols,first,last) if complete else None
    print(json.dumps(dict(stage='raw_quote_cache_loaded',remaining_complete_accounts=len(complete),symbols=len(symbols),rows=0 if quotes is None else len(quotes))),flush=True)
    actions=pd.read_csv(source/'effective_valuation_actions.csv',float_precision='round_trip',low_memory=False)
    for col in ['ex_date','available_date','pay_date']:actions[col]=pd.to_datetime(actions[col],format='mixed')
    benchmark_frame=pd.read_csv(index/'raw_prices.csv',parse_dates=['date'],float_precision='round_trip')
    for r in manifest['accounts']:
        h,t,g=r['horizon'],r['n_estimators'],r['group'];key=(h,t,g)
        if key in done:continue
        folder=source/h/str(t)/g
        if r['status']!='complete_calendar_mature_ranking_account':
            proof['accounts'].append(dict(horizon=h,n_estimators=t,group=g,status='incomplete_no_full_account_attribution'));save();continue
        nav=pd.read_csv(folder/'daily_nav.csv',parse_dates=['date'],float_precision='round_trip')
        orders=pd.read_csv(folder/'orders.csv',parse_dates=['date'],dtype={'symbol':str},float_precision='round_trip')
        stock_names=set(pd.read_csv(folder/'scheduled_top20.csv',usecols=['symbol'],dtype={'symbol':str}).symbol)|set(orders.symbol)
        raw=quotes[quotes.symbol.isin(stock_names)&quotes.date.between(nav.date.min(),nav.date.max())]
        events=actions[actions.symbol.isin(stock_names)&actions.ex_date.between(nav.date.min(),nav.date.max())]
        daily,positions,assets=attribute_open_account(nav,orders,raw,events,benchmark_frame)
        if len(daily)!=len(nav) or not np.isfinite(daily.reconciliation_error).all():raise ValueError('incomplete accounting reconstruction')
        dest=target/h/str(t)/g;dest.mkdir(parents=True,exist_ok=True)
        for value,name in [(daily,'daily_attribution.csv'),(assets,'asset_attribution.csv'),(positions,'positions.csv')]:value.to_csv(dest/name,index=False)
        row=dict(horizon=h,n_estimators=t,group=g,status='complete_account_independently_reconciled',sessions=len(daily),first=str(nav.date.min().date()),last=str(nav.date.max().date()),max_absolute_reconciliation_error=float(daily.reconciliation_error.abs().max()),price_pnl=float(daily.price_pnl.sum()),gross_dividend_entitlement=float(daily.gross_dividend_entitlement.sum()),commission=float(daily.commission_cost.sum()),slippage=float(daily.slippage_cost.sum()),stamp_duty=float(daily.stamp_duty_cost.sum()),ending_nav=float(nav.nav.iloc[-1]),output_hashes={p.relative_to(target).as_posix():sha256(p) for p in dest.glob('*.csv')})
        proof['accounts'].append(row);save();print(json.dumps(dict(audited=len(proof['accounts']),horizon=h,trees=t,group=g,error=row['max_absolute_reconciliation_error'])),flush=True)
        del daily,assets,positions,raw,nav,orders
    # Recompute every paired diagnostic at completion, including after a resume.
    proof['paired_checks']=[]
    for item in manifest['paired_diagnostics']:
        if 'sessions' not in item:continue
        h,t,g=item['horizon'],item['n_estimators'],item['group']
        nav=pd.read_csv(source/h/str(t)/g/'daily_nav.csv',parse_dates=['date'],float_precision='round_trip')
        baseline=pd.read_csv(source/h/str(t)/'baseline/daily_nav.csv',parse_dates=['date'],float_precision='round_trip')
        difference,diagnostic=paired_net_returns(nav,baseline)
        stored=pd.read_csv(source/h/str(t)/g/'paired_daily.csv',parse_dates=['date'],float_precision='round_trip')
        if not stored.date.equals(nav.date) or not np.array_equal(stored.paired_net_return_difference.to_numpy(),difference) or any(v!=item[k] for k,v in diagnostic.items()):raise ValueError('paired diagnostic differs from independent reconstruction')
        proof['paired_checks'].append(dict(horizon=h,n_estimators=t,group=g,exact=True))
    if sha256(source/'manifest.json')!=source_hash or code()!=codes:raise ValueError('audit source or code changed during reconstruction')
    verify_checkpoint(proof,source_hash,codes,target,expected)
    if len(proof['accounts'])!=30:raise ValueError('all thirty attempts must be accounted for')
    proof.update(status='all_available_complete_accounts_reconciled',completed_accounts=sum(r['status']=='complete_account_independently_reconciled' for r in proof['accounts']),completed_at_utc=datetime.now(timezone.utc).isoformat());save();return proof


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project',default='.')
    for name in ['source','dataset','benchmark','out']:parser.add_argument('--'+name,required=True)
    parser.add_argument('--resume',action='store_true')
    result=audit_ranking_accounts(**vars(parser.parse_args()));print(json.dumps(dict(status=result['status'],completed_accounts=result['completed_accounts'])),flush=True)

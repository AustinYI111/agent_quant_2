"""Bounded independent reconstruction from observed disk orders, not targets."""
from pathlib import Path
from datetime import datetime,timezone
from itertools import groupby,zip_longest
import argparse,csv,json,math
import numpy as np
import pandas as pd
from .disk_market import QuoteStore,ReferenceAudit,code_hashes,write_manifest
from .phase2_protocol import sha256
from .costs import stamp_duty_rate


def prefix_matches(old_folder,new_folder):
    """Compare every old valid NAV/order scalar, no full-history DataFrames."""
    old=Path(old_folder);new=Path(new_folder);last=None;counts={}
    for name in ['partial_valid_nav.csv','partial_orders.csv']:
        with (old/name).open(newline='',encoding='utf8') as af,(new/name).open(newline='',encoding='utf8') as bf:
            a=csv.DictReader(af);b=csv.DictReader(bf)
            if a.fieldnames!=b.fieldnames:raise ValueError('old/new prefix schemas differ')
            n=0
            for x in a:
                y=next(b,None)
                if y is None:raise ValueError('new valid prefix is shorter')
                if name=='partial_valid_nav.csv':last=x['date']
                for k in a.fieldnames:
                    if k in ['date','symbol','side','status','return_basis']:
                        equal=x[k]==y[k]
                    else:equal=float(x[k])==float(y[k])
                    if not equal:raise ValueError('old/new prefix changed: '+name+' '+k)
                n+=1
            if name=='partial_orders.csv':
                extra=next(b,None)
                if extra is not None and extra['date']<=last:raise ValueError('extra new trade within original valid prefix')
            counts[name]=n
    return dict(old_valid_prefix_exact=True,through_date=last,rows=counts)


def reconstruct_disk_account(store,reference,folder,first,last,out,stop_date=None,expected_holdings=None):
    source=Path(folder);target=Path(out)
    if target.exists() or target.resolve().is_relative_to(source.resolve()):raise ValueError('fresh separate reconstruction directory required')
    target.mkdir(parents=True);partial=stop_date is not None
    nav_path=source/('partial_valid_nav.csv' if partial else 'daily_nav.csv');trade_path=source/('partial_orders.csv' if partial else 'orders.csv')
    dates=store.calendar[(store.calendar>=pd.Timestamp(first))&(store.calendar<=pd.Timestamp(last))]
    if partial:dates=dates[dates<pd.Timestamp(stop_date)]
    cash=1.;quantities={};marks={};receivables=[];previous=1.;peak_error=0.;sessions=orders_checked=max_day_quotes=0
    actions={d:g for d,g in reference.actions.groupby('ex_date')}
    columns=['date','reported_nav','reconstructed_nav','price_pnl','gross_dividend_entitlement','execution_cost','cash','receivable','reconciliation_error']
    totals_path=source/('partial_execution_totals.csv' if partial and (source/'partial_execution_totals.csv').exists() else 'daily_execution_totals.csv')
    with nav_path.open(newline='',encoding='utf8') as nf,trade_path.open(newline='',encoding='utf8') as tf,totals_path.open(newline='',encoding='utf8') as sf,(target/'daily_reconstruction.csv').open('w',newline='',encoding='utf8') as of:
        nav=csv.DictReader(nf);totals=csv.DictReader(sf);groups=groupby(csv.DictReader(tf),key=lambda r:r['date']);pending=next(groups,None);writer=csv.DictWriter(of,fieldnames=columns);writer.writeheader()
        for date,row in zip_longest(dates,nav):
            if date is None or row is None or str(date.date())!=row['date']:raise ValueError('reconstruction must cover every requested valid session')
            bars={s:(float(o),float(p),state,None if t is None else pd.Timestamp(t),bool(b),bool(e)) for s,o,p,state,t,b,e in store.connection.execute('SELECT symbol,open,preclose,tradestatus,terminal_date,can_buy,can_sell FROM quotes WHERE date=?',(row['date'],))}
            max_day_quotes=max(max_day_quotes,len(bars))
            faults={s for (s,) in reference.connection.execute('SELECT symbol FROM faults WHERE date=?',(row['date'],))}
            def price(symbol):
                if symbol not in bars:raise ValueError('missing positive held quote in independent account')
                op,pre,state,terminal,_,_=bars[symbol]
                value=op if state=='1' and op>0 else pre
                if not np.isfinite(value) or value<=0:raise ValueError('unknown independent valuation')
                return value
            event={r.symbol:r for r in actions[date].itertuples(index=False)} if date in actions else {}
            pnl=income=cost=0.
            day_amounts=[];day_costs=[];filled=blocked=buys=0
            for s in quantities:
                if s not in bars or s in faults or bars[s][3] is not None and date>=bars[s][3]:raise ValueError('unresolved old positive holding inside valid prefix')
            for s,q in list(quantities.items()):
                e=event.get(s);mult=float(e.share_multiplier) if e else 1.;div=q*float(e.cash_per_share) if e else 0.
                pnl+=q*(mult*price(s)-marks[s]);income+=div;quantities[s]=q*mult
                if div:receivables.append((e.pay_date,div))
            cash+=sum(v for pay,v in receivables if pay<date);receivables=[(pay,v) for pay,v in receivables if pay>=date]
            if pending is not None and pending[0]<row['date']:raise ValueError('trade outside valid NAV calendar')
            if pending is not None and pending[0]==row['date']:
                for order in pending[1]:
                    if order['side'] not in ['buy','sell'] or order['status'] not in ['filled','blocked']:raise ValueError('unknown order side or state')
                    amount=float(order['notional']);reported_cost=float(order['cost']);stamp=float(order['stamp_duty'])
                    if not np.isfinite([amount,reported_cost,stamp]).all() or min(amount,reported_cost,stamp)<0:raise ValueError('invalid observed order amounts')
                    sell=order['side']=='sell';rate=stamp_duty_rate(date,None) if sell else 0.
                    if not np.isclose(float(order['stamp_duty_bps']),rate*10000,rtol=0,atol=1e-12):raise ValueError('dated stamp-duty policy changed')
                    if order['status']=='blocked':
                        if reported_cost!=0 or stamp!=0:raise ValueError('blocked order charged cost')
                        blocked+=1
                    else:
                        s=order['symbol']
                        if s not in bars or bars[s][2]!='1' or bars[s][0]<=0 or not bars[s][5 if sell else 4] or not sell and bars[s][3] is not None and date>=bars[s][3]:raise ValueError('unexecutable fill')
                        fee=amount*.0003;slip=amount*.0005;tax=amount*rate
                        if not np.isclose(stamp,tax,rtol=0,atol=1e-12) or not np.isclose(reported_cost,fee+slip+tax,rtol=0,atol=1e-12):raise ValueError('commission/slippage/stamp contract changed')
                        sign=-1 if sell else 1;quantities[s]=quantities.get(s,0.)+sign*amount/bars[s][0];cash-=sign*amount+fee+slip+tax;cost+=fee+slip+tax
                        day_amounts.append(amount);day_costs.append(reported_cost);filled+=1;buys+=not sell
                        if quantities[s]<-1e-10:raise ValueError('negative independent shares')
                    orders_checked+=1
                pending=next(groups,None)
            total=next(totals,None)
            if total is None or total['date']!=row['date'] or not np.allclose([float(total['cost']),float(total['notional'])],[math.fsum(day_costs),math.fsum(day_amounts)],rtol=0,atol=1e-12) or [int(total[n]) for n in ['filled_orders','blocked_orders','filled_buys']]!=[filled,blocked,buys]:raise ValueError('daily execution totals differ from actual observed order stream')
            quantities={s:q for s,q in quantities.items() if q>1e-12}
            if cash<-1e-9:raise ValueError('negative independent cash')
            cash=max(cash,0.);marks={s:price(s) for s in quantities};receivable=sum(v for _,v in receivables);wealth=cash+receivable+sum(q*marks[s] for s,q in quantities.items())
            reported=float(row['nav']);error=reported-previous-(pnl+income-cost)
            if not np.allclose([wealth,cash,receivable],[reported,float(row['cash']),float(row['dividend_receivable'])],rtol=0,atol=1e-10) or abs(error)>1e-10 or int(row['positions'])!=len(quantities):raise ValueError('independent shares/cash/dividends/PnL differ')
            peak_error=max(peak_error,abs(error),abs(wealth-reported));writer.writerow(dict(date=row['date'],reported_nav=reported,reconstructed_nav=wealth,price_pnl=pnl,gross_dividend_entitlement=income,execution_cost=cost,cash=cash,receivable=receivable,reconciliation_error=error));previous=reported;sessions+=1
        if pending is not None:raise ValueError('trade after valid NAV prefix')
        if next(totals,None) is not None:raise ValueError('execution total after valid NAV prefix')
    if expected_holdings is not None and (set(expected_holdings)!=set(quantities) or any(not np.isclose(quantities[s],expected_holdings[s],rtol=0,atol=1e-12) for s in quantities)):raise ValueError('held shares at stop were removed or changed')
    return dict(status='valid_prefix_independently_reconciled' if partial else 'complete_account_independently_reconciled',sessions=sessions,orders_checked=orders_checked,max_absolute_reconciliation_error=peak_error,max_day_quotes=max_day_quotes,holdings_at_stop_checked=expected_holdings is not None,full_period_performance_verified=not partial)


def audit_streamed_equal(project,work_root,source,store_directory,reference_directory,out):
    root=Path(project).resolve();work=Path(work_root).resolve()
    def under(base,name):
        p=Path(name)
        if p.is_absolute() or '..' in p.parts or ':' in str(p):raise ValueError('relative audit path required')
        p=(base/p).resolve()
        if not p.is_relative_to(base):raise ValueError('audit path escapes root')
        return p
    source,stored,reviewed,target=[under(work,n) for n in [source,store_directory,reference_directory,out]]
    if target.exists() or any(target.is_relative_to(p) or p.is_relative_to(target) for p in [source,stored,reviewed]):raise ValueError('new separate audit output required')
    m=json.loads((source/'manifest.json').read_text(encoding='utf8'));source_hash=sha256(source/'manifest.json')
    if m.get('status')!='streamed_equal_weight_attempts_finished' or len(m.get('accounts',[]))!=3 or {r['horizon'] for r in m['accounts']}!={'5d','20d','12m'} or any(m.get(k) is not False for k in ['independent_data_used','model_selection_performed','formal_model_promoted']):raise ValueError('terminal unselected three-horizon streamed control required')
    for key,base in [('project_source_hashes',root),('work_source_hashes',work)]:
        for name,digest in m[key].items():
            if sha256(under(base,name))!=digest:raise ValueError('audit source changed')
    if code_hashes(list(m['code_hashes']))!=m['code_hashes']:raise ValueError('producer code changed')
    for name,digest in m['output_hashes'].items():
        if sha256(under(source,name))!=digest:raise ValueError('ledger changed before independent audit')
    store=QuoteStore(stored)
    try:reference=ReferenceAudit(reviewed,store)
    except BaseException:store.close();raise
    codes=code_hashes(['disk_account_audit.py','costs.py']);target.mkdir(parents=True)
    proof=dict(schema='bounded_equal_independent_audit_v1',status='reconstructing',started_at_utc=datetime.now(timezone.utc).isoformat(),source_manifest_sha256=source_hash,verification_code_hashes=codes,accounts=[],independent_data_used=False,model_selection_performed=False,formal_model_promoted=False)
    write_manifest(target/'manifest.json',proof)
    try:
        for r in m['accounts']:
            partial=r['status']=='incomplete_held_valuation'
            if not partial and r['status']!='complete_calendar_mature_equal_weight_control':raise ValueError('unknown streamed account state')
            first=store.calendar[store.calendar.searchsorted(pd.Timestamp(r['first_signal']),side='right')]
            record=reconstruct_disk_account(store,reference,source/r['horizon'],first,r['requested_end'],target/r['horizon'],r['date'] if partial else None,r['holdings'] if partial else None);record['horizon']=r['horizon'];proof['accounts'].append(record);write_manifest(target/'manifest.json',proof);print(json.dumps(record),flush=True)
        if sha256(source/'manifest.json')!=source_hash or code_hashes(list(codes))!=codes:raise ValueError('audit source or verifier changed')
        for name,digest in m['output_hashes'].items():
            if sha256(under(source,name))!=digest:raise ValueError('ledger changed during audit')
        if sha256(stored/'quotes.sqlite')!=store.manifest['database_sha256'] or sha256(reviewed/'faults.sqlite')!=reference.manifest['fault_database_sha256']:raise ValueError('market/reference database changed during audit')
        proof.update(status='all_available_valid_prefixes_reconciled',completed_at_utc=datetime.now(timezone.utc).isoformat(),complete_accounts=sum(r['full_period_performance_verified'] for r in proof['accounts']),output_hashes={p.relative_to(target).as_posix():sha256(p) for p in target.rglob('*.csv')});write_manifest(target/'manifest.json',proof);return proof
    except BaseException as e:proof.update(status='failed',error=str(e));write_manifest(target/'manifest.json',proof);raise
    finally:reference.close();store.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['project','work-root','source','store-directory','reference-directory','out']:p.add_argument('--'+name,required=True)
    audit_streamed_equal(**vars(p.parse_args()))

"""Independent rights cash/share reconstruction from observed ledgers and SQL.

Never calls the producer wallet, its valuation helpers, or target orders.
"""
from pathlib import Path
from itertools import groupby, zip_longest
import csv, math
import numpy as np
import pandas as pd
from .costs import stamp_duty_rate


def reconstruct_rights_account(store, reference, folder, first, last, out, policy,
                               stop_date=None, expected_holdings=None,
                               expected_rights_state=None, subscription_fee_bps=3.):
    if policy not in ['cash_limited_subscribe','decline']:
        raise ValueError('explicit rights audit policy required')
    source=Path(folder);target=Path(out)
    if target.exists() or target.resolve().is_relative_to(source.resolve()):
        raise ValueError('fresh separate rights reconstruction directory required')
    target.mkdir(parents=True)
    dates=store.calendar[(store.calendar>=pd.Timestamp(first))&(store.calendar<=pd.Timestamp(last))]
    if stop_date is not None:dates=dates[dates<pd.Timestamp(stop_date)]
    events={e['event_id']:e for e in reference.events}
    actions={d:g for d,g in reference.actions.groupby('ex_date')}
    quantities={};marks={};receivables=[];cash=1.;previous_nav=1.;previous_claim=0.
    registered={};paid={};claims={};confirmed=set();lapsed=set()
    sessions=orders=0;maximum=0.
    rate=subscription_fee_bps/10000
    fields=['date','reported_nav','reconstructed_nav','cash','rights_value','rights_price_pnl','stock_price_pnl','dividend_income','stock_cost','subscription_fee','error']
    with (source/'daily_nav.csv').open(newline='',encoding='utf8') as nf,(source/'orders.csv').open(newline='',encoding='utf8') as tf,(source/'rights_ledger.csv').open(newline='',encoding='utf8') as rf,(source/'rights_daily.csv').open(newline='',encoding='utf8') as df,(source/'daily_execution_totals.csv').open(newline='',encoding='utf8') as sf,(target/'daily_reconstruction.csv').open('w',newline='',encoding='utf8') as of:
        nav=csv.DictReader(nf);daily=csv.DictReader(df);totals=csv.DictReader(sf)
        tg=groupby(csv.DictReader(tf),key=lambda r:r['date']);rg=groupby(csv.DictReader(rf),key=lambda r:r['date'])
        trades=next(tg,None);rights=next(rg,None)
        writer=csv.DictWriter(of,fieldnames=fields);writer.writeheader()
        for date,row in zip_longest(dates,nav):
            if date is None or row is None or row['date']!=str(date.date()):
                raise ValueError('rights audit must cover every valid requested session')
            key=row['date']
            bars={s:(float(o),float(p),state,None if t is None else pd.Timestamp(t),bool(b),bool(e))
                  for s,o,p,state,t,b,e in store.connection.execute('SELECT symbol,open,preclose,tradestatus,terminal_date,can_buy,can_sell FROM quotes WHERE date=?',(key,))}
            faults=reference.day_faults_for_rights(date)
            def px(s):
                if s not in bars:raise ValueError('missing positive audit quote')
                op,pre,state,_,_,_=bars[s];v=op if state=='1' and op>0 else pre
                if not np.isfinite(v) or v<=0:raise ValueError('invalid positive audit valuation')
                return v
            def visible(e):return e['result_available_date'] is not None and e['result_available_date']<=date
            def mark(identifier,claim):
                e=events[identifier]
                return claim[0]*px(e['symbol']) if e['outcome']=='success' and visible(e) and date>=e['ex_date'] else claim[1]
            for s in set(quantities)|{events[i]['symbol'] for i in claims if events[i]['outcome']=='success' and visible(events[i]) and date>=events[i]['ex_date']}:
                if s not in bars or s in faults or bars[s][3] is not None and date>=bars[s][3]:
                    raise ValueError('unknown held equity inside valid rights prefix')
            for identifier in claims:
                e=events[identifier]
                if date>=e['expected_result_available_date'] and (not visible(e) or e['outcome'] is None):raise ValueError('unknown rights outcome inside valid prefix')
                if visible(e) and e['outcome'] in ['failed','canceled'] and e['refund_date'] is None:raise ValueError('unknown refund inside valid prefix')
            current_claim=sum(mark(i,c) for i,c in claims.items());rights_pnl=current_claim-previous_claim
            today={r.symbol:r for r in actions[date].itertuples(index=False)} if date in actions else {}
            if set(today)&{events[i]['symbol'] for i in claims}:raise ValueError('unsupported corporate action on locked rights')
            pnl=income=stock_cost=fees=0.
            for s,q in list(quantities.items()):
                e=today.get(s);mult=float(e.share_multiplier) if e else 1.;div=q*float(e.cash_per_share) if e else 0.
                pnl+=q*(mult*px(s)-marks[s]);income+=div;quantities[s]=q*mult
                if div:receivables.append((e.pay_date,div))
            cash+=sum(v for pay,v in receivables if pay<date);receivables=[(pay,v) for pay,v in receivables if pay>=date]
            expected=[]
            def flow(identifier,kind,q=0.,principal=0.,fee=0.,delta=0.,phase='open'):
                expected.append(dict(date=key,phase=phase,event_id=identifier,symbol=events[identifier]['symbol'],kind=kind,quantity=q,principal=principal,fee=fee,cash_delta=delta))
            for identifier,(q,principal) in list(claims.items()):
                e=events[identifier]
                if visible(e) and e['outcome'] in ['failed','canceled']:
                    if date>e['refund_date']:
                        cash+=principal;flow(identifier,'refund',q,principal,delta=principal);del claims[identifier]
                elif visible(e) and e['outcome']=='success' and date>=e['ex_date']:
                    if identifier not in confirmed:flow(identifier,'allocation_confirmed',q,principal);confirmed.add(identifier)
                    if e['tradable_from'] is not None and e['listing_available_date'] is not None and date>=max(e['tradable_from'],e['listing_available_date']):
                        s=e['symbol'];quantities[s]=quantities.get(s,0.)+q
                        flow(identifier,'shares_unlocked',q,principal);del claims[identifier]
            requests=[]
            for identifier,e in sorted(events.items()):
                if policy=='cash_limited_subscribe' and e['offer_available_date']<=date and e['subscription_start']<=date<=e['subscription_end'] and not (visible(e) and e['outcome'] in ['failed','canceled']):
                    q=max(0.,registered.get(identifier,0.)-paid.get(identifier,0.))
                    if q>0:requests.append((identifier,q,float(e['subscription_price'])))
            demand=math.fsum(q*p*(1+rate) for _,q,p in requests)
            fraction=min(1.,cash/demand) if demand else 0.
            for identifier,q,p in requests:
                q*=fraction
                if q<=0:continue
                principal=q*p;fee=principal*rate;fees+=fee;cash-=principal+fee
                old=claims.get(identifier,(0.,0.));claims[identifier]=(old[0]+q,old[1]+principal);paid[identifier]=paid.get(identifier,0.)+q
                flow(identifier,'subscribe',q,principal,fee,-principal-fee)
            if cash<-1e-9:raise ValueError('rights funding borrowed cash')
            cash=max(cash,0.)
            for identifier,e in sorted(events.items()):
                if date>e['subscription_end'] and identifier not in lapsed:
                    q=max(0.,registered.get(identifier,0.)-paid.get(identifier,0.))
                    if q:flow(identifier,'rights_lapsed',q)
                    lapsed.add(identifier)
            day_costs=[];day_amounts=[];filled=blocked=buys=0
            if trades is not None and trades[0]<key:raise ValueError('trade outside valid calendar')
            if trades is not None and trades[0]==key:
                for trade in trades[1]:
                    if trade['side'] not in ['buy','sell'] or trade['status'] not in ['filled','blocked']:raise ValueError('unknown observed order')
                    sell=trade['side']=='sell';s=trade['symbol'];amount=float(trade['notional']);cost=float(trade['cost']);tax=float(trade['stamp_duty']);taxrate=stamp_duty_rate(date,None) if sell else 0.
                    if not np.isfinite([amount,cost,tax]).all() or min(amount,cost,tax)<0 or abs(float(trade['stamp_duty_bps'])-taxrate*10000)>1e-12:raise ValueError('invalid observed trade amount or dated tax')
                    if trade['status']=='blocked':
                        if cost or tax:raise ValueError('blocked order charged cost')
                        blocked+=1
                    else:
                        if s not in bars or bars[s][2]!='1' or bars[s][0]<=0 or not bars[s][5 if sell else 4] or not sell and bars[s][3] is not None and date>=bars[s][3]:raise ValueError('unexecutable observed fill')
                        fee=amount*.0008;expected_tax=amount*taxrate
                        if abs(tax-expected_tax)>1e-12 or abs(cost-fee-expected_tax)>1e-12:raise ValueError('observed execution cost differs')
                        sign=-1 if sell else 1;quantities[s]=quantities.get(s,0.)+sign*amount/bars[s][0];cash-=sign*amount+fee+expected_tax
                        stock_cost+=fee+expected_tax;day_costs.append(cost);day_amounts.append(amount);filled+=1;buys+=not sell
                        if quantities[s]<-1e-10:raise ValueError('negative reconstructed shares')
                    orders+=1
                trades=next(tg,None)
            quantities={s:q for s,q in quantities.items() if q>1e-12}
            for identifier,e in sorted(events.items()):
                if e['record_date']==date:
                    q=quantities.get(e['symbol'],0.)*float(e['offer_ratio']);registered[identifier]=q
                    flow(identifier,'registry_entitlement',q,phase='close')
            observed=[]
            if rights is not None and rights[0]<key:raise ValueError('rights flow outside calendar')
            if rights is not None and rights[0]==key:observed=list(rights[1]);rights=next(rg,None)
            if len(observed)!=len(expected):raise ValueError('missing or extra rights flow')
            for actual,wanted in zip(observed,expected):
                for name,value in wanted.items():
                    if isinstance(value,str):equal=actual[name]==value
                    else:equal=np.isfinite(float(actual[name])) and abs(float(actual[name])-value)<=1e-12
                    if not equal:raise ValueError('rights flow differs: '+name)
            rights_value=sum(mark(i,c) for i,c in claims.items())
            daily_row=next(daily,None);total=next(totals,None)
            wanted_daily=[rights_value,fees,sum(r['principal'] for r in expected if r['kind']=='subscribe'),sum(r['principal'] for r in expected if r['kind']=='refund'),len(claims)]
            if daily_row is None or daily_row['date']!=key or not np.allclose([float(daily_row[k]) for k in ['rights_value','subscription_fee','subscribed_principal','refunded_principal','locked_claims']],wanted_daily,rtol=0,atol=1e-12):raise ValueError('rights daily totals differ')
            if total is None or total['date']!=key or not np.allclose([float(total['cost']),float(total['notional'])],[math.fsum(day_costs),math.fsum(day_amounts)],rtol=0,atol=1e-12) or [int(total[n]) for n in ['filled_orders','blocked_orders','filled_buys']]!=[filled,blocked,buys]:raise ValueError('stock execution totals differ')
            if cash<-1e-9:raise ValueError('negative reconstructed cash')
            cash=max(cash,0.);marks={s:px(s) for s in quantities};pending=sum(v for _,v in receivables)
            wealth=cash+pending+rights_value+sum(q*marks[s] for s,q in quantities.items())
            reported=float(row['nav']);error=reported-previous_nav-(pnl+rights_pnl+income-stock_cost-fees)
            if not np.allclose([wealth,cash,pending],[reported,float(row['cash']),float(row['dividend_receivable'])],rtol=0,atol=1e-10) or abs(error)>1e-10 or int(row['positions'])!=len(quantities):raise ValueError('rights account NAV/cash/shares/PnL mismatch')
            maximum=max(maximum,abs(error),abs(wealth-reported));sessions+=1
            writer.writerow(dict(date=key,reported_nav=reported,reconstructed_nav=wealth,cash=cash,rights_value=rights_value,rights_price_pnl=rights_pnl,stock_price_pnl=pnl,dividend_income=income,stock_cost=stock_cost,subscription_fee=fees,error=error))
            previous_nav=reported;previous_claim=rights_value
        if trades is not None or rights is not None or next(daily,None) is not None or next(totals,None) is not None:raise ValueError('ledger beyond valid prefix')
    if expected_holdings is not None and (set(expected_holdings)!=set(quantities) or any(abs(quantities[s]-expected_holdings[s])>1e-12 for s in quantities)):raise ValueError('stopped ordinary shares changed')
    if expected_rights_state is not None:
        for name,actual in [('registered_rights',registered),('subscribed_rights',paid)]:
            wanted=expected_rights_state[name]
            if set(wanted)!=set(actual) or any(abs(actual[k]-wanted[k])>1e-12 for k in actual):raise ValueError('stopped rights state differs')
        wanted=expected_rights_state['locked_claims']
        if set(wanted)!=set(claims) or any(wanted[i]['symbol']!=events[i]['symbol'] or not np.allclose([wanted[i]['quantity'],wanted[i]['principal']],c,rtol=0,atol=1e-12) for i,c in claims.items()):raise ValueError('stopped locked claims differ')
    return dict(status='valid_rights_prefix_reconciled' if stop_date else 'complete_rights_account_reconciled',sessions=sessions,orders_checked=orders,max_absolute_reconciliation_error=maximum,full_period_performance_verified=stop_date is None,stopped_state_checked=expected_holdings is not None and expected_rights_state is not None)

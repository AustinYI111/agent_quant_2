"""Independent pending-listing research reconstruction from SQL and original targets.

Never imports or calls either producer wallet, engine or target-order helpers.
Validates arithmetic and source-bound listing dates, not actual broker fills.
"""
from pathlib import Path
from itertools import groupby, zip_longest
import csv, math, hashlib
from contextlib import closing
import numpy as np
import pandas as pd
from .costs import stamp_duty_rate



def _schedule_digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def _audit_schedule(path, calendar):
    """Independent bounded-memory schedule parser; no producer helpers."""
    previous=None
    with Path(path).open(newline='',encoding='utf8') as stream:
        reader=csv.DictReader(stream)
        if not {'date','symbol','target_weight'}.issubset(reader.fieldnames or []):raise ValueError('audit schedule schema required')
        for raw_date,rows in groupby(reader,key=lambda r:r['date']):
            signal=pd.Timestamp(raw_date)
            if pd.isna(signal) or signal.tz is not None or signal!=signal.normalize() or signal not in calendar or previous is not None and signal<=previous:raise ValueError('ordered audit signal sessions required')
            previous=signal;targets={}
            for row in rows:
                symbol=row['symbol'];weight=float(row['target_weight'])
                if not symbol or symbol!=symbol.strip() or symbol in targets or not math.isfinite(weight) or not 0<=weight<=1:raise ValueError('invalid audit target keys or weights')
                if 'available_date' in row:
                    available=pd.Timestamp(row['available_date'])
                    if pd.isna(available) or available.tz is not None or available!=available.normalize() or available>signal:raise ValueError('unavailable audit ranking')
                targets[symbol]=weight
            if math.fsum(targets.values())>1+1e-12:raise ValueError('leveraged audit targets')
            index=calendar.searchsorted(signal,side='right')
            if index<len(calendar):yield calendar[index],targets


def reconstruct_pending_listing_account(store, reference, listing_book, folder, first, last, out, policy,
                               stop_date=None, expected_holdings=None,
                               expected_rights_state=None, subscription_fee_bps=3.,eligible_symbols=None,expected_ordinary_state=None,*,schedule_path,expected_schedule_sha256):
    if policy not in ['cash_limited_subscribe','decline']:
        raise ValueError('explicit rights audit policy required')
    if _schedule_digest(schedule_path)!=expected_schedule_sha256:raise ValueError('original audit schedule hash differs')
    for _ in _audit_schedule(schedule_path,store.calendar):pass
    listing_book.verify_sources()
    listings=dict(listing_book.events)
    source=Path(folder);target=Path(out)
    if target.exists() or target.resolve().is_relative_to(source.resolve()):
        raise ValueError('fresh separate rights reconstruction directory required')
    target.mkdir(parents=True)
    dates=store.calendar[(store.calendar>=pd.Timestamp(first))&(store.calendar<=pd.Timestamp(last))]
    if stop_date is not None:dates=dates[dates<pd.Timestamp(stop_date)]
    events={e['event_id']:e for e in reference.events if eligible_symbols is None or e['symbol'] in eligible_symbols}
    actions={d:g for d,g in reference.actions.groupby('ex_date')}
    quantities={};marks={};receivables=[];cash=1.;previous_nav=1.;previous_claim=0.
    registered={};paid={};claims={};confirmed=set();lapsed=set()
    bonus_claims={};bonus_registry={};previous_bonus=0.
    sessions=orders=0;maximum=0.
    rate=subscription_fee_bps/10000
    fields=['date','reported_nav','reconstructed_nav','cash','rights_value','ordinary_locked_value','ordinary_locked_price_pnl','rights_price_pnl','stock_price_pnl','dividend_income','stock_cost','subscription_fee','error']
    with closing(_audit_schedule(schedule_path,store.calendar)) as schedule, (source/'daily_nav.csv').open(newline='',encoding='utf8') as nf,(source/'orders.csv').open(newline='',encoding='utf8') as tf,(source/'rights_ledger.csv').open(newline='',encoding='utf8') as rf,(source/'rights_daily.csv').open(newline='',encoding='utf8') as df,(source/'daily_execution_totals.csv').open(newline='',encoding='utf8') as sf,(source/'ordinary_ledger.csv').open(newline='',encoding='utf8') as bf,(target/'daily_reconstruction.csv').open('w',newline='',encoding='utf8') as of:
        next_target=next(schedule,None)
        nav=csv.DictReader(nf);daily=csv.DictReader(df);totals=csv.DictReader(sf)
        tg=groupby(csv.DictReader(tf),key=lambda r:r['date']);rg=groupby(csv.DictReader(rf),key=lambda r:r['date'])
        bg=groupby(csv.DictReader(bf),key=lambda r:r['date'])
        bonus=next(bg,None)
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
            for s in set(quantities)|{s for (s,d) in bonus_claims}|{events[i]['symbol'] for i in claims if events[i]['outcome']=='success' and visible(events[i]) and date>=events[i]['ex_date']}:
                if s not in bars or s in faults or bars[s][3] is not None and date>=bars[s][3]:
                    raise ValueError('unknown held equity inside valid rights prefix')
            for identifier in claims:
                e=events[identifier]
                if date>=e['expected_result_available_date'] and (not visible(e) or e['outcome'] is None):raise ValueError('unknown rights outcome inside valid prefix')
                if visible(e) and e['outcome'] in ['failed','canceled'] and e['refund_date'] is None:raise ValueError('unknown refund inside valid prefix')
            current_claim=sum(mark(i,c) for i,c in claims.items());rights_pnl=current_claim-previous_claim
            today={r.symbol:r for r in actions[date].itertuples(index=False)} if date in actions else {}
            if set(today)&{events[i]['symbol'] for i in claims}:raise ValueError('unsupported corporate action on locked rights')
            if len(today)!=(len(actions[date]) if date in actions else 0):raise ValueError('duplicate ordinary audit action')
            if set(today)&{s for s,d in bonus_claims}:raise ValueError('unsupported action on locked ordinary shares')
            rights_dates={e['symbol'] for e in events.values() if e['record_date']==date or e.get('ex_date')==date}
            if rights_dates&{s for s,d in bonus_claims}:raise ValueError('rights event on locked ordinary shares')
            current_bonus=sum(q*px(s) for (s,d),q in bonus_claims.items());bonus_pnl=current_bonus-previous_bonus
            bonus_expected=[]
            pnl=income=stock_cost=fees=0.
            for s,q in list(quantities.items()):
                e=today.get(s);mult=float(e.share_multiplier) if e else 1.;div=q*float(e.cash_per_share) if e else 0.
                if mult<1 or not np.isfinite(mult):raise ValueError('unsupported ordinary share reduction')
                k=(s,date);term=listings.get(k)
                if term is not None and (e is None or mult!=term.share_multiplier or float(e.cash_per_share)!=term.cash_per_share):raise ValueError('ordinary listing economics differ')
                if mult>1:
                    if term is None or term.available_date>date:raise ValueError('unknown ordinary listing inside valid prefix')
                    if k not in bonus_registry or abs(bonus_registry[k]-q)>1e-12:raise ValueError('ordinary registry differs')
                    if s in rights_dates:raise ValueError('overlapping new bonus and rights event')
                    extra=q*(mult-1);bonus_claims[k]=extra
                    bonus_expected.append(dict(date=key,symbol=s,ex_date=key,kind='bonus_claim_recognized',quantity=extra))
                pnl+=q*(mult*px(s)-marks[s]);income+=div
                if div:receivables.append((e.pay_date,div))
            due={s for (s,d),q in bonus_registry.items() if d==date and q>0}
            if not due.issubset(today):raise ValueError('ordinary action missing from audit prefix')
            for k,q in list(bonus_claims.items()):
                if listings[k].tradable_from is not None and date>=listings[k].tradable_from:
                    quantities[k[0]]=quantities.get(k[0],0.)+q;del bonus_claims[k]
                    bonus_expected.append(dict(date=key,symbol=k[0],ex_date=str(k[1].date()),kind='bonus_shares_unlocked',quantity=q))
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
                    if q>0:requests.append((identifier,q,float(e['subscription_price']),float(e['subscription_fee_bps'])/10000 if e.get('subscription_fee_bps') is not None else rate))
            demand=math.fsum(q*p*(1+fee_rate) for _,q,p,fee_rate in requests)
            fraction=min(1.,cash/demand) if demand else 0.
            for identifier,q,p,fee_rate in requests:
                q*=fraction
                if q<=0:continue
                principal=q*p;fee=principal*fee_rate;fees+=fee;cash-=principal+fee
                old=claims.get(identifier,(0.,0.));claims[identifier]=(old[0]+q,old[1]+principal);paid[identifier]=paid.get(identifier,0.)+q
                flow(identifier,'subscribe',q,principal,fee,-principal-fee)
            if cash<-1e-9:raise ValueError('rights funding borrowed cash')
            cash=max(cash,0.)
            for identifier,e in sorted(events.items()):
                if date>e['subscription_end'] and identifier not in lapsed:
                    q=max(0.,registered.get(identifier,0.)-paid.get(identifier,0.))
                    if q:flow(identifier,'rights_lapsed',q)
                    lapsed.add(identifier)
            # This check uses the original hashed schedule and independently
            # rebuilt cash/shares/claims, before reading observed trades. A
            # producer cannot omit a required uncertain sale and still pass.
            while next_target is not None and next_target[0]<date:next_target=next(schedule,None)
            if next_target is not None and next_target[0]==date:
                weights=next_target[1];uncertain={};known_locked={}
                for (s,d),q in bonus_claims.items():
                    values=uncertain if listings[(s,d)].tradable_from is None else known_locked
                    values[s]=values.get(s,0.)+q*px(s)
                for identifier,claim in claims.items():
                    s=events[identifier]['symbol'];known_locked[s]=known_locked.get(s,0.)+mark(identifier,claim)
                equity=cash+sum(v for _,v in receivables)+sum(q*px(s) for s,q in quantities.items())+sum(mark(i,c) for i,c in claims.items())+sum(q*px(s) for (s,d),q in bonus_claims.items())
                for s,value in uncertain.items():
                    if weights.get(s,0.)*equity-known_locked.get(s,0.)<value-1e-12:raise ValueError('original target needs unresolved bonus sale')
                next_target=next(schedule,None)
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
                        sign=-1 if sell else 1;quantities[s]=quantities.get(s,0.)+sign*amount/bars[s][0]
                        # Preserve the declared gross settlement grouping. Summing
                        # amount and fee separately leaves sub-ulp cash that can
                        # invent extra subscriptions on a later cash-limited day.
                        # Amounts and dated rates above are independently checked;
                        # no reported cash/claim or producer wallet is imported.
                        cash+=amount*(1-(.0008+taxrate)) if sell else -amount*(1+.0008)
                        stock_cost+=fee+expected_tax;day_costs.append(cost);day_amounts.append(amount);filled+=1;buys+=not sell
                        if quantities[s]<-1e-10:raise ValueError('negative reconstructed shares')
                    orders+=1
                trades=next(tg,None)
            quantities={s:q for s,q in quantities.items() if q>1e-12}
            for identifier,e in sorted(events.items()):
                if e['record_date']==date:
                    q=quantities.get(e['symbol'],0.)*float(e['offer_ratio']);registered[identifier]=q
                    flow(identifier,'registry_entitlement',q,phase='close')
            for k,term in listings.items():
                if term.record_date==date:
                    if term.symbol in {s for s,d in bonus_claims} or term.symbol in {events[i]['symbol'] for i in claims}:raise ValueError('ordinary registry with locked claims')
                    bonus_registry[k]=quantities.get(term.symbol,0.)
            observed_bonus=[]
            if bonus is not None and bonus[0]<key:raise ValueError('ordinary flow outside calendar')
            if bonus is not None and bonus[0]==key:observed_bonus=list(bonus[1]);bonus=next(bg,None)
            if len(observed_bonus)!=len(bonus_expected):raise ValueError('missing or extra ordinary flow')
            for actual,wanted in zip(observed_bonus,bonus_expected):
                for name,value in wanted.items():
                    equal=actual[name]==value if isinstance(value,str) else np.isfinite(float(actual[name])) and abs(float(actual[name])-value)<=1e-12
                    if not equal:raise ValueError('ordinary flow differs: '+name)
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
            bonus_value=sum(q*px(s) for (s,d),q in bonus_claims.items())
            wealth=cash+pending+rights_value+bonus_value+sum(q*marks[s] for s,q in quantities.items())
            reported=float(row['nav']);error=reported-previous_nav-(pnl+rights_pnl+bonus_pnl+income-stock_cost-fees)
            if not np.allclose([wealth,cash,pending],[reported,float(row['cash']),float(row['dividend_receivable'])],rtol=0,atol=1e-10) or abs(error)>1e-10 or int(row['positions'])!=len(quantities):raise ValueError('rights account NAV/cash/shares/PnL mismatch')
            maximum=max(maximum,abs(error),abs(wealth-reported));sessions+=1
            writer.writerow(dict(date=key,reported_nav=reported,reconstructed_nav=wealth,cash=cash,rights_value=rights_value,ordinary_locked_value=bonus_value,ordinary_locked_price_pnl=bonus_pnl,rights_price_pnl=rights_pnl,stock_price_pnl=pnl,dividend_income=income,stock_cost=stock_cost,subscription_fee=fees,error=error))
            previous_nav=reported;previous_claim=rights_value;previous_bonus=bonus_value
        if bonus is not None or trades is not None or rights is not None or next(daily,None) is not None or next(totals,None) is not None:raise ValueError('ledger beyond valid prefix')
    if expected_holdings is not None and (set(expected_holdings)!=set(quantities) or any(abs(quantities[s]-expected_holdings[s])>1e-12 for s in quantities)):raise ValueError('stopped ordinary shares changed')
    if expected_rights_state is not None:
        if 'event_subscription_fee_bps' in expected_rights_state:
            actual_fees={i:float(e['subscription_fee_bps']) if e.get('subscription_fee_bps') is not None else subscription_fee_bps for i,e in events.items()}
            if set(actual_fees)!=set(expected_rights_state['event_subscription_fee_bps']) or any(abs(actual_fees[i]-expected_rights_state['event_subscription_fee_bps'][i])>1e-12 for i in actual_fees):raise ValueError('stopped per-event fee state differs')
        for name,actual in [('registered_rights',registered),('subscribed_rights',paid)]:
            wanted=expected_rights_state[name]
            if set(wanted)!=set(actual) or any(abs(actual[k]-wanted[k])>1e-12 for k in actual):raise ValueError('stopped rights state differs')
        wanted=expected_rights_state['locked_claims']
        if set(wanted)!=set(claims) or any(wanted[i]['symbol']!=events[i]['symbol'] or not np.allclose([wanted[i]['quantity'],wanted[i]['principal']],c,rtol=0,atol=1e-12) for i,c in claims.items()):raise ValueError('stopped locked claims differ')
    if expected_ordinary_state is not None:
        actual_registry={f'{s}/{d.date()}':q for (s,d),q in bonus_registry.items()}
        wanted=expected_ordinary_state['registered']
        if set(actual_registry)!=set(wanted) or any(abs(q-wanted[k])>1e-12 for k,q in actual_registry.items()):raise ValueError('stopped ordinary registry differs')
        wanted=expected_ordinary_state['locked_claims']
        actual={f'{s}/{d.date()}':dict(symbol=s,ex_date=str(d.date()),quantity=q,tradable_from=None if listings[(s,d)].tradable_from is None else str(listings[(s,d)].tradable_from.date())) for (s,d),q in bonus_claims.items()}
        if set(actual)!=set(wanted):raise ValueError('stopped ordinary claim keys differ')
        for k,r in actual.items():
            if any(r[n]!=wanted[k][n] for n in ['symbol','ex_date','tradable_from']) or abs(r['quantity']-wanted[k]['quantity'])>1e-12:raise ValueError('stopped ordinary claim differs')
    if _schedule_digest(schedule_path)!=expected_schedule_sha256:raise ValueError('audit schedule changed during reconstruction')
    listing_book.verify_sources()
    return dict(status='pending_listing_prefix_reconciled' if stop_date else 'pending_listing_requested_interval_reconciled',sessions=sessions,orders_checked=orders,max_absolute_reconciliation_error=maximum,full_period_performance_verified=False,execution_profile='unresolved_listing_target_invariant_v1',listing_dates_certified=False,original_schedule_sha256=expected_schedule_sha256,stopped_state_checked=expected_holdings is not None and expected_rights_state is not None and expected_ordinary_state is not None,ordinary_source_hashes=dict(listing_book.source_hashes),audited_rights_event_ids=sorted(events))

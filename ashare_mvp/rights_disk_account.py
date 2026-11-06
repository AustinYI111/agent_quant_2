"""Separate paid-rights account; the original raw-share engine stays unchanged.

Ordinary order arithmetic follows disk_account; rights subscriptions have their
own ledger and costs. Locked rights reduce desired ordinary-stock exposure and
cannot be sold before the source-gated listing date. Unsupported corporate
actions on positive locked claims stop with a valid prefix, never lose claims.
"""
from pathlib import Path
import csv,hashlib,json
import numpy as np
import pandas as pd
from .disk_market import exchange_dates,iter_groups
from .phase2_protocol import sha256
from .costs import stamp_duty_rate
from .rights_wallet import RightsWallet, UnresolvedRightsOutcome, RIGHTS_LEDGER_COLUMNS
from .rights_reference_audit import RightsReferenceAudit
from .disk_account import iter_schedule, schedule_info, DiskHeldValuationError

RIGHTS_DAILY_COLUMNS=['date','rights_value','subscription_fee','subscribed_principal','refunded_principal','locked_claims']


def locked_exposure(wallet,date,prices):
    values={}
    for identifier,claim in wallet.claims.items():
        e=wallet.by_id[identifier]
        value=claim['quantity']*prices[e['symbol']] if e['outcome']=='success' and wallet._result_known(e,date) and date>=e['ex_date'] else claim['principal']
        values[e['symbol']]=values.get(e['symbol'],0.)+value
    return values


class RightsAccountStop(DiskHeldValuationError):
    def __init__(self,reason,symbol,date,holdings,folder,nav_rows,order_rows,wallet):
        super().__init__(reason,symbol,date,holdings,folder,nav_rows,order_rows)
        self.rights_state=wallet.snapshot()



NAV_COLUMNS=['date','nav','cash','positions','dividend_receivable','return_basis']
ORDER_COLUMNS=['date','symbol','side','notional','stamp_duty_bps','stamp_duty','cost','status']
TOTAL_COLUMNS=['date','cost','notional','filled_orders','blocked_orders','filled_buys']


def simulate_rights_account(store,reference,schedule_path,calendar,config,out,policy,subscription_commission_bps=3.):
    """Same original next-open targets, costs, shares and cash; full endpoint.

    Memory scales with an issuer CSV chunk / one session / positive holdings,
    never the number of historical quote or trade rows. Missing positive held
    prices, unknown adjustments and terminal values stop with intact holdings.
    """
    if not isinstance(reference,RightsReferenceAudit):raise ValueError('typed rights reference audit required')
    cal=exchange_dates(calendar)
    if not cal.equals(store.calendar[store.calendar<=cal[-1]]):raise ValueError('account calendar must be original prefix, never a shortened/reanchored grid')
    if reference.manifest['quote_database_sha256']!=store.manifest['database_sha256']:raise ValueError('reference audit and quote store differ')
    path=Path(schedule_path);info=schedule_info(path,cal);target=Path(out).resolve()
    if target.exists() or any(target.is_relative_to(p.resolve()) or p.resolve().is_relative_to(target) for p in [store.path,reference.path,path]):raise ValueError('fresh account directory outside inputs required')
    target.mkdir(parents=True)
    exec_dates={cal[cal.searchsorted(d,side='right')]:d for d in info['dates'] if cal.searchsorted(d,side='right')<len(cal)}
    for date in cal[cal>=min(exec_dates)] if exec_dates else []:
        if not store.has_day(date,info['symbols']):raise ValueError('missing requested portfolio market session')
    events=reference.actions[reference.actions.symbol.isin(info['symbols'])&reference.actions.ex_date.between(info['dates'].min(),cal[-1])] if len(info['dates']) else reference.actions.iloc[:0]
    event_days={d:g for d,g in events.groupby('ex_date')}
    wallet=RightsWallet([e for e in reference.events if e['symbol'] in info['symbols']],policy,subscription_commission_bps)
    scheduled=iter(iter_schedule(path));next_target=next(scheduled,None)
    cash=1.;holdings={};receivables=[];nav_rows=order_rows=max_day_orders=max_day_quotes=0
    buy_rate=(config.commission_bps+config.slippage_bps)/10000
    if not np.isfinite(buy_rate) or buy_rate<0:raise ValueError('nonnegative execution assumptions required')
    with (target/'daily_nav.csv').open('w',newline='',encoding='utf8') as nf,(target/'orders.csv').open('w',newline='',encoding='utf8') as tf,(target/'daily_execution_totals.csv').open('w',newline='',encoding='utf8') as sf,(target/'rights_ledger.csv').open('w',newline='',encoding='utf8') as rf,(target/'rights_daily.csv').open('w',newline='',encoding='utf8') as df:
        rw=csv.DictWriter(rf,fieldnames=RIGHTS_LEDGER_COLUMNS);dw=csv.DictWriter(df,fieldnames=RIGHTS_DAILY_COLUMNS)
        nw=csv.DictWriter(nf,fieldnames=NAV_COLUMNS);tw=csv.DictWriter(tf,fieldnames=ORDER_COLUMNS);sw=csv.DictWriter(sf,fieldnames=TOTAL_COLUMNS)
        for writer in [nw,tw,sw,rw,dw]:writer.writeheader()
        for date in cal[cal>=min(exec_dates)] if exec_dates else []:
            bars=store.day(date,info['symbols']);max_day_quotes=max(max_day_quotes,len(bars));faults=reference.day_faults_for_rights(date)
            holdings={s:q for s,q in holdings.items() if q>1e-12}
            try:wallet.check_outcomes(date)
            except UnresolvedRightsOutcome as error:
                for stream in [nf,tf,sf,rf,df]:stream.flush()
                raise RightsAccountStop('rights_outcome',error.symbol,date,holdings,target,nav_rows,order_rows,wallet) from error
            for s in set(holdings)|wallet.valuation_symbols(date):
                reason='missing' if s not in bars else ('reference' if s in faults else ('terminal' if bars[s]['terminal_date'] is not None and date>=bars[s]['terminal_date'] else None))
                if reason:

                    for stream in [nf,tf,sf,rf,df]:stream.flush()
                    raise RightsAccountStop(reason,s,date,holdings,target,nav_rows,order_rows,wallet)
            prices={s:b['open'] if b['tradestatus']=='1' and b['open']>0 else b['preclose'] for s,b in bars.items()}
            if date in event_days:
                locked_symbols={wallet.by_id[i]['symbol'] for i,c in wallet.claims.items() if c['quantity']>0}
                overlap=sorted(locked_symbols & set(event_days[date].symbol))
                if overlap:
                    for stream in [nf,tf,sf,rf,df]:stream.flush()
                    raise RightsAccountStop('rights_corporate_action_overlap',overlap[0],date,holdings,target,nav_rows,order_rows,wallet)
            if date in event_days:
                for e in event_days[date].itertuples(index=False):
                    shares=holdings.get(e.symbol,0.);entitlement=shares*e.cash_per_share
                    if entitlement:receivables.append((e.pay_date,entitlement))
                    if shares:holdings[e.symbol]=shares*e.share_multiplier
            cash+=sum(amount for day,amount in receivables if day<date);receivables=[(day,amount) for day,amount in receivables if day>=date]
            cash,rights_value,subscription_fee,rights_rows=wallet.open(date,cash,holdings,prices)
            locked_values=locked_exposure(wallet,date,prices)
            pending=sum(amount for _,amount in receivables);equity=cash+pending+sum(q*prices[s] for s,q in holdings.items())+rights_value;day_orders=[]
            tax_rate=stamp_duty_rate(date,config.sell_tax_bps);sell_rate=buy_rate+tax_rate
            def order(symbol,side,amount,cost,status,stamp=0.,stamp_bps=0.):
                day_orders.append(dict(date=date,symbol=symbol,side=side,notional=amount,stamp_duty_bps=stamp_bps,stamp_duty=stamp,cost=cost,status=status))
            if date in exec_dates:
                while next_target is not None and next_target[0]<exec_dates[date]:next_target=next(scheduled,None)
                if next_target is None or next_target[0]!=exec_dates[date]:raise ValueError('schedule changed during streamed execution')
                targets=next_target[1]
                for s in sorted(holdings):
                    price=prices[s];value=holdings[s]*price;sale=max(0.,value-max(0.,targets.get(s,0.)*equity-locked_values.get(s,0.)))
                    if sale>1e-12:
                        if bars[s]['can_sell'] and price>0:
                            holdings[s]-=sale/price;cash+=sale*(1-sell_rate);order(s,'sell',sale,sale*sell_rate,'filled',sale*tax_rate,tax_rate*10000)
                        else:order(s,'sell',sale,0.,'blocked',0.,tax_rate*10000)
                buys={}
                for s,weight in targets.items():
                    if s not in bars or bars[s]['terminal_date'] is not None and date>=bars[s]['terminal_date']:
                        order(s,'buy',max(0.,weight*equity),0.,'blocked');continue
                    price=prices[s];amount=max(0.,weight*equity-holdings.get(s,0.)*price-locked_values.get(s,0.))
                    if amount>1e-12:
                        if bars[s]['can_buy'] and price>0:buys[s]=amount
                        else:order(s,'buy',amount,0.,'blocked')
                scale=min(1.,cash/(sum(buys.values())*(1+buy_rate))) if buys else 0.
                for s,amount in buys.items():
                    amount*=scale;holdings[s]=holdings.get(s,0.)+amount/prices[s];cash-=amount*(1+buy_rate);order(s,'buy',amount,amount*buy_rate,'filled')
                next_target=next(scheduled,None)
            if cash<-1e-9:raise AssertionError('negative cash')
            cash=max(cash,0.);holdings={s:q for s,q in holdings.items() if q>1e-12};equity=cash+pending+sum(q*prices[s] for s,q in holdings.items())+wallet.value(date,prices)
            rights_rows+=wallet.close(date,holdings)
            rw.writerows(rights_rows)
            dw.writerow(dict(date=str(date.date()),rights_value=wallet.value(date,prices),subscription_fee=subscription_fee,subscribed_principal=sum(r['principal'] for r in rights_rows if r['kind']=='subscribe'),refunded_principal=sum(r['principal'] for r in rights_rows if r['kind']=='refund'),locked_claims=len(wallet.claims)))
            nw.writerow(dict(date=str(date.date()),nav=equity,cash=cash,positions=len(holdings),dividend_receivable=pending,return_basis='raw_share_account_gross_dividends'));nav_rows+=1
            if day_orders:
                frame=pd.DataFrame(day_orders);filled=frame[frame.status.eq('filled')]
                cost=filled.groupby('date').cost.sum().iloc[0] if not filled.empty else 0.;notional=filled.groupby('date').notional.sum().iloc[0] if not filled.empty else 0.
                for r in day_orders:r['date']=str(date.date());tw.writerow(r)
                total=dict(date=str(date.date()),cost=cost,notional=notional,filled_orders=len(filled),blocked_orders=int(frame.status.eq('blocked').sum()),filled_buys=int(filled.side.eq('buy').sum()))
            else:total=dict(date=str(date.date()),cost=0.,notional=0.,filled_orders=0,blocked_orders=0,filled_buys=0)
            sw.writerow(total);order_rows+=len(day_orders);max_day_orders=max(max_day_orders,len(day_orders))
    if sha256(path)!=info['sha256']:raise ValueError('schedule changed during execution')
    return dict(status='complete_requested_calendar_account' if exec_dates else 'no_executable_schedule',sessions=nav_rows,orders=order_rows,first_execution=None if not exec_dates else str(min(exec_dates).date()),requested_end=str(cal[-1].date()),max_day_quotes=max_day_quotes,max_day_orders=max_day_orders,history_retained_in_memory=False,rights_state=wallet.snapshot(),rights_coverage='documented cases only; full offer universe unverified',holdings={s:float(q) for s,q in holdings.items()})

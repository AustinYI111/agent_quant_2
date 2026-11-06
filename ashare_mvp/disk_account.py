"""One-session raw-share accounting; order history is written, never accumulated."""
from pathlib import Path
import csv,hashlib,json
import numpy as np
import pandas as pd
from .disk_market import exchange_dates,iter_groups
from .phase2_protocol import sha256
from .costs import stamp_duty_rate


NAV_COLUMNS=['date','nav','cash','positions','dividend_receivable','return_basis']
ORDER_COLUMNS=['date','symbol','side','notional','stamp_duty_bps','stamp_duty','cost','status']
TOTAL_COLUMNS=['date','cost','notional','filled_orders','blocked_orders','filled_buys']


def iter_schedule(path):
    header=pd.read_csv(path,nrows=0).columns
    required=['date','symbol','target_weight'];columns=required+(['available_date'] if 'available_date' in header else [])
    if not set(required).issubset(header):raise ValueError('schedule schema incomplete')
    for value,frame in iter_groups(path,'date',columns):
        date=exchange_dates([value])[0]
        if frame.symbol.isna().any() or frame.symbol.astype(str).str.strip().eq('').any() or frame.symbol.duplicated().any():raise ValueError('unique intended issuer keys required')
        weights=pd.to_numeric(frame.target_weight,errors='raise')
        if not np.isfinite(weights).all() or weights.lt(0).any() or weights.gt(1).any() or weights.sum()>1+1e-12:raise ValueError('finite nonnegative unlevered targets required')
        if 'available_date' in frame:
            available=pd.to_datetime(frame.available_date,format='mixed')
            if available.isna().any() or available.dt.tz is not None or not available.eq(available.dt.normalize()).all() or available.gt(date).any():raise ValueError('unavailable ranking keys')
        yield date,dict(zip(frame.symbol,weights.astype(float)))


def schedule_info(path,calendar):
    cal=exchange_dates(calendar);dates=[];symbols=set();counts={}
    for date,targets in iter_schedule(path):
        if date not in cal:raise ValueError('decision outside original exchange calendar')
        dates.append(date);symbols.update(targets);counts[str(date.date())]=len(targets)
    return dict(dates=pd.DatetimeIndex(dates),symbols=symbols,counts=counts,sha256=sha256(path))


class DiskHeldValuationError(ValueError):
    """Paths to flushed valid prefixes, rather than loading full order history."""
    def __init__(self,reason,symbol,date,holdings,folder,nav_rows,order_rows):
        self.reason=reason;self.symbol=symbol;self.date=pd.Timestamp(date);self.holdings={s:float(q) for s,q in holdings.items()};self.folder=Path(folder);self.nav_rows=nav_rows;self.order_rows=order_rows
        super().__init__(f'unresolved positive held {reason}: {symbol} {self.date.date()}')


def simulate_disk_account(store,reference,schedule_path,calendar,config,out):
    """Same original next-open targets, costs, shares and cash; full endpoint.

    Memory scales with an issuer CSV chunk / one session / positive holdings,
    never the number of historical quote or trade rows. Missing positive held
    prices, unknown adjustments and terminal values stop with intact holdings.
    """
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
    scheduled=iter(iter_schedule(path));next_target=next(scheduled,None)
    cash=1.;holdings={};receivables=[];nav_rows=order_rows=max_day_orders=max_day_quotes=0
    buy_rate=(config.commission_bps+config.slippage_bps)/10000
    if not np.isfinite(buy_rate) or buy_rate<0:raise ValueError('nonnegative execution assumptions required')
    with (target/'daily_nav.csv').open('w',newline='',encoding='utf8') as nf,(target/'orders.csv').open('w',newline='',encoding='utf8') as tf,(target/'daily_execution_totals.csv').open('w',newline='',encoding='utf8') as sf:
        nw=csv.DictWriter(nf,fieldnames=NAV_COLUMNS);tw=csv.DictWriter(tf,fieldnames=ORDER_COLUMNS);sw=csv.DictWriter(sf,fieldnames=TOTAL_COLUMNS)
        for writer in [nw,tw,sw]:writer.writeheader()
        for date in cal[cal>=min(exec_dates)] if exec_dates else []:
            bars=store.day(date,info['symbols']);max_day_quotes=max(max_day_quotes,len(bars));faults=reference.day_faults(date)
            holdings={s:q for s,q in holdings.items() if q>1e-12}
            for s in holdings:
                reason='missing' if s not in bars else ('reference' if s in faults else ('terminal' if bars[s]['terminal_date'] is not None and date>=bars[s]['terminal_date'] else None))
                if reason:
                    nf.flush();tf.flush();sf.flush();raise DiskHeldValuationError(reason,s,date,holdings,target,nav_rows,order_rows)
            prices={s:b['open'] if b['tradestatus']=='1' and b['open']>0 else b['preclose'] for s,b in bars.items()}
            if date in event_days:
                for e in event_days[date].itertuples(index=False):
                    shares=holdings.get(e.symbol,0.);entitlement=shares*e.cash_per_share
                    if entitlement:receivables.append((e.pay_date,entitlement))
                    if shares:holdings[e.symbol]=shares*e.share_multiplier
            cash+=sum(amount for day,amount in receivables if day<date);receivables=[(day,amount) for day,amount in receivables if day>=date]
            pending=sum(amount for _,amount in receivables);equity=cash+pending+sum(q*prices[s] for s,q in holdings.items());day_orders=[]
            tax_rate=stamp_duty_rate(date,config.sell_tax_bps);sell_rate=buy_rate+tax_rate
            def order(symbol,side,amount,cost,status,stamp=0.,stamp_bps=0.):
                day_orders.append(dict(date=date,symbol=symbol,side=side,notional=amount,stamp_duty_bps=stamp_bps,stamp_duty=stamp,cost=cost,status=status))
            if date in exec_dates:
                while next_target is not None and next_target[0]<exec_dates[date]:next_target=next(scheduled,None)
                if next_target is None or next_target[0]!=exec_dates[date]:raise ValueError('schedule changed during streamed execution')
                targets=next_target[1]
                for s in sorted(holdings):
                    price=prices[s];value=holdings[s]*price;sale=max(0.,value-targets.get(s,0.)*equity)
                    if sale>1e-12:
                        if bars[s]['can_sell'] and price>0:
                            holdings[s]-=sale/price;cash+=sale*(1-sell_rate);order(s,'sell',sale,sale*sell_rate,'filled',sale*tax_rate,tax_rate*10000)
                        else:order(s,'sell',sale,0.,'blocked',0.,tax_rate*10000)
                buys={}
                for s,weight in targets.items():
                    if s not in bars or bars[s]['terminal_date'] is not None and date>=bars[s]['terminal_date']:
                        order(s,'buy',max(0.,weight*equity),0.,'blocked');continue
                    price=prices[s];amount=max(0.,weight*equity-holdings.get(s,0.)*price)
                    if amount>1e-12:
                        if bars[s]['can_buy'] and price>0:buys[s]=amount
                        else:order(s,'buy',amount,0.,'blocked')
                scale=min(1.,cash/(sum(buys.values())*(1+buy_rate))) if buys else 0.
                for s,amount in buys.items():
                    amount*=scale;holdings[s]=holdings.get(s,0.)+amount/prices[s];cash-=amount*(1+buy_rate);order(s,'buy',amount,amount*buy_rate,'filled')
                next_target=next(scheduled,None)
            if cash<-1e-9:raise AssertionError('negative cash')
            cash=max(cash,0.);holdings={s:q for s,q in holdings.items() if q>1e-12};equity=cash+pending+sum(q*prices[s] for s,q in holdings.items())
            nw.writerow(dict(date=str(date.date()),nav=equity,cash=cash,positions=len(holdings),dividend_receivable=pending,return_basis='raw_share_account_gross_dividends'));nav_rows+=1
            if day_orders:
                frame=pd.DataFrame(day_orders);filled=frame[frame.status.eq('filled')]
                cost=filled.groupby('date').cost.sum().iloc[0] if not filled.empty else 0.;notional=filled.groupby('date').notional.sum().iloc[0] if not filled.empty else 0.
                for r in day_orders:r['date']=str(date.date());tw.writerow(r)
                total=dict(date=str(date.date()),cost=cost,notional=notional,filled_orders=len(filled),blocked_orders=int(frame.status.eq('blocked').sum()),filled_buys=int(filled.side.eq('buy').sum()))
            else:total=dict(date=str(date.date()),cost=0.,notional=0.,filled_orders=0,blocked_orders=0,filled_buys=0)
            sw.writerow(total);order_rows+=len(day_orders);max_day_orders=max(max_day_orders,len(day_orders))
    if sha256(path)!=info['sha256']:raise ValueError('schedule changed during execution')
    return dict(status='complete_requested_calendar_account' if exec_dates else 'no_executable_schedule',sessions=nav_rows,orders=order_rows,first_execution=None if not exec_dates else str(min(exec_dates).date()),requested_end=str(cal[-1].date()),max_day_quotes=max_day_quotes,max_day_orders=max_day_orders,history_retained_in_memory=False,holdings={s:float(q) for s,q in holdings.items()})


def disk_account_metrics(folder,benchmark):
    from .horizon_research import compare_index
    directory=Path(folder);nav=pd.read_csv(directory/'daily_nav.csv',parse_dates=['date'],float_precision='round_trip');totals=pd.read_csv(directory/'daily_execution_totals.csv',parse_dates=['date'],float_precision='round_trip')
    if not nav.date.equals(totals.date):raise ValueError('daily execution totals do not cover complete NAV calendar')
    comparison,metrics=compare_index(nav,pd.DataFrame(),benchmark,'price_index')
    costs=totals.cost.to_numpy();notional=totals.notional.to_numpy();before=nav.nav.to_numpy()+costs
    turnover=np.divide(notional,before,out=np.zeros(len(nav)),where=before>0)
    first=totals.loc[totals.filled_buys.gt(0),'date'].min();years=max((nav.date.iloc[-1]-nav.date.iloc[0]).days/365.25,1/365.25)
    ex_initial=turnover[nav.date.ne(first).to_numpy()] if pd.notna(first) else turnover
    metrics.update(paid_cost_initial_equity_units=float(costs.sum()),two_way_turnover_sum=float(turnover.sum()),one_way_turnover_sum=float(turnover.sum()/2),annual_one_way_turnover=float(turnover.sum()/2/years),one_way_turnover_ex_initial=float(ex_initial.sum()/2),initial_allocation_date=str(first.date()) if pd.notna(first) else None,filled_orders=int(totals.filled_orders.sum()),blocked_orders=int(totals.blocked_orders.sum()))
    comparison['paid_cost']=costs;comparison['two_way_turnover']=turnover
    return comparison,metrics

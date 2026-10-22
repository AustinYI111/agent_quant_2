"""Exact open-to-open research accounting, not a causal alpha explanation."""
import numpy as np
import pandas as pd


DAILY=['date','opening_nav','nav','price_pnl','gross_dividend_entitlement','dividends_paid_cash','receivable_change','commission_cost','slippage_cost','stamp_duty_cost','total_execution_cost','nav_change','reconciliation_error','price_return_contribution','dividend_return_contribution','cost_return_contribution','net_return','unit_beta_market_reference','price_residual_after_market']
POSITIONS=['date','symbol','shares','raw_open_valuation','market_value','portfolio_weight']
ASSETS=['date','symbol','price_pnl','gross_dividend_entitlement','commission_cost','slippage_cost','stamp_duty_cost']


def attribute_open_account(nav,orders,quotes,actions,benchmark=None,commission_bps=3.,slippage_bps=5.):
    """Reconstruct shares/cash/receivables independently from complete inception.

    A dividend is income on ex-date for existing holders; payment moves an
    asset from receivables to cash and is never counted a second time. New
    buyers on ex-date get no earlier holder entitlement. Splits change shares.
    Unit-beta market attribution is a price-index reference, not fitted alpha.
    """
    if nav.empty:return pd.DataFrame(columns=DAILY),pd.DataFrame(columns=POSITIONS),pd.DataFrame(columns=ASSETS)
    if not np.isfinite([commission_bps,slippage_bps]).all() or min(commission_bps,slippage_bps)<0:raise ValueError('known nonnegative execution assumptions required')
    if not {'date','nav','cash','dividend_receivable'}.issubset(nav) or not {'date','symbol','open','preclose','tradestatus'}.issubset(quotes):raise ValueError('raw share account attribution schema incomplete')
    n=nav.copy();p=quotes.copy();a=actions.copy();t=orders.copy()
    for frame in [n,p]:frame['date']=pd.to_datetime(frame.date,format='mixed')
    if n.date.isna().any() or n.date.duplicated().any() or not n.date.is_monotonic_increasing or not n.date.eq(n.date.dt.normalize()).all():raise ValueError('ordered unique account dates required')
    if p[['date','symbol']].isna().any().any() or p.duplicated(['date','symbol']).any():raise ValueError('unique raw account quotes required')
    for column in ['open','preclose']:
        p[column]=pd.to_numeric(p[column],errors='raise')
        if not np.isfinite(p[column]).all() or p[column].lt(0).any():raise ValueError('invalid raw account quote')
    p['tradestatus']=p.tradestatus.astype(str)
    if not p.tradestatus.isin(['0','1']).all():raise ValueError('known raw trading states required')
    for column in ['nav','cash','dividend_receivable']:
        n[column]=pd.to_numeric(n[column],errors='raise')
        if not np.isfinite(n[column]).all() or n[column].lt(0).any():raise ValueError('nonnegative known account assets required')
    for column in ['ex_date','pay_date']:a[column]=pd.to_datetime(a[column],format='mixed')
    if a.duplicated(['symbol','ex_date']).any() or a[['symbol','ex_date']].isna().any().any():raise ValueError('unique actual account actions required')
    for column in ['cash_per_share','share_multiplier']:
        a[column]=pd.to_numeric(a[column],errors='raise')
        if not np.isfinite(a[column]).all() or a[column].lt(0).any():raise ValueError('invalid account entitlement')
    if a.share_multiplier.le(0).any() or (a.cash_per_share.gt(0)&a.pay_date.isna()).any() or a.pay_date.lt(a.ex_date).any():raise ValueError('known payment dates and positive actual share multipliers required')
    if not t.empty:
        required={'date','symbol','status','side','notional','cost','stamp_duty'}
        if not required.issubset(t):raise ValueError('observed fill/cost details required')
        t['date']=pd.to_datetime(t.date,format='mixed')
        for column in ['notional','cost','stamp_duty']:
            t[column]=pd.to_numeric(t[column],errors='raise')
            if not np.isfinite(t[column]).all() or t[column].lt(0).any():raise ValueError('known nonnegative order amounts required')
        if not t.date.isin(n.date).all() or not t.status.isin(['filled','blocked']).all() or not t.side.isin(['buy','sell']).all():raise ValueError('invalid account order')
        if (t.status.eq('blocked')&(t.cost.ne(0)|t.stamp_duty.ne(0))).any():raise ValueError('blocked order cannot incur filled cost')
    b=None
    if benchmark is not None:
        b=benchmark.copy();b['date']=pd.to_datetime(b.date,format='mixed');b['open']=pd.to_numeric(b.open,errors='raise')
        if b.date.duplicated().any() or not np.isfinite(b.open).all() or b.open.le(0).any() or ('code' in b and not b.code.eq('sh.000300').all()):raise ValueError('valid CSI300 same-open price reference required')
        b=b.set_index('date').reindex(n.date)
        if b.open.isna().any():raise ValueError('benchmark cannot be forward-filled')
    lookup=p.set_index(['date','symbol']);holdings={};marks={};cash=1.;receivables=[];previous_nav=1.;previous_receivable=0.;previous_index=None
    daily=[];positions=[];assets=[]
    for row in n.itertuples():
        date=row.date;old_holdings=holdings.copy();old_market_value=sum(q*marks[s] for s,q in old_holdings.items());contributions={}
        def asset(symbol):
            if symbol not in contributions:contributions[symbol]=dict(date=date,symbol=symbol,price_pnl=0.,gross_dividend_entitlement=0.,commission_cost=0.,slippage_cost=0.,stamp_duty_cost=0.)
            return contributions[symbol]
        def price(symbol):
            if (date,symbol) not in lookup.index:raise ValueError('missing positive held valuation in accounting audit')
            quote=lookup.loc[(date,symbol)];value=float(quote.open if quote.tradestatus=='1' and quote.open>0 else quote.preclose)
            if not np.isfinite(value) or value<=0:raise ValueError('unknown positive held valuation in accounting audit')
            return value
        events=a.loc[a.ex_date.eq(date)].set_index('symbol');income=0.;price_pnl=0.
        for symbol,quantity in old_holdings.items():
            event=events.loc[symbol] if symbol in events.index else None
            multiplier=float(event.share_multiplier) if event is not None else 1.
            entitlement=quantity*float(event.cash_per_share) if event is not None else 0.
            holdings[symbol]=quantity*multiplier
            change=quantity*(multiplier*price(symbol)-marks[symbol]);price_pnl+=change;income+=entitlement
            asset(symbol)['price_pnl']+=change;asset(symbol)['gross_dividend_entitlement']+=entitlement
            if entitlement:receivables.append((event.pay_date,entitlement))
        paid=sum(amount for pay,amount in receivables if pay<date);cash+=paid
        receivables=[(pay,amount) for pay,amount in receivables if pay>=date]
        commission=slippage=tax=0.
        fills=t.loc[t.date.eq(date)&t.status.eq('filled')] if not t.empty else t
        for order in fills.itertuples():
            quote=lookup.loc[(date,order.symbol)]
            if quote.tradestatus!='1' or quote.open<=0:raise ValueError('filled order is not executable at raw open')
            fee=float(order.notional)*commission_bps/10000;slip=float(order.notional)*slippage_bps/10000;stamp=float(order.stamp_duty)
            if (order.side=='buy' and stamp!=0) or not np.isclose(fee+slip+stamp,float(order.cost),rtol=0,atol=1e-12):raise ValueError('observed cost contract differs from accounting assumptions')
            quantity=float(order.notional)/float(quote.open);sign=1 if order.side=='buy' else -1
            holdings[order.symbol]=holdings.get(order.symbol,0)+sign*quantity;cash-=sign*float(order.notional)+float(order.cost)
            if holdings[order.symbol]<-1e-10:raise ValueError('negative reconstructed account shares')
            commission+=fee;slippage+=slip;tax+=stamp
            for key,value in [('commission_cost',fee),('slippage_cost',slip),('stamp_duty_cost',stamp)]:asset(order.symbol)[key]+=value
        if cash<-1e-9:raise ValueError('negative reconstructed account cash')
        cash=max(0.,cash);holdings={s:q for s,q in holdings.items() if q>1e-12};marks={s:price(s) for s in holdings};pending=sum(amount for _,amount in receivables)
        wealth=cash+pending+sum(q*marks[s] for s,q in holdings.items());cost=commission+slippage+tax;delta=float(row.nav)-previous_nav;error=delta-(price_pnl+income-cost)
        if not np.allclose([wealth,cash,pending],[row.nav,row.cash,row.dividend_receivable],rtol=0,atol=1e-10) or abs(error)>1e-10:raise ValueError('daily shares/cash/receivables/PnL do not reconcile')
        denominator=previous_nav if previous_nav>0 else np.nan;market=np.nan;residual=np.nan
        if b is not None:
            index=float(b.loc[date,'open']);index_return=index/previous_index-1 if previous_index is not None else 0.
            market=old_market_value*index_return;residual=price_pnl-market;previous_index=index
        daily.append(dict(date=date,opening_nav=previous_nav,nav=float(row.nav),price_pnl=price_pnl,gross_dividend_entitlement=income,dividends_paid_cash=paid,receivable_change=pending-previous_receivable,commission_cost=commission,slippage_cost=slippage,stamp_duty_cost=tax,total_execution_cost=cost,nav_change=delta,reconciliation_error=error,price_return_contribution=price_pnl/denominator,dividend_return_contribution=income/denominator,cost_return_contribution=-cost/denominator,net_return=delta/denominator,unit_beta_market_reference=market,price_residual_after_market=residual))
        for symbol,quantity in sorted(holdings.items()):positions.append(dict(date=date,symbol=symbol,shares=quantity,raw_open_valuation=marks[symbol],market_value=quantity*marks[symbol],portfolio_weight=quantity*marks[symbol]/row.nav if row.nav>0 else np.nan))
        assets.extend(contributions.values());previous_nav=float(row.nav);previous_receivable=pending
    return pd.DataFrame(daily,columns=DAILY),pd.DataFrame(positions,columns=POSITIONS),pd.DataFrame(assets,columns=ASSETS)

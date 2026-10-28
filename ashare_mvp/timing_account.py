"""Separate timing account fork; the sealed ranking producer is unchanged.

Accounting body copied from pipeline.simulate_monthly; only the target-order
hook changes. Tests require exact identity-controller NAV/orders, plus timing,
blocked execution, corporate-action and held-valuation coverage.
"""
import pandas as pd
from .costs import stamp_duty_rate


def simulate_timing(data,picks,config,reader,group,raw_prices,corporate_actions,reference_tick_size=.01):
    from .timing_policy import CloseTimingPolicy
    from .causal_quality import HeldValuationError
    controller=CloseTimingPolicy(data.calendar,picks,reader,group,config.top_k)
    try:
        nav,trades=_simulate_raw_timing(data,picks,config,raw_prices,corporate_actions,reference_tick_size,True,controller)
    except HeldValuationError as error:
        error.timing_decisions=pd.DataFrame(controller.decisions)
        raise
    return nav,trades,pd.DataFrame(controller.decisions)

RANKING_ENGINE_FUNCTION_SHA256 = 'f499b79618e2685eecdf232bc1665d79c7f3470d806b11002eef350379e506cb'


def _simulate_raw_timing(data, picks, config, raw_prices=None, corporate_actions=None, reference_tick_size=None, causal_quality_research=False, controller=None):
    """Fractional adjusted shares: economic research NAV, with blocked orders.

    Uses contemporaneous execution flags only; blocked sales remain held,
    blocked buys stay in cash. No substitution using future returns.
    """
    if picks.empty:
        return pd.DataFrame(), pd.DataFrame()
    orders = {}
    for date, selected in picks.groupby("date"):
        idx = data.calendar.searchsorted(date, side="right")
        if idx < len(data.calendar):
            orders[data.calendar[idx]] = selected.set_index("symbol").target_weight.to_dict()
    if not orders:
        return pd.DataFrame(), pd.DataFrame()
    if (raw_prices is None) != (corporate_actions is None):
        raise ValueError("raw prices and corporate actions must be supplied together")
    if reference_tick_size is not None and raw_prices is None:
        raise ValueError("reference tick requires raw prices and corporate actions")
    event_mode = raw_prices is not None
    if causal_quality_research and not event_mode:raise ValueError("causal quality account requires raw price/event inputs")
    receivables, events, raw_quotes = [], {}, None
    if event_mode:
        from .corporate_actions import economic_return_prices
        if set(raw_prices.symbol) != set(data.prices.symbol):
            raise ValueError("event account universe differs from feature universe")
        # Validate complete quotes, event clocks and price discontinuities first.
        fault_keys=set()
        if causal_quality_research:
            from .causal_quality import quality_inputs,HeldValuationError
            checked,_,_=quality_inputs(raw_prices,corporate_actions,data.calendar,reference_tick_size)
            faulty=checked[checked.reference_fault]
            fault_keys=set(zip(faulty.date,faulty.symbol))
            required_dates=data.calendar[data.calendar>=min(orders)]
            if not required_dates.isin(pd.to_datetime(raw_prices.date)).all():raise ValueError("missing requested portfolio market sessions")
        else:
            economic_return_prices(raw_prices, corporate_actions, data.calendar, reference_tick_size)
        raw_quotes = raw_prices.copy()
        raw_quotes["date"] = pd.to_datetime(raw_quotes.date)
        for field in ["open", "preclose"]:
            raw_quotes[field] = pd.to_numeric(raw_quotes[field], errors="raise")
        raw_quotes = raw_quotes.set_index(["date", "symbol"])
        if causal_quality_research:raw_quotes=raw_quotes.sort_index()
        a = corporate_actions.copy()
        a["ex_date"] = pd.to_datetime(a.ex_date)
        a["pay_date"] = pd.to_datetime(a.pay_date)
        for field in ["cash_per_share", "share_multiplier"]:
            a[field] = pd.to_numeric(a[field], errors="raise")
        events = {day: group for day, group in a.groupby("ex_date")}
    cash, holdings = 1.0, {}
    nav, trades = [], []
    buy_rate = (config.commission_bps + config.slippage_bps) / 10000
    for date, daily in data.prices.groupby("date", sort=True):
        if date < min(orders):
            continue
        tax_rate = stamp_duty_rate(date, config.sell_tax_bps)
        sell_rate = buy_rate + tax_rate
        bars = daily.set_index("symbol")
        prices = bars.total_return_open
        if event_mode and causal_quality_research:
            holdings={s:q for s,q in holdings.items() if q>1e-12}
            for symbol in holdings:
                if symbol not in bars.index or (date,symbol) not in raw_quotes.index:
                    raise HeldValuationError("missing",symbol,date,nav,trades,holdings)
                if (date,symbol) in fault_keys:
                    raise HeldValuationError("reference",symbol,date,nav,trades,holdings)
                if 'terminal_date' in raw_quotes and pd.notna(raw_quotes.at[(date,symbol),'terminal_date']) and date>=pd.Timestamp(raw_quotes.at[(date,symbol),'terminal_date']):
                    raise HeldValuationError("terminal",symbol,date,nav,trades,holdings)
        if event_mode:
            quotes = raw_quotes.loc[date].reindex(bars.index)
            tradable = quotes.tradestatus.astype(str).eq("1") & quotes.open.gt(0)
            prices = quotes.open.where(tradable, quotes.preclose)
            bars = bars.copy()
            bars["can_buy"] &= tradable
            bars["can_sell"] &= tradable
            if date in events:
                for event in events[date].itertuples(index=False):
                    shares = holdings.get(event.symbol, 0.0)
                    entitlement = shares * event.cash_per_share
                    if entitlement:
                        receivables.append((event.pay_date, entitlement))
                    if shares:
                        holdings[event.symbol] = shares * event.share_multiplier
            cash += sum(amount for day, amount in receivables if day < date)
            receivables = [(day, amount) for day, amount in receivables if day >= date]
        pending = sum(amount for _, amount in receivables)
        equity = cash + pending + sum(q * prices[s] for s, q in holdings.items())
        targets = orders.get(date) if controller is None else controller.targets(date, holdings, prices, equity)
        if targets is not None:
            for symbol in sorted(holdings):
                price = prices[symbol]
                value = holdings[symbol] * price
                sale = max(0.0, value - targets.get(symbol, 0.0) * equity)
                if sale > 1e-12:
                    if bars.at[symbol, "can_sell"] and price > 0:
                        holdings[symbol] -= sale / price
                        cash += sale * (1 - sell_rate)
                        trades.append(dict(date=date, symbol=symbol, side="sell", notional=sale,
                                           stamp_duty_bps=tax_rate * 10000, stamp_duty=sale * tax_rate,
                                           cost=sale * sell_rate, status="filled"))
                    else:
                        trades.append(dict(date=date, symbol=symbol, side="sell", notional=sale,
                                           stamp_duty_bps=tax_rate * 10000, stamp_duty=0.0,
                                           cost=0.0, status="blocked"))
            buys = {}
            for symbol, weight in targets.items():
                if causal_quality_research and symbol not in bars.index:
                    trades.append(dict(date=date,symbol=symbol,side="buy",notional=max(0.,weight*equity),stamp_duty_bps=0.,stamp_duty=0.,cost=0.,status="blocked"))
                    continue
                if causal_quality_research and 'terminal_date' in raw_quotes and pd.notna(raw_quotes.at[(date,symbol),'terminal_date']) and date>=pd.Timestamp(raw_quotes.at[(date,symbol),'terminal_date']):
                    trades.append(dict(date=date,symbol=symbol,side="buy",notional=max(0.,weight*equity),stamp_duty_bps=0.,stamp_duty=0.,cost=0.,status="blocked"))
                    continue
                price = prices[symbol]
                amount = max(0.0, weight * equity - holdings.get(symbol, 0.0) * price)
                if amount > 1e-12:
                    if bars.at[symbol, "can_buy"] and price > 0:
                        buys[symbol] = amount
                    else:
                        trades.append(dict(date=date, symbol=symbol, side="buy", notional=amount,
                                           stamp_duty_bps=0.0, stamp_duty=0.0,
                                           cost=0.0, status="blocked"))
            scale = min(1.0, cash / (sum(buys.values()) * (1 + buy_rate))) if buys else 0.0
            for symbol, amount in buys.items():
                amount *= scale
                holdings[symbol] = holdings.get(symbol, 0.0) + amount / prices[symbol]
                cash -= amount * (1 + buy_rate)
                trades.append(dict(date=date, symbol=symbol, side="buy", notional=amount,
                                   stamp_duty_bps=0.0, stamp_duty=0.0,
                                   cost=amount * buy_rate, status="filled"))
        if cash < -1e-9:
            raise AssertionError("negative cash")
        cash = max(cash, 0.0)  # remove floating-point subtraction residue
        if causal_quality_research:holdings={s:q for s,q in holdings.items() if q>1e-12}
        equity = cash + pending + sum(q * prices[s] for s, q in holdings.items())
        record = dict(date=date, nav=equity, cash=cash, positions=sum(q > 1e-12 for q in holdings.values()))
        if event_mode:
            record.update(dividend_receivable=pending, return_basis="raw_share_account_gross_dividends")
        nav.append(record)
    trades=pd.DataFrame(trades)
    if not trades.empty and "trading_symbol" in data.prices:
        trades=trades.merge(data.prices[["date","symbol","trading_symbol"]],on=["date","symbol"],how="left",validate="many_to_one")
    return pd.DataFrame(nav), trades

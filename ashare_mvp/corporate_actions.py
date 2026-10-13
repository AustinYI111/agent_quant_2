"""Causal gross economic-return indices with explicit cash/share events.

Entitlements are valued at ex-date; cash is reinvested after payment on the
next tradable session. This is a research index, not a broker share ledger.
"""
import numpy as np
import pandas as pd


def economic_return_prices(prices, actions, calendar):
    required = {"date", "symbol", "open", "close", "preclose", "tradestatus"}
    fields = {"symbol", "ex_date", "available_date", "pay_date", "cash_per_share", "share_multiplier"}
    if not required.issubset(prices) or not fields.issubset(actions):
        raise ValueError("raw prices or corporate actions have incomplete schema")
    p, a = prices.copy(), actions.copy()
    p["date"] = pd.to_datetime(p.date)
    for field in ["ex_date", "available_date", "pay_date"]:
        a[field] = pd.to_datetime(a[field])
    cal = pd.DatetimeIndex(calendar)
    if cal.has_duplicates or cal.hasnans or not cal.is_monotonic_increasing:
        raise ValueError("invalid corporate-action calendar")
    if p.duplicated(["date", "symbol"]).any() or a.duplicated(["symbol", "ex_date"]).any():
        raise ValueError("ambiguous price/action key")
    for field in ["open", "close", "preclose"]:
        p[field] = pd.to_numeric(p[field], errors="raise")
        if not np.isfinite(p[field]).all() or (p[field] < 0).any():
            raise ValueError("invalid raw price")
    for field in ["cash_per_share", "share_multiplier"]:
        a[field] = pd.to_numeric(a[field], errors="raise")
        if not np.isfinite(a[field]).all():
            raise ValueError("invalid corporate-action amount")
    if (a.cash_per_share < 0).any() or (a.share_multiplier <= 0).any():
        raise ValueError("invalid cash/share multiplier")
    if a[["ex_date", "available_date", "pay_date"]].isna().any().any() or (a.available_date > a.ex_date).any() or (a.pay_date < a.ex_date).any():
        raise ValueError("action not known at ex-date or invalid payment date")
    if not p.date.isin(cal).all() or not a.ex_date.isin(cal).all() or not p.tradestatus.astype(str).isin(["0", "1"]).all():
        raise ValueError("session/state not consistent with calendar")
    if "rights_cash_required" in a and a.rights_cash_required.fillna(0).ne(0).any():
        raise ValueError("rights subscription requires external capital; explicit account handling required")
    result = []
    for symbol, rows in p.groupby("symbol", sort=False):
        rows = rows.sort_values("date")
        expected = cal[cal >= rows.date.min()]
        if not pd.DatetimeIndex(rows.date).equals(expected):
            raise ValueError(f"{symbol}: missing sessions or unresolved terminal valuation; do not silently remove delisted assets")
        events = a[(a.symbol == symbol) & (a.ex_date > rows.date.min())].set_index("ex_date")
        shares, cash, receivables, previous_close = 1.0, 0.0, [], None
        for row in rows.itertuples(index=False):
            tradable = str(row.tradestatus) == "1" and row.open > 0
            valuation_open = row.open if tradable else row.preclose
            if valuation_open <= 0 or row.close <= 0:
                raise ValueError(f"{symbol}: nonpositive valuation requires a terminal settlement record")
            event = events.loc[row.date] if row.date in events.index else None
            if previous_close is not None:
                expected_preclose = previous_close
                if event is not None:
                    expected_preclose = (previous_close - event.cash_per_share) / event.share_multiplier
                if not np.isclose(row.preclose, expected_preclose, rtol=1e-5, atol=1e-4):
                    raise ValueError(f"{symbol} {row.date.date()}: unreconciled price adjustment/corporate action")
            if event is not None:
                entitlement = shares * event.cash_per_share
                if entitlement:
                    receivables.append((event.pay_date, entitlement))
                shares *= event.share_multiplier
            # Pay-date cash availability time is not assumed known at that open.
            matured = [(day, amount) for day, amount in receivables if day < row.date]
            cash += sum(amount for _, amount in matured)
            receivables = [(day, amount) for day, amount in receivables if day >= row.date]
            if tradable and cash > 0:
                shares += cash / row.open
                cash = 0.0
            pending = sum(amount for _, amount in receivables)
            record = row._asdict()
            record.update(raw_open=row.open, raw_close=row.close,
                          total_return_open=shares * valuation_open + cash + pending,
                          close=shares * row.close + cash + pending,
                          economic_shares=shares, dividend_receivable=pending, dividend_cash=cash,
                          price_method="gross_entitlement_index_reinvest_after_payment",
                          index_certified=False)
            result.append(record)
            previous_close = row.close
    return pd.DataFrame(result).sort_values(["date", "symbol"]).reset_index(drop=True)

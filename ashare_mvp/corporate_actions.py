"""Causal gross economic-return indices with explicit cash/share events.

Entitlements are valued at ex-date; cash is reinvested after payment on the
next tradable session. This is a research index, not a broker share ledger.
"""
import numpy as np
import pandas as pd
from pathlib import Path
import hashlib
import json
from .price_reference import reference_matches, validate_reference_tick


def economic_return_prices(prices, actions, calendar, reference_tick_size=None):
    validate_reference_tick(reference_tick_size)
    required = {"date", "symbol", "open", "close", "preclose", "tradestatus"}
    fields = {"symbol", "ex_date", "available_date", "pay_date", "cash_per_share", "share_multiplier"}
    if not required.issubset(prices) or not fields.issubset(actions):
        raise ValueError("raw prices or corporate actions have incomplete schema")
    p, a = prices.copy(), actions.copy()
    p["date"] = pd.to_datetime(p.date)
    for field in ["ex_date", "available_date", "pay_date"]:
        a[field] = pd.to_datetime(a[field])
    cal = pd.DatetimeIndex(calendar)
    if cal.empty or cal.has_duplicates or cal.hasnans or not cal.is_monotonic_increasing:
        raise ValueError("invalid corporate-action calendar")
    if p.empty or p[["date", "symbol"]].isna().any().any() or a.symbol.isna().any():
        raise ValueError("empty prices or missing security/date key")
    if p.symbol.astype(str).str.strip().eq("").any() or a.symbol.astype(str).str.strip().eq("").any():
        raise ValueError("empty security key")
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
    # Differential distributions can pay public holders more than the exchange
    # reference-price deduction. An explicit notice-backed value is required.
    if "reference_cash_per_share" in a:
        a["reference_cash_per_share"] = pd.to_numeric(a.reference_cash_per_share, errors="raise").fillna(a.cash_per_share)
        if not np.isfinite(a.reference_cash_per_share).all() or (a.reference_cash_per_share < 0).any():
            raise ValueError("invalid reference-price dividend")
    else:
        a["reference_cash_per_share"] = a.cash_per_share
    if "reference_share_multiplier" in a:
        a["reference_share_multiplier"]=pd.to_numeric(a.reference_share_multiplier,errors="raise").fillna(a.share_multiplier)
        if not np.isfinite(a.reference_share_multiplier).all() or a.reference_share_multiplier.le(0).any():
            raise ValueError("invalid reference-price share multiplier")
    else:
        a["reference_share_multiplier"]=a.share_multiplier
    if a[["ex_date", "available_date"]].isna().any().any() or (a.cash_per_share.gt(0) & a.pay_date.isna()).any() or (a.available_date > a.ex_date).any() or (a.pay_date < a.ex_date).any():
        raise ValueError("action not known at ex-date or invalid payment date")
    within_interval = a.ex_date.between(cal.min(), cal.max())
    if not p.date.isin(cal).all() or not a.loc[within_interval, "ex_date"].isin(cal).all() or not p.tradestatus.astype(str).isin(["0", "1"]).all():
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
                    expected_preclose = (previous_close - event.reference_cash_per_share) / event.reference_share_multiplier
                if not (event is None and row.preclose == previous_close) and not reference_matches(row.preclose, previous_close,
                        event.reference_cash_per_share if event is not None else 0.0,
                        event.reference_share_multiplier if event is not None else 1.0, reference_tick_size):
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


def reconcile_price_files(prices_path, actions_path, calendar_path, out):
    """Seal supplied CSV evidence and export a research index, never readiness."""
    target = Path(out)
    if target.exists() and any(target.iterdir()):
        raise ValueError("reconciliation output must be empty")
    sources = {"prices": Path(prices_path), "actions": Path(actions_path), "calendar": Path(calendar_path)}
    contents = {key: path.read_bytes() for key, path in sources.items()}
    from io import BytesIO
    prices = pd.read_csv(BytesIO(contents["prices"]), dtype={"symbol": str, "code": str, "tradestatus": str})
    if "code" in prices and "symbol" not in prices:
        prices = prices.rename(columns={"code": "symbol"})
    actions = pd.read_csv(BytesIO(contents["actions"]), dtype={"symbol": str})
    calendar = pd.read_csv(BytesIO(contents["calendar"]), dtype=str)
    if {"calendar_date", "is_trading_day"}.issubset(calendar):
        dates = calendar.loc[calendar.is_trading_day == "1", "calendar_date"]
    elif "date" in calendar:
        dates = calendar.date
    else:
        raise ValueError("calendar requires date or calendar_date/is_trading_day")
    # Calendar defines the requested interval; never silently truncate at last price.
    index = economic_return_prices(prices, actions, pd.to_datetime(dates))
    target.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for key, data in contents.items():
        (target / f"input_{key}.csv").write_bytes(data)
        hashes[key] = hashlib.sha256(data).hexdigest()
    output = target / "economic_prices.csv"
    index.to_csv(output, index=False)
    summary = {"rows": len(index), "symbols": int(index.symbol.nunique()),
               "start": str(index.date.min().date()), "end": str(index.date.max().date()),
               "input_sha256": hashes, "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
               "certified": False, "model_ready": False,
               "method": "gross_entitlement_index_reinvest_after_payment",
               "limitations": ["supplied event vintage not independently certified", "no dividend tax or reinvestment fees",
                               "share entitlements are not a broker tradability ledger", "rights and unresolved terminal values rejected"]}
    (target / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def entry_return_path(prices, actions, calendar, symbol, entry, end, reference_tick_size=None):
    """Gross wealth for a new open buyer, excluding pre-entry entitlements.

    A seasoned entitlement index cannot be divided to obtain this return:
    its pending dividends belong to earlier holders. Start a fresh ledger.
    End is a valuation date; suspended end dates are not executable exits.
    """
    entry, end = pd.Timestamp(entry), pd.Timestamp(end)
    cal = pd.DatetimeIndex(calendar)
    if entry not in cal or end not in cal or entry > end:
        raise ValueError("holding interval must use ordered calendar sessions")
    dates = pd.to_datetime(prices.date)
    selected = prices.loc[(prices.symbol == symbol) & dates.between(entry, end)].copy()
    first = selected.loc[pd.to_datetime(selected.date) == entry]
    if len(first) != 1 or str(first.iloc[0].tradestatus) != "1" or first.iloc[0].open <= 0:
        raise ValueError("entry requires an executable raw open")
    path = economic_return_prices(selected, actions.loc[actions.symbol == symbol], cal[(cal >= entry) & (cal <= end)], reference_tick_size)
    path["holding_return"] = path.total_return_open / float(first.iloc[0].open) - 1
    path["entry_date"] = entry
    path["executable_exit"] = path.tradestatus.astype(str).eq("1") & path.raw_open.gt(0)
    return path


def entry_forward_labels(prices, actions, calendar, signal_dates, horizons=(3, 6, 12), reference_tick_size=None):
    """Gross next-open outcomes; future labels, not historical features."""
    cal=pd.DatetimeIndex(calendar)
    signals=pd.DatetimeIndex(signal_dates)
    if cal.empty or cal.hasnans or cal.has_duplicates or not cal.is_monotonic_increasing:
        raise ValueError("ordered unique label calendar required")
    if signals.has_duplicates or not signals.isin(cal).all():
        raise ValueError("unique signal sessions required")
    if not horizons or len(set(horizons))!=len(horizons) or any(not isinstance(h,int) or h<=0 for h in horizons):
        raise ValueError("positive unique integer horizons required")
    groups=dict(tuple(prices.groupby("symbol",sort=False)))
    events=dict(tuple(actions.groupby("symbol",sort=False)))
    rows=[]
    for date in signals:
        pos=cal.searchsorted(date,side="right")
        if pos==len(cal): continue
        entry=cal[pos]
        ends={h:(cal[i] if (i:=cal.searchsorted(entry+pd.DateOffset(months=h)))<len(cal) else pd.NaT) for h in horizons}
        for symbol,bars in groups.items():
            first=bars.loc[pd.to_datetime(bars.date)==entry]
            executable=len(first)==1 and str(first.iloc[0].tradestatus)=="1" and first.iloc[0].open>0
            mature=[d for d in ends.values() if pd.notna(d)]
            path=entry_return_path(bars,events.get(symbol,actions.iloc[:0]),cal,symbol,entry,max(mature),reference_tick_size).set_index("date") if executable and mature else None
            for h,end in ends.items():
                outcome=path.loc[end] if path is not None and pd.notna(end) else None
                rows.append(dict(date=date,symbol=symbol,horizon=h,entry_date=entry,label_end=end,target=float(outcome.holding_return) if outcome is not None else np.nan,
                    executable_exit=bool(outcome.executable_exit) if outcome is not None else False,
                    status="unmatured" if pd.isna(end) else ("valued" if executable else "blocked_entry"),return_basis="gross_new_entry_entitlements"))
    return pd.DataFrame(rows,columns=["date","symbol","horizon","entry_date","label_end","target","executable_exit","status","return_basis"])

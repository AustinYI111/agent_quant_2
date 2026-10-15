"""Vectorized raw-price discontinuity audit; never removes failed securities."""
import numpy as np
import pandas as pd


def audit_price_actions(prices, actions, calendar):
    p=prices.copy(); a=actions.copy(); cal=pd.DatetimeIndex(calendar)
    if cal.empty or cal.hasnans or cal.has_duplicates or not cal.is_monotonic_increasing:
        raise ValueError("ordered unique audit calendar required")
    required={"date","symbol","close","preclose"}
    if not required.issubset(p) or not {"symbol","ex_date","cash_per_share","share_multiplier"}.issubset(a):
        raise ValueError("audit schema incomplete")
    p["date"]=pd.to_datetime(p.date); a["ex_date"]=pd.to_datetime(a.ex_date)
    if p.empty or p[["date","symbol"]].isna().any().any() or p.duplicated(["date","symbol"]).any() or a.duplicated(["symbol","ex_date"]).any():
        raise ValueError("ambiguous or empty audit keys")
    if not p.date.isin(cal).all(): raise ValueError("quote date outside calendar")
    for field in ["close","preclose"]: p[field]=pd.to_numeric(p[field],errors="raise")
    for field in ["cash_per_share","share_multiplier"]: a[field]=pd.to_numeric(a[field],errors="raise")
    if not np.isfinite(a[["cash_per_share","share_multiplier"]]).all().all() or a.cash_per_share.lt(0).any() or a.share_multiplier.le(0).any():
        raise ValueError("invalid event amounts")
    if "reference_cash_per_share" in a:
        a["reference_cash_per_share"]=pd.to_numeric(a.reference_cash_per_share,errors="raise").fillna(a.cash_per_share)
        if not np.isfinite(a.reference_cash_per_share).all() or a.reference_cash_per_share.lt(0).any(): raise ValueError("invalid reference cash")
    else: a["reference_cash_per_share"]=a.cash_per_share
    p=p.sort_values(["symbol","date"])
    p["previous_close"]=p.groupby("symbol").close.shift()
    p["previous_date"]=p.groupby("symbol").date.shift()
    p=p.merge(a[["symbol","ex_date","reference_cash_per_share","share_multiplier"]],left_on=["symbol","date"],right_on=["symbol","ex_date"],how="left",validate="one_to_one")
    p["expected_preclose"]=(p.previous_close-p.reference_cash_per_share.fillna(0))/p.share_multiplier.fillna(1)
    issues=[]
    for symbol,bars in p.groupby("symbol",sort=False):
        expected=cal[cal>=bars.date.min()]
        missing=expected[~expected.isin(bars.date)]
        if len(missing): issues.append(dict(symbol=symbol,date=missing[0],issue="missing_sessions_or_terminal_valuation",count=len(missing)))
    invalid=~np.isfinite(p.close)|~np.isfinite(p.preclose)|p.close.le(0)|p.preclose.le(0)
    adjacent=(cal.get_indexer(p.date)-cal.get_indexer(p.previous_date))==1
    adjacent &= p.previous_date.notna()
    mismatch=adjacent & p.previous_close.notna() & ~np.isclose(p.preclose,p.expected_preclose,rtol=1e-5,atol=1e-4) & ~invalid
    for issue,mask in [("nonpositive_or_invalid_valuation",invalid),("unreconciled_adjustment",mismatch)]:
        for row in p.loc[mask].itertuples(index=False):
            issues.append(dict(symbol=row.symbol,date=row.date,issue=issue,count=1,observed_preclose=row.preclose,expected_preclose=row.expected_preclose))
    result=pd.DataFrame(issues,columns=["symbol","date","issue","count","observed_preclose","expected_preclose"])
    return result,dict(rows=len(p),symbols=int(p.symbol.nunique()),affected_symbols=int(result.symbol.nunique()),
                       issue_counts=result.groupby("issue")["count"].sum().astype(int).to_dict(),model_ready=False,
                       limitation="Audit from first observed quote; listing dates, event vintages, rights and terminal settlements require separate validation")

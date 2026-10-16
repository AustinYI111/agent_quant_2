"""Reviewed reference cash/share terms; actual holder entitlements stay intact."""
import re
import numpy as np
import pandas as pd


def load_reference_evidence(path):
    """Preserve declared JSON decimal precision at quote-rounding boundaries."""
    return pd.read_json(path,convert_dates=False,precise_float=True)


def apply_reference_overrides(actions, overrides, calendar):
    required={"symbol","ex_date","reference_cash_per_share","expected_cash_per_share","announcement_date","available_date","source_url","source_sha256","source_page","reviewed"}
    if not required.issubset(overrides): raise ValueError("reference evidence schema incomplete")
    a=actions.copy(); o=overrides.copy(); cal=pd.DatetimeIndex(calendar)
    if cal.empty or cal.hasnans or cal.has_duplicates or not cal.is_monotonic_increasing: raise ValueError("ordered reference evidence calendar required")
    for frame,fields in [(a,["ex_date","available_date"]),(o,["ex_date","announcement_date","available_date"])]:
        for field in fields:
            frame[field]=pd.to_datetime(frame[field])
            if frame[field].isna().any() or frame[field].dt.tz is not None or not frame[field].eq(frame[field].dt.normalize()).all():
                raise ValueError("normalized non-null reference clocks required")
        if frame.symbol.isna().any() or frame.duplicated(["symbol","ex_date"]).any(): raise ValueError("ambiguous reference event keys")
    if not o.reviewed.eq(True).all(): raise ValueError("unreviewed reference evidence rejected")
    if not o.source_url.astype(str).str.startswith("https://").all() or not o.source_sha256.astype(str).str.fullmatch(r"[0-9a-f]{64}").all():
        raise ValueError("reference source URL and SHA-256 required")
    page=pd.to_numeric(o.source_page,errors="raise")
    if not np.isfinite(page).all() or page.lt(1).any() or page.mod(1).ne(0).any(): raise ValueError("reference source page required")
    for field in ["reference_cash_per_share","expected_cash_per_share"]:
        o[field]=pd.to_numeric(o[field],errors="raise")
        if not np.isfinite(o[field]).all() or o[field].lt(0).any(): raise ValueError("invalid reference cash")
    share_fields=['reference_share_multiplier','expected_share_multiplier']
    if any(field in o for field in share_fields):
        if not all(field in o for field in share_fields):raise ValueError('paired reference and actual share anchors required')
        for field in share_fields:o[field]=pd.to_numeric(o[field],errors='raise')
        supplied=o[share_fields].notna()
        if supplied.any(axis=1).ne(supplied.all(axis=1)).any():raise ValueError('paired reference and actual share anchors required')
        for field in share_fields:
            known=o.loc[supplied.all(axis=1),field]
            if not np.isfinite(known).all() or known.le(0).any():raise ValueError('invalid reference share multiplier')
        a['share_multiplier']=pd.to_numeric(a.share_multiplier,errors='raise')
        if 'reference_share_multiplier' not in a:a['reference_share_multiplier']=np.nan
        else:a['reference_share_multiplier']=pd.to_numeric(a.reference_share_multiplier,errors='raise')
    a["cash_per_share"]=pd.to_numeric(a.cash_per_share,errors="raise")
    if "reference_cash_per_share" not in a: a["reference_cash_per_share"]=np.nan
    else: a["reference_cash_per_share"]=pd.to_numeric(a.reference_cash_per_share,errors="raise")
    lookup={(row.symbol,row.ex_date):index for index,row in a.iterrows()}
    for row in o.itertuples(index=False):
        key=(row.symbol,row.ex_date)
        if key not in lookup: raise ValueError("reference evidence has no matching payment event")
        index=lookup[key]
        if not np.isclose(a.at[index,"cash_per_share"],row.expected_cash_per_share,rtol=0,atol=1e-8): raise ValueError("reference evidence conflicts with actual payout")
        pos=cal.searchsorted(row.announcement_date,side="right")
        if pos>=len(cal) or row.available_date<cal[pos] or row.available_date>row.ex_date or row.ex_date not in cal:
            raise ValueError("reference evidence not visible before execution")
        existing=a.at[index,"reference_cash_per_share"]
        if pd.notna(existing) and not np.isclose(existing,row.reference_cash_per_share,rtol=0,atol=1e-8): raise ValueError("conflicting reviewed reference amounts")
        reference_shares=getattr(row,'reference_share_multiplier',np.nan)
        if pd.notna(reference_shares):
            if not np.isclose(a.at[index,'share_multiplier'],row.expected_share_multiplier,rtol=0,atol=1e-8):raise ValueError('reference evidence conflicts with actual shares')
            existing_shares=a.at[index,'reference_share_multiplier']
            if pd.notna(existing_shares) and not np.isclose(existing_shares,reference_shares,rtol=0,atol=1e-8):raise ValueError('conflicting reviewed reference shares')
            a.at[index,'reference_share_multiplier']=reference_shares
        a.at[index,"reference_cash_per_share"]=row.reference_cash_per_share
        a.at[index,"available_date"]=max(a.at[index,"available_date"],row.available_date)
        if a.at[index,"available_date"]>row.ex_date: raise ValueError("payment event not visible before execution")
        a.loc[index,"reference_source_url"]=row.source_url
        a.loc[index,"reference_source_sha256"]=row.source_sha256
        a.loc[index,"reference_source_page"]=int(row.source_page)
    return a

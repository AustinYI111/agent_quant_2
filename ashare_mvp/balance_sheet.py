"""Balance-sheet ratios visible only after all supplied provider clocks."""
import numpy as np
import pandas as pd


def normalize_balance_records(records, symbol, calendar):
    from .public_data import is_ashare
    if not is_ashare(symbol):
        raise ValueError("supported A-share symbol required")
    raw = pd.DataFrame(records)
    required = ["SECURITY_CODE", "REPORT_DATE", "NOTICE_DATE", "UPDATE_DATE", "TOTAL_ASSETS", "TOTAL_LIABILITIES"]
    if not set(required).issubset(raw) or raw.empty or not raw.SECURITY_CODE.astype(str).eq(symbol.split(".")[1]).all():
        raise ValueError("balance identity/schema mismatch")
    sessions = pd.DatetimeIndex(calendar)
    if sessions.empty or sessions.hasnans or sessions.has_duplicates or not sessions.is_monotonic_increasing or sessions.tz is not None or not sessions.equals(sessions.normalize()):
        raise ValueError("ordered normalized session calendar required")
    result = pd.DataFrame({"symbol": symbol, "report_date": pd.to_datetime(raw.REPORT_DATE).dt.normalize()})
    clocks = pd.DataFrame({c: pd.to_datetime(raw[c]).dt.normalize() for c in ["NOTICE_DATE", "UPDATE_DATE"]})
    if result.report_date.isna().any() or clocks.isna().any().any() or clocks.NOTICE_DATE.le(result.report_date).any():
        raise ValueError("invalid balance publication clocks")
    if "EITIME" in raw:
        clocks["EITIME"] = pd.to_datetime(raw.EITIME).dt.normalize()
    latest = clocks.max(axis=1)
    positions = sessions.searchsorted(latest, side="right")
    result["available_date"] = [sessions[p] if p < len(sessions) else pd.NaT for p in positions]
    assets = pd.to_numeric(raw.TOTAL_ASSETS, errors="raise")
    liabilities = pd.to_numeric(raw.TOTAL_LIABILITIES, errors="raise")
    if ((assets.notna() & (~np.isfinite(assets) | assets.le(0))) | (liabilities.notna() & (~np.isfinite(liabilities) | liabilities.lt(0)))).any():
        raise ValueError("invalid balance amounts")
    result["debt_ratio"] = liabilities / assets
    result["publication_date"] = clocks.NOTICE_DATE
    result["provider_update_date"] = clocks.UPDATE_DATE
    result["certified"] = False
    if result.duplicated(["symbol", "report_date", "available_date"]).any():
        raise ValueError("ambiguous balance versions")
    return result

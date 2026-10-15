"""Dated one-for-one same-issuer code changes; no assumed delisting recovery."""
import pandas as pd
from .public_data import is_ashare


def code_change_maps(changes):
    required={"old_symbol","new_symbol","announcement_date","effective_date","share_ratio","source_url"}
    if not required.issubset(changes): raise ValueError("code-change evidence schema incomplete")
    c=changes.copy()
    for field in ["announcement_date","effective_date"]:
        c[field]=pd.to_datetime(c[field])
        if c[field].isna().any() or c[field].dt.tz is not None or not c[field].eq(c[field].dt.normalize()).all():
            raise ValueError("normalized code-change clocks required")
    if c.duplicated("old_symbol").any() or c.duplicated("new_symbol").any(): raise ValueError("ambiguous code-change graph")
    if c.announcement_date.ge(c.effective_date).any(): raise ValueError("code change must be announced before effective session")
    if not c.old_symbol.map(is_ashare).all() or not c.new_symbol.map(is_ashare).all(): raise ValueError("supported A-share code required")
    if not pd.to_numeric(c.share_ratio,errors="raise").eq(1).all(): raise ValueError("share exchange ratios require a separate corporate action")
    if not c.source_url.astype(str).str.startswith("https://").all(): raise ValueError("code-change evidence URL required")
    parents=dict(zip(c.new_symbol,c.old_symbol)); starts=dict(zip(c.new_symbol,c.effective_date)); ends=dict(zip(c.old_symbol,c.effective_date))
    roots={}
    for symbol in set(c.old_symbol)|set(c.new_symbol):
        root=symbol; seen=set()
        while root in parents:
            if root in seen: raise ValueError("cyclic code-change graph")
            seen.add(root); root=parents[root]
        roots[symbol]=root
        if symbol in starts and symbol in ends and starts[symbol]>=ends[symbol]: raise ValueError("code-change chain runs backward")
    return roots,starts,ends


def stitch_code_prices(prices, changes):
    """Keep original trading code; quarantine inactive aliases without rewriting quotes."""
    roots,starts,ends=code_change_maps(changes)
    p=prices.copy(); p["date"]=pd.to_datetime(p.date)
    if p[["symbol","date"]].isna().any().any() or p.duplicated(["symbol","date"]).any(): raise ValueError("ambiguous input quote key")
    if "trading_symbol" in p: raise ValueError("quotes already contain a trading-code mapping")
    active=pd.Series(True,index=p.index)
    for symbol,day in starts.items(): active &= p.symbol.ne(symbol)|p.date.ge(day)
    for symbol,day in ends.items(): active &= p.symbol.ne(symbol)|p.date.lt(day)
    rejected=p.loc[~active].copy(); rejected["identity_issue"]="code_inactive_at_session"
    result=p.loc[active].copy(); result["trading_symbol"]=result.symbol
    result["symbol"]=result.symbol.map(lambda x:roots.get(x,x))
    if result.duplicated(["symbol","date"]).any(): raise ValueError("overlapping active quotes for same issuer")
    return result.sort_values(["symbol","date"]).reset_index(drop=True),rejected


def canonical_event_symbols(events,changes,date_column="ex_date"):
    """Map provider latest-code histories to stable issuer keys; reject collisions."""
    roots,_,_=code_change_maps(changes)
    e=events.copy()
    if not {"symbol",date_column}.issubset(e): raise ValueError("event identity keys missing")
    e[date_column]=pd.to_datetime(e[date_column])
    if e[["symbol",date_column]].isna().any().any() or e[date_column].dt.tz is not None or not e[date_column].eq(e[date_column].dt.normalize()).all():
        raise ValueError("normalized non-null event identity keys required")
    if "source_symbol" in e: raise ValueError("events already contain an identity mapping")
    e["source_symbol"]=e.symbol
    e["symbol"]=e.symbol.map(lambda x:roots.get(x,x))
    if e.duplicated(["symbol",date_column]).any(): raise ValueError("ambiguous same-issuer event versions")
    return e

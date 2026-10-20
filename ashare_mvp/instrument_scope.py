"""Dated, reviewed instrument-type evidence for the ordinary A-share universe.

Never infer non-listing, settlement, or historical risk status from this policy.
Only explicit CDR codes are supported; unrecognized codes remain unresolved.
"""
import hashlib
import json
from urllib.parse import urlsplit
import numpy as np
import pandas as pd


def classify_instrument_scope(coverage, actions, evidence, calendar):
    required={'symbol','instrument_type','effective_from','valid_through','announcement_date',
              'source_url','source_sha256','source_page','source_locator','reviewed'}
    if not required.issubset(evidence):raise ValueError('instrument scope evidence schema incomplete')
    e=evidence.copy();c=coverage.copy();cal=pd.DatetimeIndex(calendar)
    if cal.empty or cal.hasnans or cal.has_duplicates or cal.tz is not None or not cal.is_monotonic_increasing or not cal.equals(cal.normalize()):
        raise ValueError('ordered normalized instrument scope calendar required')
    if not {'security','status'}.issubset(c) or 'symbol' not in actions:raise ValueError('event scope input schema incomplete')
    if e.empty or e.symbol.isna().any() or e.symbol.duplicated().any() or not e.symbol.astype(str).str.fullmatch(r'sh\.689\d{3}').all() or not e.instrument_type.eq('cdr').all():
        raise ValueError('explicit unique reviewed CDR identities required; ordinary A-shares cannot be excluded')
    if not e.reviewed.eq(True).all():raise ValueError('unreviewed instrument scope evidence rejected')
    for field in ['effective_from','valid_through','announcement_date']:
        e[field]=pd.to_datetime(e[field])
        if e[field].isna().any() or e[field].dt.tz is not None or not e[field].eq(e[field].dt.normalize()).all():
            raise ValueError('normalized instrument scope dates required')
    if e.announcement_date.ge(e.effective_from).any() or e.effective_from.gt(e.valid_through).any():
        raise ValueError('instrument type must be published before its effective date and have ordered bounds')
    if not e.source_url.map(lambda u:isinstance(u,str) and urlsplit(u).scheme=='https' and bool(urlsplit(u).hostname)).all():
        raise ValueError('instrument scope HTTPS source required')
    if not e.source_sha256.astype(str).str.fullmatch(r'[0-9a-f]{64}').all() or e.source_locator.isna().any() or e.source_locator.astype(str).str.strip().eq('').any():
        raise ValueError('instrument scope source hash and locator required')
    pages=pd.to_numeric(e.source_page,errors='raise')
    if not np.isfinite(pages).all() or pages.lt(1).any() or pages.mod(1).ne(0).any():raise ValueError('instrument scope source page required')
    if actions.symbol.isin(e.symbol).any():raise ValueError('instrument scope contradicts existing payment assets; do not remove entitlements')
    audit=[]
    for row in e.itertuples(index=False):
        security=row.symbol.split('.')[1]+'.SH'
        for index,event in c[c.security.eq(security)&c.status.isin(['missing_payment_evidence','unresolved'])].iterrows():
            date=pd.to_datetime(event.get('ex_date'),errors='coerce')
            if pd.isna(date):continue
            if date.tz is not None or date!=date.normalize():raise ValueError('normalized event date required for scope classification')
            if not (cal.min()<=date<=cal.max() and row.effective_from<=date<=row.valid_through):continue
            if date not in cal:raise ValueError('classified event must use exchange calendar')
            audit.append(dict(security=security,symbol=row.symbol,ex_date=str(date.date()),
                source_period=event.get('source_period'),original_status=event.status,
                classification='excluded_instrument',instrument_type='cdr',
                source_url=row.source_url,source_sha256=row.source_sha256,source_page=int(row.source_page)))
            c.at[index,'status']='excluded_instrument'
    payload=e.copy()
    for field in ['effective_from','valid_through','announcement_date']:payload[field]=payload[field].dt.strftime('%Y-%m-%d')
    serialized=payload.sort_values('symbol').to_json(orient='records')
    rows=pd.DataFrame(audit,columns=['security','symbol','ex_date','source_period','original_status','classification','instrument_type','source_url','source_sha256','source_page'])
    return c,rows,dict(sha256=hashlib.sha256(serialized.encode()).hexdigest(),evidence=json.loads(serialized),
        excluded_event_records=len(rows),scope='ordinary Shanghai/Shenzhen A-shares; dated reviewed CDR type only',
        limitation='No raw quote or payment removed; unreviewed codes and out-of-bound event dates remain unresolved')

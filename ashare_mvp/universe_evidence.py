"""Reviewed IPO evidence bounds; absence from an API is never non-listing proof."""
import hashlib
import json
from urllib.parse import urlsplit
import pandas as pd
from .public_data import is_ashare


def reviewed_non_listing_bounds(evidence, calendar_end, requested, master):
    required={'symbol','evidence_kind','not_listed_through','source_observed_date','source_url','source_sha256','source_locator','reviewed'}
    if not required.issubset(evidence):raise ValueError('non-listing evidence schema incomplete')
    e=evidence.copy();end=pd.Timestamp(calendar_end)
    if e.symbol.isna().any() or e.symbol.duplicated().any() or not e.symbol.map(is_ashare).all():
        raise ValueError('unique supported non-listing evidence symbols required')
    if not e.reviewed.eq(True).all():raise ValueError('unreviewed non-listing evidence rejected')
    if not set(e.symbol).issubset(set(requested)):raise ValueError('non-listing evidence outside requested identities')
    for field in ['not_listed_through','source_observed_date']:
        e[field]=pd.to_datetime(e[field])
        if e[field].isna().any() or e[field].dt.tz is not None or not e[field].eq(e[field].dt.normalize()).all():
            raise ValueError('normalized non-listing evidence dates required')
    if e.not_listed_through.gt(e.source_observed_date).any():raise ValueError('non-listing bound exceeds observed evidence date')
    if not e.evidence_kind.isin(['first_listing','pre_ipo_status']).all():raise ValueError('explicit first-listing or pre-IPO status evidence required')
    if not e.source_url.map(lambda value:isinstance(value,str) and urlsplit(value).scheme=='https' and bool(urlsplit(value).hostname)).all():
        raise ValueError('non-listing source HTTPS URL required')
    if not e.source_sha256.astype(str).str.fullmatch(r'[0-9a-f]{64}').all() or e.source_locator.isna().any() or e.source_locator.astype(str).str.strip().eq('').any():
        raise ValueError('non-listing source hash and fact locator required')
    known=master.set_index('code')
    for row in e.itertuples(index=False):
        if row.evidence_kind=='first_listing':
            listing=pd.Timestamp(getattr(row,'listing_date',None))
            if pd.isna(listing) or listing.tz is not None or listing!=listing.normalize() or row.not_listed_through!=listing-pd.Timedelta(days=1):
                raise ValueError('first-listing date must bound all prior dates')
        else:
            if getattr(row,'pre_ipo_stage',None) not in ['pricing','waiting_listing','suspended_issue'] or row.not_listed_through!=row.source_observed_date:
                raise ValueError('explicit observed pre-IPO stage required; null listing date is insufficient')
        if row.symbol in known.index:
            listing=pd.Timestamp(known.loc[row.symbol,'ipoDate'])
            if pd.notna(listing) and listing<=row.not_listed_through:
                raise ValueError('non-listing evidence contradicts known listing history')
    records=e.copy()
    for field in ['not_listed_through','source_observed_date']:
        records[field]=records[field].dt.strftime('%Y-%m-%d')
    payload=records.sort_values('symbol').to_json(orient='records',date_format='iso')
    excluded=set(e.loc[e.not_listed_through.ge(end),'symbol'])
    return excluded,dict(sha256=hashlib.sha256(payload.encode()).hexdigest(),bounds=json.loads(payload),
                        scope='historical listing coverage only; never a factor or trading signal',
                        excluded_through_calendar_end=sorted(excluded))

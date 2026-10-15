"""Validate reviewed implementation notices against exact raw provider events."""
import numpy as np
import pandas as pd
from .public_data import is_ashare


def reviewed_distribution_lookup(evidence, raw, calendar):
    required={'symbol','source_period','ex_date','announcement_date','available_date','pay_date','cash_per_share','share_multiplier',
              'expected_provider_cash_per_share','expected_provider_share_multiplier','expected_provider_description',
              'source_url','source_sha256','source_page','reviewed'}
    if not required.issubset(evidence):raise ValueError('reviewed distribution evidence schema incomplete')
    e=evidence.copy();cal=pd.DatetimeIndex(calendar)
    if not e.reviewed.eq(True).all():raise ValueError('unreviewed distribution notice rejected')
    if e.symbol.isna().any() or not e.symbol.map(is_ashare).all():raise ValueError('supported reviewed security required')
    for field in ['source_period','ex_date','announcement_date','available_date','pay_date']:
        e[field]=pd.to_datetime(e[field])
        if e[field].isna().any() or e[field].dt.tz is not None or not e[field].eq(e[field].dt.normalize()).all():raise ValueError('normalized reviewed event dates required')
    if e.duplicated(['symbol','source_period']).any() or e.duplicated(['symbol','ex_date']).any():raise ValueError('ambiguous reviewed distribution keys')
    if e.source_period.ge(e.ex_date).any() or e.pay_date.lt(e.ex_date).any() or not e.ex_date.isin(cal).all() or not e.available_date.isin(cal).all():raise ValueError('reviewed event dates outside execution scope')
    for field in ['cash_per_share','share_multiplier','expected_provider_cash_per_share','expected_provider_share_multiplier']:
        e[field]=pd.to_numeric(e[field],errors='raise')
        if not np.isfinite(e[field]).all() or e[field].lt(0).any():raise ValueError('invalid reviewed event amount')
    if e.share_multiplier.le(0).any() or e.expected_provider_share_multiplier.le(0).any():raise ValueError('positive reviewed share multiplier required')
    if not e.source_url.astype(str).str.startswith('https://').all() or not e.source_sha256.astype(str).str.fullmatch(r'[0-9a-f]{64}').all():raise ValueError('reviewed notice URL and hash required')
    pages=pd.to_numeric(e.source_page,errors='raise')
    if not np.isfinite(pages).all() or pages.lt(1).any() or pages.mod(1).ne(0).any():raise ValueError('reviewed notice page required')
    for field in ['ordinary_cash_per_share','special_cash_per_share']:
        if field in e:
            e[field]=pd.to_numeric(e[field],errors='raise')
            if not np.isfinite(e[field]).all() or e[field].lt(0).any():raise ValueError('invalid reviewed dividend component')
    if {'ordinary_cash_per_share','special_cash_per_share'}.issubset(e) and not np.isclose(e.ordinary_cash_per_share+e.special_cash_per_share,e.cash_per_share,rtol=0,atol=1e-8).all():raise ValueError('reviewed cash components do not sum to actual payout')
    lookup={}
    for row in e.itertuples(index=False):
        pos=cal.searchsorted(row.announcement_date,side='right')
        if pos>=len(cal) or row.available_date<cal[pos] or row.available_date>row.ex_date:raise ValueError('reviewed notice not visible before execution')
        security=row.symbol.split('.')[1]+'.'+row.symbol.split('.')[0].upper()
        selected=raw[(raw.SECUCODE==security)&pd.to_datetime(raw.source_period).eq(row.source_period)]
        if len(selected)!=1:raise ValueError('reviewed event has no unique raw provider period')
        item=selected.iloc[0]
        if item.ASSIGN_PROGRESS!='实施分配' or pd.Timestamp(item.EX_DIVIDEND_DATE)!=row.ex_date:raise ValueError('reviewed event differs from implemented provider date')
        cash=float(item.PRETAX_BONUS_RMB)/10
        shares=1+sum(0 if pd.isna(item[field]) else float(item[field])/10 for field in ['BONUS_RATIO','IT_RATIO'])
        if not np.isclose(cash,row.expected_provider_cash_per_share,rtol=0,atol=1e-8) or not np.isclose(shares,row.expected_provider_share_multiplier,rtol=0,atol=1e-8) or str(item.IMPL_PLAN_PROFILE)!=row.expected_provider_description:
            raise ValueError('reviewed evidence no longer matches original provider terms')
        lookup[(security,row.source_period)]=row
    return lookup

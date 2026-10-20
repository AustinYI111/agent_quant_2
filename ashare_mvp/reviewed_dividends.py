"""Validate reviewed implementation notices against exact raw provider events."""
import numpy as np
import pandas as pd
from .public_data import is_ashare


def reviewed_distribution_lookup(evidence, raw, calendar, code_changes=None):
    required={'symbol','source_period','ex_date','announcement_date','available_date','pay_date','cash_per_share','share_multiplier',
              'expected_provider_cash_per_share','expected_provider_share_multiplier','expected_provider_description',
              'source_url','source_sha256','source_page','reviewed'}
    if not required.issubset(evidence):raise ValueError('reviewed distribution evidence schema incomplete')
    e=evidence.copy();cal=pd.DatetimeIndex(calendar)
    if not e.reviewed.eq(True).all():raise ValueError('unreviewed distribution notice rejected')
    if e.symbol.isna().any() or not e.symbol.map(is_ashare).all():raise ValueError('supported reviewed security required')
    for field in ['source_period','ex_date','announcement_date','available_date']:
        e[field]=pd.to_datetime(e[field])
        if e[field].isna().any() or e[field].dt.tz is not None or not e[field].eq(e[field].dt.normalize()).all():raise ValueError('normalized reviewed event dates required')
    e['pay_date']=pd.to_datetime(e.pay_date)
    known_pay=e.pay_date.dropna()
    if known_pay.dt.tz is not None or not known_pay.eq(known_pay.dt.normalize()).all():raise ValueError('normalized reviewed event dates required')
    if e.duplicated(['symbol','source_period']).any() or e.duplicated(['symbol','ex_date']).any():raise ValueError('ambiguous reviewed distribution keys')
    if e.source_period.ge(e.ex_date).any() or e.pay_date.lt(e.ex_date).any() or not e.ex_date.isin(cal).all() or not e.available_date.isin(cal).all():raise ValueError('reviewed event dates outside execution scope')
    for field in ['cash_per_share','share_multiplier','expected_provider_share_multiplier']:
        e[field]=pd.to_numeric(e[field],errors='raise')
        if not np.isfinite(e[field]).all() or e[field].lt(0).any():raise ValueError('invalid reviewed event amount')
    e['expected_provider_cash_per_share']=pd.to_numeric(e.expected_provider_cash_per_share,errors='raise')
    known_cash=e.expected_provider_cash_per_share.dropna()
    if not np.isfinite(known_cash).all() or known_cash.lt(0).any():raise ValueError('invalid reviewed event amount')
    missing_cash=e.expected_provider_cash_per_share.isna()
    if (missing_cash & (e.cash_per_share.ne(0) | e.share_multiplier.le(1))).any():raise ValueError('absent provider cash anchor requires a reviewed cash-free share distribution')
    if (e.cash_per_share.gt(0)&e.pay_date.isna()).any():raise ValueError('cash dividend requires a payment date')
    if e.share_multiplier.le(0).any() or e.expected_provider_share_multiplier.le(0).any():raise ValueError('positive reviewed share multiplier required')
    if not e.source_url.astype(str).str.startswith('https://').all() or not e.source_sha256.astype(str).str.fullmatch(r'[0-9a-f]{64}').all():raise ValueError('reviewed notice URL and hash required')
    pages=pd.to_numeric(e.source_page,errors='raise')
    if not np.isfinite(pages).all() or pages.lt(1).any() or pages.mod(1).ne(0).any():raise ValueError('reviewed notice page required')
    for field in ['ordinary_cash_per_share','special_cash_per_share']:
        if field in e:
            e[field]=pd.to_numeric(e[field],errors='raise')
            known=e[field].dropna()
            if not np.isfinite(known).all() or known.lt(0).any():raise ValueError('invalid reviewed dividend component')
    component_fields=['ordinary_cash_per_share','special_cash_per_share']
    if any(field in e for field in component_fields):
        if not all(field in e for field in component_fields):raise ValueError('cash components must both be provided or both absent')
        supplied=e[component_fields].notna()
        if supplied.any(axis=1).ne(supplied.all(axis=1)).any():raise ValueError('cash components must both be provided or both absent')
        known=supplied.all(axis=1)
        if not np.isclose(e.loc[known,'ordinary_cash_per_share']+e.loc[known,'special_cash_per_share'],e.loc[known,'cash_per_share'],rtol=0,atol=1e-8).all():raise ValueError('reviewed cash components do not sum to actual payout')
    conflict=e.get('independent_conflict_reviewed',pd.Series(False,index=e.index)).fillna(False).eq(True)
    if conflict.any():
        fields={'payout_scope','expected_independent_cash_per_share','expected_independent_share_multiplier','expected_independent_pay_date'}
        if not fields.issubset(e) or not e.loc[conflict,'payout_scope'].eq('public_a_share').all():raise ValueError('reviewed public-holder scope and complete independent payment anchor required')
        for field in ['expected_independent_cash_per_share','expected_independent_share_multiplier']:
            e[field]=pd.to_numeric(e[field],errors='raise')
            selected=e.loc[conflict,field]
            if not np.isfinite(selected).all() or selected.lt(0).any():raise ValueError('invalid independent payment anchor')
        if e.loc[conflict,'expected_independent_share_multiplier'].le(0).any():raise ValueError('invalid independent share anchor')
        e['expected_independent_pay_date']=pd.to_datetime(e.expected_independent_pay_date)
        selected=e.loc[conflict,'expected_independent_pay_date']
        if selected.isna().any() or selected.dt.tz is not None or not selected.eq(selected.dt.normalize()).all():raise ValueError('normalized independent payment anchor date required')
    roots={}
    if code_changes is not None:
        from .security_identity import code_change_maps
        roots,_,_=code_change_maps(code_changes)
    e['provider_symbol']=e.get('provider_symbol',e.symbol).fillna(e.symbol)
    if not e.provider_symbol.map(is_ashare).all():raise ValueError('supported reviewed provider security required')
    for row in e.itertuples(index=False):
        if roots.get(row.provider_symbol,row.provider_symbol)!=roots.get(row.symbol,row.symbol):
            raise ValueError('reviewed provider alias requires matching verified issuer evidence')
    lookup={}
    for row in e.itertuples(index=False):
        pos=cal.searchsorted(row.announcement_date,side='right')
        if pos>=len(cal) or row.available_date<cal[pos] or row.available_date>row.ex_date:raise ValueError('reviewed notice not visible before execution')
        security=row.provider_symbol.split('.')[1]+'.'+row.provider_symbol.split('.')[0].upper()
        selected=raw[(raw.SECUCODE==security)&pd.to_datetime(raw.source_period).eq(row.source_period)]
        if len(selected)!=1:raise ValueError('reviewed event has no unique raw provider period')
        item=selected.iloc[0]
        if item.ASSIGN_PROGRESS!='实施分配' or pd.Timestamp(item.EX_DIVIDEND_DATE)!=row.ex_date:raise ValueError('reviewed event differs from implemented provider date')
        cash=float(item.PRETAX_BONUS_RMB)/10
        shares=1+sum(0 if pd.isna(item[field]) else float(item[field])/10 for field in ['BONUS_RATIO','IT_RATIO'])
        cash_anchor_matches=(pd.isna(cash) and pd.isna(row.expected_provider_cash_per_share)) or np.isclose(cash,row.expected_provider_cash_per_share,rtol=0,atol=1e-8)
        if not cash_anchor_matches or not np.isclose(shares,row.expected_provider_share_multiplier,rtol=0,atol=1e-8) or str(item.IMPL_PLAN_PROFILE)!=row.expected_provider_description:
            raise ValueError('reviewed evidence no longer matches original provider terms')
        lookup[(security,row.source_period)]=row
    return lookup


def check_reviewed_independent_payment(notice, prior):
    """A reviewed holder-class correction is anchored, never a blanket preference."""
    flag=getattr(notice,'independent_conflict_reviewed',False)
    if pd.notna(flag) and flag==True:
        if prior is None:raise ValueError('reviewed independent payment anchor has no matching event')
        if not np.isclose(prior.cash_per_share,notice.expected_independent_cash_per_share,rtol=0,atol=1e-8) or not np.isclose(prior.share_multiplier,notice.expected_independent_share_multiplier,rtol=0,atol=1e-8) or prior.pay_date!=notice.expected_independent_pay_date:
            raise ValueError('reviewed independent payment anchor changed')
    elif prior is not None:
        if not np.isclose(prior.cash_per_share,notice.cash_per_share,rtol=0,atol=1e-8) or not np.isclose(prior.share_multiplier,notice.share_multiplier,rtol=0,atol=1e-8) or (notice.cash_per_share>0 and prior.pay_date!=notice.pay_date):
            raise ValueError('reviewed notice conflicts with independent payment history')

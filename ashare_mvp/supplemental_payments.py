"""Notice-confirmed cash events absent from quarterly plan tables.

This is not a general preference for one vendor. Every supplement must match
one otherwise unmatched independent payment and cannot replace any entitlement.
"""
import numpy as np
import pandas as pd
from .public_data import is_ashare


def supplement_cash_payments(actions, unmatched, evidence, calendar):
    cal=pd.DatetimeIndex(calendar)
    if cal.empty or cal.hasnans or cal.has_duplicates or cal.tz is not None or not cal.is_monotonic_increasing or not cal.equals(cal.normalize()):
        raise ValueError('ordered normalized supplement calendar required')
    a,u,e=actions.copy(),unmatched.copy(),evidence.copy()
    required={'symbol','ex_date','announcement_date','available_date','pay_date','cash_per_share','reference_cash_per_share',
        'expected_cash_per_share','expected_pay_date','expected_source_file','expected_source_row',
        'source_url','source_sha256','source_page','payment_source_page','reference_source_page','reviewed'}
    if e.empty or not required.issubset(e) or not e.reviewed.eq(True).all():raise ValueError('explicit reviewed cash supplements required')
    if not {'symbol','ex_date','available_date','pay_date','cash_per_share','share_multiplier','source_file','source_row'}.issubset(u):raise ValueError('unmatched independent payment provenance required')
    if not {'symbol','ex_date','pay_date','cash_per_share','share_multiplier'}.issubset(a):raise ValueError('existing payment events required')
    for frame,fields in [(a,['ex_date']),(u,['ex_date','available_date','pay_date']),(e,['ex_date','announcement_date','available_date','pay_date','expected_pay_date'])]:
        for field in fields:
            frame[field]=pd.to_datetime(frame[field],format='mixed')
            if frame[field].isna().any() or frame[field].dt.tz is not None or not frame[field].eq(frame[field].dt.normalize()).all():raise ValueError('normalized non-null supplement dates required')
        if frame[['symbol','ex_date']].isna().any().any() or frame.duplicated(['symbol','ex_date']).any():raise ValueError('unique supplement event identities required')
    if not e.symbol.map(is_ashare).all():raise ValueError('A-share cash supplements only')
    if not e.source_url.astype(str).str.startswith('https://').all() or not e.source_sha256.astype(str).str.fullmatch(r'[0-9a-f]{64}').all():raise ValueError('official supplement URL and hash required')
    for field in ['source_page','payment_source_page','reference_source_page','expected_source_row']:
        values=pd.to_numeric(e[field],errors='raise')
        if not np.isfinite(values).all() or values.mod(1).ne(0).any() or values.lt(0 if field=='expected_source_row' else 1).any():raise ValueError('valid supplement page/row locator required')
    for field in ['cash_per_share','reference_cash_per_share','expected_cash_per_share']:
        e[field]=pd.to_numeric(e[field],errors='raise')
        if not np.isfinite(e[field]).all() or e[field].lt(0).any():raise ValueError('finite nonnegative supplemental cash required')
    if e.cash_per_share.le(0).any():raise ValueError('cash-only supplements cannot classify a share reorganization')
    audit=[];used=[]
    for event in e.itertuples():
        if ((a.symbol==event.symbol)&a.ex_date.eq(event.ex_date)).any():raise ValueError('supplement cannot replace an existing event')
        selected=u[(u.symbol==event.symbol)&u.ex_date.eq(event.ex_date)]
        if len(selected)!=1:raise ValueError('supplement requires an unmatched independent payment')
        prior=selected.iloc[0]
        if (float(prior.share_multiplier)!=1 or not np.isclose(float(prior.cash_per_share),event.expected_cash_per_share,rtol=0,atol=1e-12)
            or not np.isclose(event.cash_per_share,event.expected_cash_per_share,rtol=0,atol=1e-12)
            or prior.pay_date!=event.expected_pay_date or event.pay_date!=event.expected_pay_date
            or prior.source_file!=event.expected_source_file or int(prior.source_row)!=event.expected_source_row):
            raise ValueError('supplement differs from exact independent cash/date/provenance anchor')
        position=cal.searchsorted(event.announcement_date,side='right')
        if (position>=len(cal) or event.ex_date not in cal or event.available_date not in cal or event.available_date<cal[position]
            or event.available_date>event.ex_date or prior.available_date>event.ex_date or event.pay_date<event.ex_date):
            raise ValueError('supplement notice/payment clock invalid')
        row=dict(symbol=event.symbol,provider_symbol=prior.get('source_symbol',event.symbol),payment_provider_symbol=event.symbol,
            ex_date=event.ex_date,available_date=max(event.available_date,prior.available_date),pay_date=event.pay_date,
            cash_per_share=event.cash_per_share,reference_cash_per_share=event.reference_cash_per_share,share_multiplier=1.,
            source_period=None,event_vintage_certified=False,event_evidence_type='notice_confirmed_independent_cash_supplement',
            source_notice_url=event.source_url,source_notice_sha256=event.source_sha256,source_notice_page=event.source_page,
            source_payment_page=event.payment_source_page,reference_source_url=event.source_url,reference_source_sha256=event.source_sha256,
            reference_source_page=event.reference_source_page,independent_source_file=prior.source_file,independent_source_row=int(prior.source_row))
        a=pd.concat([a,pd.DataFrame([row])],ignore_index=True)
        used.append(selected.index[0]);audit.append(dict(symbol=event.symbol,ex_date=event.ex_date,cash_per_share=event.cash_per_share,reference_cash_per_share=event.reference_cash_per_share,source_url=event.source_url,source_sha256=event.source_sha256))
    return a,u.drop(index=used),pd.DataFrame(audit)

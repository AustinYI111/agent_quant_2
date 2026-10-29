"""Primary-notice cash omitted from a sealed retrospective valuation input.

This extends valuation events only. It never fills training labels, changes an
existing entitlement, or claims that an announced payment was bank-confirmed.
"""
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlparse
import json
import numpy as np
import pandas as pd
from .phase2_protocol import sha256,utc,day
from .public_data import is_ashare


def supplement_missing_valuation_cash(parent_file,evidence_file,project,calendar,research_end):
    root=Path(project).resolve()
    def local(value):
        p=Path(value)
        if p.is_absolute() or '..' in p.parts or ':' in str(p):raise ValueError('project-relative payment source required')
        result=(root/p).resolve()
        if not result.is_relative_to(root):raise ValueError('payment source escapes project')
        return result
    parent,review=local(parent_file),local(evidence_file)
    config=json.loads(review.read_text(encoding='utf8'));parent_hash=sha256(parent)
    if config.get('schema')!='official_missing_research_cash_v1' or config.get('expected_parent_actions_sha256')!=parent_hash:
        raise ValueError('explicit sealed-parent missing-cash evidence required')
    cal=pd.DatetimeIndex(pd.to_datetime(calendar));end=day(research_end)
    if cal.empty or cal.hasnans or cal.has_duplicates or cal.tz is not None or not cal.is_monotonic_increasing or not cal.equals(cal.normalize()) or cal.max()>end:
        raise ValueError('ordered research-only exchange calendar required')
    actions=pd.read_csv(parent,float_precision='round_trip',low_memory=False)
    for column in ['ex_date','available_date','pay_date']:actions[column]=pd.to_datetime(actions[column],format='mixed')
    if actions.duplicated(['symbol','ex_date']).any():raise ValueError('unique parent payment identities required')
    records=pd.DataFrame(config.get('events',[]))
    required={'symbol','ex_date','announcement_date','available_date','pay_date','cash_per_share','share_multiplier','payout_scope','source_url','source_sha256','source_page','payment_source_page','reviewed','source_file','source_capture_file','source_capture_sha256','observed_at_utc'}
    if records.empty or not required.issubset(records) or not records.reviewed.eq(True).all():raise ValueError('reviewed ordinary cash source and payment pages required')
    if not records.symbol.map(is_ashare).all() or not records.payout_scope.eq('ordinary_a_share_holders').all() or not records.share_multiplier.eq(1.).all():
        raise ValueError('ordinary A-share cash-only supplements required')
    records['cash_per_share']=pd.to_numeric(records.cash_per_share,errors='raise')
    if not np.isfinite(records.cash_per_share).all() or records.cash_per_share.le(0).any():raise ValueError('positive literal gross cash required')
    for column in ['ex_date','announcement_date','available_date','pay_date']:records[column]=records[column].map(day)
    if records.duplicated(['symbol','ex_date']).any() or records.ex_date.gt(end).any():raise ValueError('unique research-only missing payments required')
    for column in ['source_page','payment_source_page']:
        value=pd.to_numeric(records[column],errors='raise')
        if not np.isfinite(value).all() or value.lt(1).any() or value.mod(1).ne(0).any():raise ValueError('positive integer primary source pages required')
    hashes={parent.relative_to(root).as_posix():parent_hash,review.relative_to(root).as_posix():sha256(review)}
    added=[]
    for row in records.itertuples(index=False):
        if ((actions.symbol==row.symbol)&actions.ex_date.eq(row.ex_date)).any():raise ValueError('missing payment cannot replace an existing event')
        pos=cal.searchsorted(row.announcement_date,side='right')
        if pos>=len(cal) or row.available_date not in cal or row.ex_date not in cal or row.available_date<cal[pos] or row.available_date>row.ex_date or row.pay_date<row.ex_date:
            raise ValueError('primary cash publication/ex/payment clocks invalid')
        source,capture=local(row.source_file),local(row.source_capture_file)
        if sha256(source)!=row.source_sha256 or sha256(capture)!=row.source_capture_sha256:raise ValueError('primary payment notice or capture changed')
        with source.open('rb') as stream:
            if stream.read(5)!=b'%PDF-':raise ValueError('primary payment notice must be PDF')
        if urlparse(row.source_url).scheme!='https' or urlparse(row.source_url).hostname!='static.cninfo.com.cn':raise ValueError('original official notice host required')
        observed=utc(row.observed_at_utc)
        if observed>pd.Timestamp(datetime.now(timezone.utc)):raise ValueError('future cash notice observation rejected')
        raw=json.loads(capture.read_text(encoding='utf8'))
        if raw.get('schema')!='original_candidate_notice_capture_v1' or raw.get('code')!=row.symbol.split('.')[-1] or raw.get('ex_date')!=str(row.ex_date.date()):
            raise ValueError('cash notice capture identity differs')
        matching=[r for r in raw.get('notices',[]) if r['sha256']==row.source_sha256 and r['url']==row.source_url]
        if len(matching)!=1 or (capture.parent/matching[0]['file']).resolve()!=source or utc(matching[0]['observed_at_utc'])!=observed:
            raise ValueError('original cash notice observation must be preserved')
        hashes[source.relative_to(root).as_posix()]=row.source_sha256;hashes[capture.relative_to(root).as_posix()]=row.source_capture_sha256
        added.append(dict(symbol=row.symbol,provider_symbol=row.symbol,payment_provider_symbol=row.symbol,
            ex_date=row.ex_date,available_date=row.available_date,pay_date=row.pay_date,cash_per_share=row.cash_per_share,
            share_multiplier=1.,reference_cash_per_share=row.cash_per_share,reference_share_multiplier=1.,
            event_evidence_type='official_cash_missing_from_sealed_valuation_input',event_vintage_certified=False,
            source_notice_url=row.source_url,source_notice_sha256=row.source_sha256,source_notice_page=int(row.source_page),
            source_payment_page=int(row.payment_source_page),first_observed_at_utc=row.observed_at_utc,independent_payment_confirmation=False))
    result=pd.concat([actions,pd.DataFrame(added)],ignore_index=True)
    pd.testing.assert_frame_equal(actions,result.iloc[:len(actions)][actions.columns].reset_index(drop=True),check_dtype=False,check_exact=True)
    for name,digest in hashes.items():
        if sha256(root/name)!=digest:raise ValueError('cash evidence changed during construction')
    return result,dict(schema='official_missing_valuation_cash_supplement_v1',added_events=len(added),source_hashes=hashes,
        existing_entitlements_unchanged=True,missing_entitlements_corrected=True,training_features_or_labels_changed=False,
        independent_payment_confirmation=False,historical_vintages_certified=False,
        limitation='Missing from the sealed valuation event input, not a claim that all vendor responses were empty. Announced primary cash and dates; no bank-payment confirmation.')

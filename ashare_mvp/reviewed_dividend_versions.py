"""Explicit implementation-notice resolution of sealed raw duplicate keys."""
from pathlib import Path
import re
import numpy as np
import pandas as pd


def reviewed_version_lookup(evidence, calendar):
    required = {'source_file','expected_file_sha256','source_rows','selected_source_row','symbol','ex_date',
        'expected_raw_cash_per_share','expected_raw_share_multiplier','expected_raw_description',
        'cash_per_share','share_multiplier','announcement_date','available_date','pay_date',
        'source_url','source_sha256','source_page','payment_source_page','reviewed'}
    if not required.issubset(evidence): raise ValueError('reviewed raw version schema incomplete')
    e=evidence.copy();cal=pd.DatetimeIndex(calendar)
    if not e.reviewed.eq(True).all(): raise ValueError('unreviewed raw version rejected')
    for field in ['ex_date','announcement_date','available_date','pay_date']:
        e[field]=pd.to_datetime(e[field])
        if e[field].isna().any() or e[field].dt.tz is not None or not e[field].eq(e[field].dt.normalize()).all():
            raise ValueError('normalized reviewed raw version clocks required')
    if e.duplicated(['source_file','ex_date']).any(): raise ValueError('ambiguous reviewed raw version keys')
    for field in ['expected_file_sha256','source_sha256']:
        if not e[field].astype(str).str.fullmatch(r'[0-9a-f]{64}').all(): raise ValueError('reviewed raw source SHA256 required')
    if not e.source_url.astype(str).str.startswith('https://').all(): raise ValueError('reviewed raw source URL required')
    for field in ['cash_per_share','expected_raw_cash_per_share','share_multiplier','expected_raw_share_multiplier']:
        e[field]=pd.to_numeric(e[field],errors='raise')
        if not np.isfinite(e[field]).all() or e[field].lt(0).any(): raise ValueError('invalid reviewed raw version terms')
    if e.share_multiplier.le(0).any() or not np.isclose(e.share_multiplier,e.expected_raw_share_multiplier,rtol=0,atol=1e-12).all():
        raise ValueError('share changes require separate implementation review')
    for field in ['source_page','payment_source_page','selected_source_row']:
        e[field]=pd.to_numeric(e[field],errors='raise')
        if not np.isfinite(e[field]).all() or e[field].mod(1).ne(0).any() or e[field].lt(0 if field=='selected_source_row' else 1).any():
            raise ValueError('invalid reviewed source page/row')
    lookup={}
    for row in e.to_dict('records'):
        name=row['source_file'];indices=row['source_rows']
        if not isinstance(name,str) or Path(name).name!=name:raise ValueError('reviewed raw filename required')
        if not isinstance(indices,list) or len(indices)<2 or any(isinstance(i,bool) or not isinstance(i,int) or i<0 for i in indices) or indices!=sorted(set(indices)) or row['selected_source_row'] not in indices:
            raise ValueError('complete explicit raw duplicate row anchors required')
        position=cal.searchsorted(row['announcement_date'],side='right')
        if position>=len(cal) or row['ex_date'] not in cal or row['available_date'] not in cal or not cal[position]<=row['available_date']<=row['ex_date'] or row['pay_date']<row['ex_date']:
            raise ValueError('reviewed raw version not visible before execution')
        lookup[(name,row['ex_date'])]=row
    return lookup


def resolve_raw_versions(frame, name, digest, selected, provenance, audit, conflicts, reviews):
    """Review can select a conflicting raw row; it cannot silently repair clocks."""
    from .dividend_normalization import _terms
    overrides={};used=set()
    for key,notice in reviews.items():
        if key[0]!=name: continue
        if digest!=notice['expected_file_sha256']:raise ValueError('reviewed raw file hash mismatch')
        indices=list(frame.index[pd.to_datetime(frame.dividOperateDate,errors='coerce').eq(notice['ex_date'])])
        if indices!=notice['source_rows'] or not set(indices).issubset(conflicts):
            raise ValueError('reviewed raw duplicate group anchors differ')
        index=int(notice['selected_source_row']);row=frame.loc[index];terms=_terms(row)
        if row.code!=notice['symbol'] or str(row.dividCashStock)!=notice['expected_raw_description'] or not np.isclose(terms['dividCashPsBeforeTax'],notice['expected_raw_cash_per_share'],rtol=0,atol=1e-12) or not np.isclose(1+terms['dividStocksPs']+terms['dividReserveToStockPs'],notice['expected_raw_share_multiplier'],rtol=0,atol=1e-12):
            raise ValueError('reviewed selected raw terms differ')
        if pd.Timestamp(row.dividPlanDate)!=notice['announcement_date'] or pd.Timestamp(row.dividPayDate)!=notice['pay_date']:
            raise ValueError('reviewed raw announcement/payment clock differs')
        selected.append(index);selected.sort();provenance[index]=indices
        conflicts[:]=[i for i in conflicts if i not in indices]
        for record in audit:
            if pd.Timestamp(record['ex_date'])==notice['ex_date']:
                record.update(status='resolved_reviewed_implementation_notice',selected_source_row=index,
                    source_url=notice['source_url'],source_sha256=notice['source_sha256'])
        overrides[index]=notice;used.add(key)
    return overrides,used

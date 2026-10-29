"""Retain known financial versions when a new daily observation is not visible."""
import numpy as np
import pandas as pd

FUND_COLUMNS=['symbol','report_date','available_date','roe','revenue_growth','earnings_growth','debt_ratio']


def merge_dependency_observations(panels):
    """Keep dependency versions before the same-period income/balance join.

    Provider aliases remain explicit; the reviewed identity join runs next.
    Missing new observations do not erase a previously visible dependency.
    """
    frames=[]
    for panel in panels:
        if not {'symbol','report_date','available_date','first_observed_at_utc'}.issubset(panel):raise ValueError('dependency observation schema missing')
        frame=panel.copy()
        for c in ['report_date','available_date']:frame[c]=pd.to_datetime(frame[c],format='mixed')
        frame['first_observed_at_utc']=pd.to_datetime(frame.first_observed_at_utc,utc=True,format='mixed')
        frame=frame[frame.symbol.notna()&frame.available_date.notna()].copy()
        if frame.report_date.isna().any() or frame.first_observed_at_utc.isna().any():raise ValueError('known dependency period and observation clock required')
        local=frame.first_observed_at_utc.dt.tz_convert('Asia/Shanghai').dt.tz_localize(None).dt.normalize()
        if frame.available_date.lt(local).any() or frame.first_observed_at_utc.gt(pd.Timestamp.now(tz='UTC')).any():raise ValueError('dependency observation backdated or in future')
        frames.append(frame)
    if not frames:raise ValueError('explicit dependency observations required')
    merged=pd.concat(frames,ignore_index=True)
    keys=['symbol','report_date','available_date','first_observed_at_utc']
    duplicate=merged[merged.duplicated(keys,keep=False)]
    for _,group in duplicate.groupby(keys,dropna=False):
        if len(group.drop_duplicates())!=1:raise ValueError('conflicting dependency payload at identical observation clock')
    return merged.sort_values(keys,kind='stable').drop_duplicates(keys[:3],keep='last').reset_index(drop=True)


def merge_observed_financial_history(baseline,observations,now=None):
    """Latest observation wins same-day ties; older available dates remain.

    Original immutable panel files retain every observation. A later capture
    cannot backdate its visibility. No fields are filled across report periods.
    """
    clock=pd.Timestamp.now(tz='UTC') if now is None else pd.Timestamp(now)
    if clock.tzinfo is None:raise ValueError('timezone-aware financial observation cutoff required')
    if not set(FUND_COLUMNS).issubset(baseline):raise ValueError('baseline financial columns missing')
    old=baseline[FUND_COLUMNS].copy();old['_source_order']=0;old['_observed_utc']=pd.Timestamp('1900-01-01',tz='UTC')
    frames=[old]
    for observed_frame in observations:
        if not {*FUND_COLUMNS,'first_observed_at_utc'}.issubset(observed_frame):raise ValueError('observed financial history schema missing')
        frame=observed_frame.copy()
        for column in ['report_date','available_date']:frame[column]=pd.to_datetime(frame[column],format='mixed')
        observed=pd.to_datetime(frame.first_observed_at_utc,utc=True);visible=frame.available_date.notna()
        if observed[visible].isna().any() or observed.max()>clock:raise ValueError('unknown or future financial observation')
        if frame.loc[visible,'available_date'].lt(observed[visible].dt.tz_convert('Asia/Shanghai').dt.tz_localize(None).dt.normalize()).any():raise ValueError('financial availability precedes first observation')
        for column in FUND_COLUMNS[3:]:
            frame[column]=pd.to_numeric(frame[column],errors='raise')
            if np.isinf(frame[column]).any():raise ValueError('infinite observed financial value')
        frame['_observed_utc']=observed;frame['_source_order']=1
        frames.append(frame[FUND_COLUMNS+['_source_order','_observed_utc']])
    funds=pd.concat(frames,ignore_index=True)
    for column in FUND_COLUMNS[3:]:funds[column]=pd.to_numeric(funds[column],errors='raise').astype(float)
    for column in ['report_date','available_date']:
        funds[column]=pd.to_datetime(funds[column],format='mixed')
        known=funds[column].dropna()
        if known.dt.tz is not None or not known.eq(known.dt.normalize()).all():raise ValueError('normalized financial dates required')
    if funds[['symbol','report_date']].isna().any().any() or (funds.available_date.notna()&funds.report_date.gt(funds.available_date)).any():raise ValueError('financial report/key is in its availability future')
    observed=funds[funds._source_order.eq(1)&funds.available_date.notna()]
    duplicate_key=['symbol','report_date','available_date','_observed_utc']
    duplicates=observed[observed.duplicated(duplicate_key,keep=False)]
    for _,group in duplicates.groupby(duplicate_key,dropna=False):
        for column in FUND_COLUMNS[3:]:
            if group[column].nunique(dropna=False)>1:raise ValueError('conflicting financial values at identical observation clock')
    return funds.sort_values(['symbol','report_date','available_date','_source_order','_observed_utc'],kind='stable').drop_duplicates(['symbol','report_date','available_date'],keep='last')[FUND_COLUMNS].reset_index(drop=True)

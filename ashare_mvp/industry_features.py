"""Equal-weight industry strength from membership actually observed by signal day.

No latest-membership historical backfill, no labels, no tunable membership floor.
"""
import numpy as np
import pandas as pd
from .phase2_protocol import day,utc

INDUSTRY_FEATURES=['industry_relative_momentum_63','industry_relative_momentum_126','industry_breadth_50']


def industry_snapshot_features(snapshot,memberships,signal_date,decision_at):
    required={'symbol','raw_momentum_63','raw_momentum_126','close_above_ma50'}
    if not required.issubset(snapshot) or snapshot.symbol.isna().any() or snapshot.symbol.duplicated().any():
        raise ValueError('unique eligible snapshot and raw industry inputs required')
    signal,created=day(signal_date),utc(decision_at)
    cutoff=(signal+pd.Timedelta(hours=17,minutes=20)).tz_localize('Asia/Shanghai').tz_convert('UTC')
    if created<cutoff:raise ValueError('industry daily snapshot precedes close release cutoff')
    if 'date' in snapshot and not pd.to_datetime(snapshot.date).eq(signal).all():raise ValueError('mixed industry signal dates')
    if not {'symbol','industry','effective_date','observed_at_utc'}.issubset(memberships):
        raise ValueError('dated and first-observed membership evidence required')
    m=memberships.copy()
    m['effective_date']=m.effective_date.map(day)
    m['observed_at_utc']=pd.Series(m.observed_at_utc.map(utc),index=m.index,dtype='datetime64[ns, UTC]')
    if m.symbol.isna().any() or m.duplicated(['symbol','effective_date','observed_at_utc']).any():
        raise ValueError('ambiguous membership revision keys')
    observed_day=m.observed_at_utc.dt.tz_convert('Asia/Shanghai').dt.tz_localize(None).dt.normalize()
    known=m.effective_date.le(signal)&m.observed_at_utc.le(created)&observed_day.le(signal)
    m=m[known].sort_values(['symbol','effective_date','observed_at_utc']).groupby('symbol',sort=False).tail(1)
    p=snapshot.merge(m[['symbol','industry']],on='symbol',how='left',validate='one_to_one')
    p['industry']=p.industry.where(p.industry.notna()&p.industry.astype(str).str.strip().ne(''))
    for column in ['raw_momentum_63','raw_momentum_126','close_above_ma50']:
        p[column]=pd.to_numeric(p[column],errors='raise')
        if np.isinf(p[column]).any():raise ValueError('nonfinite raw industry input')
    if not p.close_above_ma50.dropna().isin([0.,1.]).all():raise ValueError('invalid industry breadth input')
    result=p[['symbol','industry']].copy()
    result['industry_known']=p.industry.notna()
    for source,feature in [('raw_momentum_63','industry_relative_momentum_63'),('raw_momentum_126','industry_relative_momentum_126'),('close_above_ma50','industry_breadth_50')]:
        groups=p.groupby('industry',dropna=True,sort=False)[source]
        mean=groups.transform('mean');count=groups.transform('count')
        reference=0. if source=='close_above_ma50' else p[source].mean()
        result[feature]=(mean-reference).where(count.ge(10)&p.industry.notna())
    result['date']=signal
    return result

"""Descriptive sector contributions from reconciled raw-share account ledgers."""
import numpy as np
import pandas as pd
from .archived_industry_research import select_archived_research_memberships

TERMS=['price_pnl','gross_dividend_entitlement','commission_cost','slippage_cost','stamp_duty_cost']


def historical_labels(keys,memberships,releases,calendar,research_end,audited_at=None):
    """Classify the open-to-open interval using information at its starting open."""
    cal=pd.DatetimeIndex(pd.to_datetime(calendar));k=keys[['date','symbol']].copy().reset_index(drop=True)
    if cal.empty or cal.hasnans or cal.has_duplicates or not cal.is_monotonic_increasing:raise ValueError('ordered unique nonempty exchange calendar required')
    k['date']=pd.to_datetime(k.date)
    if k[['date','symbol']].isna().any().any() or k.duplicated(['date','symbol']).any() or not k.date.isin(cal).all():raise ValueError('unique research-session attribution keys required')
    # Validate the entire catalog/membership contract through the shared gate.
    select_archived_research_memberships(memberships,releases,cal,cal[-1],research_end,audited_at)
    catalog=releases.copy();catalog['published_date']=pd.to_datetime(catalog.published_date)
    idx=cal.searchsorted(catalog.published_date,side='right')
    catalog['visible_date']=[cal[i] if i<len(cal) else pd.NaT for i in idx]
    catalog=catalog.dropna(subset=['visible_date']).sort_values(['visible_date','published_date'])
    previous={date:(cal[i-1] if i else pd.NaT) for date,i in zip(pd.DatetimeIndex(k.date.unique()),cal.get_indexer(k.date.unique()))}
    k['classification_date']=k.date.map(previous)
    schedule={};snapshots={}
    for asof in k.classification_date.dropna().unique():
        visible=catalog[catalog.visible_date.le(asof)]
        if visible.empty:continue
        r=visible.iloc[-1];schedule[pd.Timestamp(asof)]=r
        if r.release_id not in snapshots:
            snapshots[r.release_id]=select_archived_research_memberships(memberships,releases,cal,asof,research_end,audited_at).set_index('symbol').industry.to_dict()
    parts=[]
    for asof,rows in k.groupby('classification_date',dropna=False,sort=False):
        f=rows.copy();r=schedule.get(asof)
        f['industry']=f.symbol.map(snapshots[r.release_id]).fillna('__UNKNOWN__') if r is not None else '__UNKNOWN__'
        f['release_id']=r.release_id if r is not None else None
        f['published_date']=r.published_date if r is not None else pd.NaT
        f['first_observed_at_utc']=r.first_observed_at_utc if r is not None else None
        parts.append(f)
    if not parts:return k.assign(industry=pd.Series(dtype=str),release_id=pd.Series(dtype=str),published_date=pd.NaT,first_observed_at_utc=pd.Series(dtype=str))
    return pd.concat(parts).sort_index()


def attribute_industries(daily,assets,positions,memberships,releases,calendar,research_end,audited_at=None):
    """Group actual PnL/costs and allocate market reference by starting exposure.

    This does not estimate sector-factor returns, selection alpha or causality.
    Unknown classification remains an explicit bucket; monetary values may not
    be missing. Cash/receivables are separate from unclassified equity exposure.
    """
    d,a,p=daily.copy(),assets.copy(),positions.copy()
    if not {'date','opening_nav','nav_change','net_return','unit_beta_market_reference','price_residual_after_market',*TERMS}.issubset(d) or not {'date','symbol',*TERMS}.issubset(a) or not {'date','symbol','market_value'}.issubset(p):raise ValueError('complete independently reconciled account ledgers required')
    for frame in [d,a,p]:frame['date']=pd.to_datetime(frame.date)
    if d.empty or d.date.duplicated().any() or not d.date.is_monotonic_increasing or a.duplicated(['date','symbol']).any() or p.duplicated(['date','symbol']).any() or not a.date.isin(d.date).all() or not p.date.isin(d.date).all():raise ValueError('unique ordered complete account dates required')
    for frame,columns in [(d,['opening_nav','nav_change','net_return',*TERMS]),(a,TERMS),(p,['market_value'])]:
        if not np.isfinite(frame[columns].to_numpy(float)).all():raise ValueError('unknown monetary contributions cannot become zero')
    if d.opening_nav.le(0).any() or p.market_value.lt(0).any():raise ValueError('positive opening NAV and nonnegative known position values required')
    cal=pd.DatetimeIndex(pd.to_datetime(calendar));indexes=cal.get_indexer(d.date)
    if (indexes<0).any() or (np.diff(indexes)!=1).any():raise ValueError('continuous actual exchange sessions required')
    # Positions are after each open's fills. Shift them to the next session so
    # exposure belongs to the same interval as independently reconstructed PnL.
    next_session={date:(cal[i+1] if i+1<len(cal) else pd.NaT) for date,i in zip(pd.DatetimeIndex(p.date.unique()),cal.get_indexer(p.date.unique()))}
    old=p[['date','symbol','market_value']].copy();old['date']=old.date.map(next_session)
    old=old[old.date.isin(d.date)].rename(columns={'market_value':'starting_market_value'})
    ledger=a.merge(old,on=['date','symbol'],how='outer',validate='one_to_one')
    # Missing rows mean an absent position or trade, not an unknown asset quote.
    for col in TERMS+['starting_market_value']:ledger[col]=ledger[col].fillna(0.)
    labels=historical_labels(ledger[['date','symbol']],memberships,releases,cal,research_end,audited_at)
    ledger=ledger.merge(labels,on=['date','symbol'],validate='one_to_one')
    grouped=ledger.groupby(['date','industry'],as_index=False,dropna=False)[TERMS+['starting_market_value']].sum()
    missing=d.loc[~d.date.isin(grouped.date),'date']
    if len(missing):grouped=pd.concat([grouped,pd.DataFrame(dict(date=missing,industry='__NO_POSITION__',**{c:0. for c in TERMS+['starting_market_value']}))],ignore_index=True)
    grouped=grouped.merge(d[['date','opening_nav','unit_beta_market_reference']],on='date',validate='many_to_one')
    total=grouped.groupby('date').starting_market_value.transform('sum')
    if (total>grouped.opening_nav+1e-10).any():raise ValueError('starting invested value exceeds opening NAV')
    if (total.eq(0)&grouped.unit_beta_market_reference.notna()&grouped.unit_beta_market_reference.ne(0)).any():raise ValueError('nonzero market contribution without starting exposure')
    grouped['starting_equity_weight']=grouped.starting_market_value/grouped.opening_nav
    grouped['cash_and_receivable_weight']=1-total/grouped.opening_nav
    grouped['allocated_market_reference']=grouped.unit_beta_market_reference*grouped.starting_market_value.div(total.where(total.gt(0)))
    grouped.loc[total.eq(0)&grouped.unit_beta_market_reference.eq(0),'allocated_market_reference']=0.
    grouped['price_residual_after_market']=grouped.price_pnl-grouped.allocated_market_reference
    grouped['net_pnl']=grouped.price_pnl+grouped.gross_dividend_entitlement-grouped[['commission_cost','slippage_cost','stamp_duty_cost']].sum(axis=1)
    grouped['net_return_contribution']=grouped.net_pnl/grouped.opening_nav
    totals=grouped.groupby('date')[TERMS+['net_pnl','net_return_contribution','allocated_market_reference','price_residual_after_market']].sum(min_count=1)
    expected=d.set_index('date');checks=pd.DataFrame(index=expected.index)
    for left,right in [(c,c) for c in TERMS]+[('net_pnl','nav_change'),('net_return_contribution','net_return'),('allocated_market_reference','unit_beta_market_reference'),('price_residual_after_market','price_residual_after_market')]:
        checks[left+'_error']=totals[left]-expected[right]
        known=expected[right].notna()
        if checks.loc[known,left+'_error'].isna().any() or checks.loc[known,left+'_error'].abs().gt(1e-10).any():raise ValueError('industry contributions do not reconcile: '+left)
    grouped=grouped.drop(columns=['unit_beta_market_reference']).sort_values(['date','industry']).reset_index(drop=True)
    return grouped,checks.reset_index(),labels

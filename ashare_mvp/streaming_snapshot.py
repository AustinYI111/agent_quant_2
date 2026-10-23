"""Bounded-memory feature snapshots using the frozen causal research rules.

Feature-index segment resets never modify a portfolio, cash or real holdings.
Unknown factors remain native NaN, as in the existing frozen public-ratio model.
"""
import numpy as np
import pandas as pd
from .data import Dataset
from .factors import technical_panel
from .phase2_protocol import day
from .streaming_feature_index import ExchangeCalendar,StreamingFeatureIndex


FEATURE_COLUMNS=['date','symbol','quality_segment','close','volume','listing_date','is_st','eligible','earnings_yield','book_yield','trading_symbol']
BREAK_COLUMNS=['symbol','date','reference_fault','previous_date','previous_close','preclose','has_payment_event']


def iter_seed_bars(path,chunksize=100000):
    """Read an issuer-sorted sealed seed; caller verifies its manifest/hash."""
    if isinstance(chunksize,bool) or not isinstance(chunksize,int) or chunksize<1:raise ValueError('positive integer seed chunk size required')
    carry=pd.DataFrame();previous=None
    for chunk in pd.read_csv(path,parse_dates=['date','listing_date'],float_precision='round_trip',chunksize=chunksize):
        if not set(FEATURE_COLUMNS).issubset(chunk) or chunk.empty or chunk.symbol.isna().any():raise ValueError('feature seed schema incomplete')
        frame=pd.concat([carry,chunk],ignore_index=True) if not carry.empty else chunk
        if not frame.symbol.is_monotonic_increasing:raise ValueError('feature seed must be issuer sorted')
        last=frame.symbol.iloc[-1]
        for symbol,group in frame[frame.symbol.ne(last)].groupby('symbol',sort=False):
            if previous is not None and symbol<=previous:raise ValueError('feature seed repeats an issuer')
            _seed_dates(group);previous=symbol;yield symbol,group
        carry=frame[frame.symbol.eq(last)].copy()
    if not carry.empty:
        symbol=carry.symbol.iloc[0]
        if previous is not None and symbol<=previous:raise ValueError('feature seed repeats an issuer')
        _seed_dates(carry);yield symbol,carry


def _seed_dates(frame):
    dates=pd.DatetimeIndex(frame.date)
    if dates.tz is not None or dates.hasnans or dates.has_duplicates or not dates.is_monotonic_increasing or not dates.equals(dates.normalize()):raise ValueError('unique ordered normalized seed dates required')


def _risk_flag(value):
    if isinstance(value,(bool,np.bool_)):return bool(value)
    if pd.isna(value):raise ValueError('unknown raw ST status')
    if value in [0,1,'0','1']:return bool(int(value))
    raise ValueError('unknown raw ST status')


def advance_feature_snapshot(symbol,seed_bars,state,raw_new,events,calendar,listing_date,signal_date,reference_tick_size=.01,volume_audit=None):
    """Advance one issuer, returning state, latest technical row and break audit.

    This shares the original feature and segment implementations. It cannot be
    used to value holdings, infer missing payments or classify a live forecast.
    """
    cal=calendar if isinstance(calendar,ExchangeCalendar) else ExchangeCalendar(calendar)
    signal=day(signal_date)
    if signal not in cal.positions:raise ValueError('signal outside exchange calendar')
    if not isinstance(events,dict):raise ValueError('explicit dated event mapping required')
    machine=StreamingFeatureIndex(symbol,cal,reference_tick_size,snapshot=state)
    old=seed_bars.copy()
    if not old.empty:
        if state is None or not set(FEATURE_COLUMNS).issubset(old) or not old.symbol.eq(symbol).all():raise ValueError('seed/state/issuer mismatch')
        old['date']=pd.to_datetime(old.date,format='mixed');_seed_dates(old)
        if old.date.gt(day(state['last_date'])).any() or old.quality_segment.gt(state['segment']).any():raise ValueError('feature seed exceeds persisted state')
    if raw_new.empty:raise ValueError('new observed raw bars required')
    raw=raw_new.copy();raw['date']=pd.to_datetime(raw.date,format='mixed');_seed_dates(raw)
    if not raw.symbol.eq(symbol).all() or raw.date.gt(signal).any():raise ValueError('raw input issuer/future date differs')
    listing=pd.to_datetime(listing_date)
    if pd.notna(listing) and (listing.tz is not None or listing!=listing.normalize()):raise ValueError('normalized listing date required')
    bars=[];breaks=[]
    for row in raw.to_dict('records'):
        volume=float(row['volume']) if pd.notna(row['volume']) else float('nan')
        suspended=str(row['tradestatus'])=='0'
        missing_suspended=bool(np.isnan(volume) and suspended)
        if not missing_suspended and (not np.isfinite(volume) or volume<0):raise ValueError('observed nonnegative volume required outside missing suspended bars')
        if suspended and np.isfinite(volume) and volume!=0:raise ValueError('suspended bar has positive volume')
        if missing_suspended and volume_audit is not None:
            volume_audit.append(dict(symbol=symbol,date=row['date'],tradestatus='0',raw_volume_missing=True,handling='preserved_native_nan',ranking_eligible=False))
        risk=_risk_flag(row['is_st']);event=events.get(row['date'])
        previous=machine.snapshot();result=machine.advance(row,event)
        bar=dict(date=row['date'],symbol=symbol,quality_segment=result['quality_segment'],close=result['feature_close'],volume=volume,
            listing_date=listing,is_st=risk,eligible=bool(not risk and not suspended and result['feature_close']>0),trading_symbol=row['trading_symbol'])
        for source,destination in [('pe_ttm','earnings_yield'),('pb_mrq','book_yield')]:
            value=float(row[source]) if pd.notna(row.get(source)) else float('nan')
            bar[destination]=1/value if np.isfinite(value) and value!=0 else float('nan')
        bars.append(bar)
        if result['quality_break']:breaks.append(dict(symbol=symbol,date=row['date'],reference_fault=result['reference_fault'],previous_date=previous['last_date'],previous_close=previous['previous_close'],preclose=row['preclose'],has_payment_event=event is not None))
    merged=pd.concat([old,pd.DataFrame(bars)],ignore_index=True)
    # Empty issuer seeds have object columns in pandas. Preserve numeric/NaN
    # semantics before rolling calculations and whole-market concatenation.
    for column in ['close','volume','earnings_yield','book_yield']:
        merged[column]=pd.to_numeric(merged[column],errors='raise').astype(float)
    merged['quality_segment']=pd.to_numeric(merged.quality_segment,errors='raise').astype('int64')
    for column in ['is_st','eligible']:merged[column]=merged[column].astype(bool)
    merged['listing_date']=pd.to_datetime(merged.listing_date,format='mixed')
    policy=dict(quality_policy=dict(schema='causal_quality_research_v1'),synthetic=False)
    panel=technical_panel(Dataset(merged,pd.DataFrame(),cal.dates,policy))
    latest=panel[panel.date.eq(signal)].copy()
    return machine.snapshot(),latest,pd.DataFrame(breaks,columns=BREAK_COLUMNS)

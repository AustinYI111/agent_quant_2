"""Prespecified market-state and ONeil-style box research features.

Unnormalized feature rows and next-open decision signals; not orders or returns.
Industry features require a separate dated-membership source, never backfill it.
"""
import numpy as np
import pandas as pd


BOX_FEATURES = ['box_width_20','box_width_60','close_to_prior_box_high20','breakout_volume_ratio50']
MARKET_FEATURES = ['index_above_ma200','index_volatility_63','momentum_63_times_market_on']


def _dates(values):
    dates=pd.DatetimeIndex(pd.to_datetime(values))
    if dates.tz is not None or dates.hasnans or not dates.equals(dates.normalize()):
        raise ValueError('normalized exchange dates required')
    return dates


def market_state_features(benchmark,calendar):
    cal=_dates(calendar)
    if cal.has_duplicates or not cal.is_monotonic_increasing or cal.empty:
        raise ValueError('ordered unique exchange calendar required')
    b=benchmark.copy();b['date']=_dates(b.date)
    if b.date.duplicated().any() or not b.date.isin(cal).all():
        raise ValueError('index quote keys outside calendar or duplicate')
    if 'code' in b and not b.code.eq('sh.000300').all():
        raise ValueError('CSI300 price benchmark required')
    close=pd.to_numeric(b.close,errors='raise')
    if close.isna().any() or not np.isfinite(close).all() or close.le(0).any():
        raise ValueError('invalid index close')
    close=pd.Series(close.to_numpy(),index=pd.DatetimeIndex(b.date)).reindex(cal)
    average=close.rolling(200,min_periods=200).mean()
    known=close.notna()&average.notna()
    on=close.gt(average).astype(float).where(known)
    volatility=close.pct_change(fill_method=None).rolling(63,min_periods=63).std()
    return pd.DataFrame(dict(date=cal,index_above_ma200=on.to_numpy(),index_volatility_63=volatility.to_numpy()))


def technical_candidate_rows(prices,index_states,calendar):
    """Use bounded per-issuer daily history; no label or outcome is accepted."""
    required={'date','symbol','quality_segment','close','volume'}
    if not required.issubset(prices):raise ValueError('causal daily price schema required')
    cal=_dates(calendar)
    if cal.empty or cal.has_duplicates or not cal.is_monotonic_increasing:raise ValueError('invalid candidate calendar')
    p=prices.copy();p['date']=_dates(p.date)
    if p[['date','symbol','quality_segment']].isna().any().any() or p.duplicated(['date','symbol']).any() or not p.date.isin(cal).all():
        raise ValueError('invalid candidate price keys')
    for column in ['close','volume']:
        p[column]=pd.to_numeric(p[column],errors='raise')
        if np.isinf(p[column]).any() or p[column].dropna().lt(0).any() or column=='close' and p.close.dropna().eq(0).any():
            raise ValueError('invalid causal feature price or volume')
    p=p.sort_values(['symbol','date']).reset_index(drop=True)
    groups=p.groupby(['symbol','quality_segment'],sort=False)
    ordinal=pd.Series(cal.get_indexer(p.date),index=p.index)
    previous=ordinal.groupby([p.symbol,p.quality_segment],sort=False).shift()
    if (previous.notna()&ordinal.sub(previous).ne(1)).any():
        raise ValueError('a missing price session must start a new quality segment')
    high20=groups.close.transform(lambda value:value.shift(1).rolling(20,min_periods=20).max())
    low20=groups.close.transform(lambda value:value.shift(1).rolling(20,min_periods=20).min())
    high60=groups.close.transform(lambda value:value.shift(1).rolling(60,min_periods=60).max())
    low60=groups.close.transform(lambda value:value.shift(1).rolling(60,min_periods=60).min())
    volume50=groups.volume.transform(lambda value:value.shift(1).rolling(50,min_periods=50).mean())
    result=p[['date','symbol']].copy()
    result['box_width_20']=high20/low20-1
    result['box_width_60']=high60/low60-1
    result['close_to_prior_box_high20']=p.close/high20
    result['breakout_volume_ratio50']=(p.volume/volume50).where(volume50.gt(0))
    result[BOX_FEATURES]=result[BOX_FEATURES].replace([np.inf,-np.inf],np.nan)
    states=index_states[['date','index_above_ma200','index_volatility_63']].copy()
    states['date']=_dates(states.date)
    if states.date.duplicated().any() or not states.date.isin(cal).all():raise ValueError('invalid index-state keys')
    result=result.merge(states,on='date',how='left',validate='many_to_one')
    if not result.index_above_ma200.dropna().isin([0.,1.]).all():raise ValueError('invalid market-state indicator')
    result['momentum_63_times_market_on']=groups.close.pct_change(63,fill_method=None)*result.index_above_ma200
    # Context for the separately gated dated-membership industry group. These
    # are not automatically added to any model's declared feature order.
    result['raw_momentum_63']=groups.close.pct_change(63,fill_method=None)
    result['raw_momentum_126']=groups.close.pct_change(126,fill_method=None)
    close50=groups.close.transform(lambda value:value.rolling(50,min_periods=50).mean())
    result['close_above_ma50']=p.close.gt(close50).astype(float).where(p.close.notna()&close50.notna())
    # Execution callers must schedule these decisions for the next tradable open.
    result['market_entry']=result.index_above_ma200.eq(1)
    result['market_exit']=result.index_above_ma200.eq(0).astype('boolean').where(result.index_above_ma200.notna(),pd.NA)
    known=p.close.notna()&high20.notna()&low20.notna()&result.breakout_volume_ratio50.notna()
    result['box_signal_known']=known
    result['box_entry']=known&p.close.gt(high20)&result.box_width_20.le(.25)&result.breakout_volume_ratio50.ge(1.5)
    result['box_exit']=p.close.lt(low20).astype('boolean').where(p.close.notna()&low20.notna(),pd.NA)
    result['combined_entry']=result.market_entry&result.box_entry
    result['combined_exit']=result.market_exit|result.box_exit
    return result


def normalize_candidate_snapshot(frame):
    """Same-date winsor/z-score stock features; keep shared market state intact."""
    required=BOX_FEATURES+MARKET_FEATURES
    if not set(required).issubset(frame):raise ValueError('complete technical candidate features required')
    if 'date' not in frame or frame.empty or frame.date.nunique(dropna=False)!=1:
        raise ValueError('normalization requires one signal date, never mixed history')
    _dates(frame.date)
    if 'symbol' in frame and frame.symbol.duplicated().any():raise ValueError('duplicate candidate issuer')
    for name in ['index_above_ma200','index_volatility_63']:
        if frame[name].nunique(dropna=False)>1:raise ValueError('market state must be shared within a signal date')
    result=frame[required].replace([np.inf,-np.inf],np.nan).copy()
    for name in BOX_FEATURES+['momentum_63_times_market_on']:
        raw=result[name];lo,hi=raw.quantile([.01,.99]);raw=raw.clip(lo,hi);std=raw.std(ddof=0)
        result[name]=(raw-raw.mean())/std if std>0 else raw*0
    return result

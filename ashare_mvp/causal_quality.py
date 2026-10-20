"""Causal indicator segments; never a substitute for portfolio wealth.

All source rows remain. A fault observed today resets today's indicator
history, without changing an earlier feature or inventing a terminal value.
"""
from pathlib import Path
import hashlib,json
import numpy as np
import pandas as pd
from .price_action_audit import audit_price_actions
from .corporate_actions import economic_return_prices


QUALITY_SCHEMA='causal_quality_research_v1'


class HeldValuationError(ValueError):
    """An actual positive holding cannot be valued; retain valid prefix evidence."""
    def __init__(self,reason,symbol,date,nav,trades,holdings):
        self.reason=reason;self.symbol=symbol;self.date=pd.Timestamp(date)
        self.nav=pd.DataFrame(nav);self.trades=pd.DataFrame(trades)
        self.holdings={key:float(value) for key,value in holdings.items()}
        label={'missing':'unresolved held valuation','reference':'unreconciled held price/action','terminal':'unresolved held terminal settlement'}[reason]
        super().__init__(f"{label}: {symbol} {self.date.date()}")


class IncompleteHorizonBacktests(ValueError):
    def __init__(self,errors):
        self.errors=errors
        super().__init__('Incomplete actual holding valuations: '+', '.join(f"{r['horizon']}:{r['symbol']}@{r['date']}" for r in errors))


def quality_inputs(prices,actions,calendar,reference_tick_size=None):
    """Validate scalar evidence and recompute observable price/gap segments."""
    required=['date','symbol','open','close','preclose','tradestatus']
    if not set(required).issubset(prices) or not {'symbol','ex_date','available_date','pay_date','cash_per_share','share_multiplier'}.issubset(actions):
        raise ValueError('quality research raw price/event schema incomplete')
    p=prices[required].copy();a=actions.copy();cal=pd.DatetimeIndex(calendar)
    if cal.empty or cal.hasnans or cal.has_duplicates or cal.tz is not None or not cal.is_monotonic_increasing or not cal.equals(cal.normalize()):raise ValueError('ordered quality research calendar required')
    p['date']=pd.to_datetime(p.date)
    if p.empty or p[['date','symbol']].isna().any().any() or p.duplicated(['date','symbol']).any() or not p.date.isin(cal).all() or p.date.dt.tz is not None or not p.date.eq(p.date.dt.normalize()).all():raise ValueError('unique normalized quality quote keys required')
    p['tradestatus']=p.tradestatus.astype(str)
    if not p.tradestatus.isin(['0','1']).all():raise ValueError('explicit quality trading states required')
    for name in ['open','close','preclose']:
        p[name]=pd.to_numeric(p[name],errors='raise')
        if not np.isfinite(p[name]).all() or (p[name]<0).any() or (name!='open' and p[name].eq(0).any()):raise ValueError('invalid raw quality valuation')
    for name in ['ex_date','available_date','pay_date']:
        a[name]=pd.to_datetime(a[name]);known=a[name].dropna()
        if known.dt.tz is not None or not known.eq(known.dt.normalize()).all():raise ValueError('normalized quality event clocks required')
    if a[['symbol','ex_date','available_date']].isna().any().any() or a.duplicated(['symbol','ex_date']).any():raise ValueError('unique quality event keys required')
    for name in ['cash_per_share','share_multiplier']:
        a[name]=pd.to_numeric(a[name],errors='raise')
        if not np.isfinite(a[name]).all() or a[name].lt(0).any():raise ValueError('invalid quality event amounts')
    if a.share_multiplier.le(0).any() or (a.cash_per_share.gt(0)&a.pay_date.isna()).any() or a.available_date.gt(a.ex_date).any() or a.pay_date.lt(a.ex_date).any():raise ValueError('quality event not visible or invalid payment clock')
    inside=a.ex_date.between(cal.min(),cal.max())
    if not a.loc[inside,'ex_date'].isin(cal).all():raise ValueError('quality event outside exchange sessions')
    if 'rights_cash_required' in a and a.rights_cash_required.fillna(0).ne(0).any():raise ValueError('unsupported external rights capital')
    issues,summary=audit_price_actions(p,a,cal,reference_tick_size)
    bad=issues[issues.issue.eq('unreconciled_adjustment')][['symbol','date']].copy();bad['reference_fault']=True
    p=p.merge(bad,on=['symbol','date'],how='left',validate='one_to_one').sort_values(['symbol','date']).reset_index(drop=True)
    p['reference_fault']=p.reference_fault.fillna(False).astype(bool)
    ordinal=pd.Series(cal.get_indexer(p.date),index=p.index)
    first=p.symbol.ne(p.symbol.shift())
    gap=~first&ordinal.sub(ordinal.shift()).ne(1)
    p['quality_break']=p.reference_fault|gap
    p['quality_segment']=(first|p.quality_break).groupby(p.symbol,sort=False).cumsum().astype('int64')
    return p,a,dict(reference_faults=len(bad),observed_gap_starts=int(gap.sum()),
        segments=int((first|p.quality_break).sum()),rows=len(p),symbols=int(p.symbol.nunique()),audit=summary)


def causal_feature_index(prices,actions,calendar,reference_tick_size=None,progress=None):
    p,a,summary=quality_inputs(prices,actions,calendar,reference_tick_size)
    cal=pd.DatetimeIndex(calendar);event_groups=dict(tuple(a.groupby('symbol',sort=False)))
    close=np.full(len(p),np.nan);op=np.full(len(p),np.nan)
    for count,((symbol,segment),bars) in enumerate(p.groupby(['symbol','quality_segment'],sort=False),1):
        executable=bars.tradestatus.eq('1')&bars.open.gt(0)
        if executable.any():
            first_index=bars.index[executable][0];rows=bars.loc[first_index:]
            first,last=rows.date.iloc[0],rows.date.iloc[-1]
            events=event_groups.get(symbol,a.iloc[:0]);events=events[events.ex_date.gt(first)&events.ex_date.le(last)]
            economic=economic_return_prices(rows,events,cal[(cal>=first)&(cal<=last)],reference_tick_size)
            if not np.array_equal(economic.date.to_numpy(),rows.date.to_numpy()):raise ValueError('quality feature alignment changed')
            close[rows.index]=economic.close.to_numpy();op[rows.index]=economic.total_return_open.to_numpy()
        if progress is not None and count%250==0:progress(count,summary['segments'])
    result=p[['date','symbol','quality_segment','quality_break','reference_fault']].copy()
    result['feature_close']=close;result['feature_total_return_open']=op
    return result,summary


def seal_causal_quality_data(data,actions,out,reference_tick_size=None,parent_metadata_sha256=None,progress=None):
    target=Path(out)
    if target.exists() and any(target.iterdir()):raise ValueError('causal quality output must be empty')
    annotations,summary=causal_feature_index(data.prices,actions,data.calendar,reference_tick_size,progress)
    prices=data.prices.merge(annotations,on=['date','symbol'],how='left',validate='one_to_one')
    if len(prices)!=len(data.prices):raise ValueError('causal export changed historical universe')
    raw_columns=['date','symbol','open','close','preclose','tradestatus','quality_segment','quality_break','reference_fault']
    if 'terminal_date' in prices:raw_columns.append('terminal_date')
    raw=prices[raw_columns].copy()
    prices['close']=prices.pop('feature_close');prices['total_return_open']=prices.pop('feature_total_return_open')
    prices['eligible'] &= prices.close.notna()&prices.total_return_open.notna()
    metadata=dict(data.metadata,price_basis='causal_segment_feature_index',certified=False,model_ready=False,
        parent_metadata_sha256=parent_metadata_sha256,
        quality_policy=dict(schema=QUALITY_SCHEMA,summary=summary,feature_only_segments=True,
            unknown_targets='retain_prediction_candidates',portfolio='strict_held_raw_paths',
            limitation='Unknown training outcomes may cause availability bias; actual held missing values still fail'),
        event_return_inputs=dict(raw_prices='event_raw_prices.csv',corporate_actions='event_corporate_actions.csv',reference_tick_size=reference_tick_size))
    target.mkdir(parents=True,exist_ok=True)
    for name,frame in [('prices.csv',prices),('fundamentals.csv',data.fundamentals),('calendar.csv',pd.DataFrame({'date':data.calendar})),
                       ('event_raw_prices.csv',raw),('event_corporate_actions.csv',actions)]:frame.to_csv(target/name,index=False)
    metadata['converted_hashes']={name:hashlib.sha256((target/name).read_bytes()).hexdigest() for name in
        ['prices.csv','fundamentals.csv','calendar.csv','event_raw_prices.csv','event_corporate_actions.csv']}
    (target/'metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
    return metadata

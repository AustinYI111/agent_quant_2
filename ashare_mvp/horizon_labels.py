"""Explicit 5/20-session and 12-calendar-month next-open research labels."""
from dataclasses import dataclass
import numpy as np
import pandas as pd
from .corporate_actions import entry_return_path


@dataclass(frozen=True)
class LabelHorizon:
    key: str
    unit: str
    length: int


HORIZONS = {
    '5d': LabelHorizon('5d', 'trading_sessions', 5),
    '20d': LabelHorizon('20d', 'trading_sessions', 20),
    '12m': LabelHorizon('12m', 'calendar_months', 12),
}


def resolve_horizons(keys=('5d','20d','12m')):
    if not keys or len(set(keys))!=len(keys) or any(key not in HORIZONS for key in keys):
        raise ValueError('distinct explicit horizon keys required: 5d, 20d, 12m')
    return tuple(HORIZONS[key] for key in keys)


def label_interval(calendar, signal, horizon):
    """Enter the next session open; count full holding intervals from entry."""
    cal=pd.DatetimeIndex(calendar);signal=pd.Timestamp(signal)
    if signal not in cal or horizon.key not in HORIZONS or horizon!=HORIZONS[horizon.key]:
        raise ValueError('known signal session and explicit horizon required')
    position=cal.searchsorted(signal,side='right')
    if position>=len(cal):return pd.NaT,pd.NaT
    entry=cal[position]
    end_position=(position+horizon.length if horizon.unit=='trading_sessions'
        else cal.searchsorted(entry+pd.DateOffset(months=horizon.length)))
    return entry,cal[end_position] if end_position<len(cal) else pd.NaT


def forward_horizon_labels(prices, actions, calendar, signal_dates,
                           horizon_keys=('5d','20d','12m'), reference_tick_size=None, asof=None):
    """Exact new-buyer outcomes; no pre-entry dividend claims or shortened labels.

    Within an interval containing no ex-date, every fresh buyer acquires the
    same future entitlements per initial share. Build one ledger per such entry
    bucket and divide its future per-share wealth by each buyer's own open.
    This shares computation without dividing a seasoned entitlement index.
    All price/event validation remains in the existing ledger.
    """
    horizons=resolve_horizons(horizon_keys)
    cal=pd.DatetimeIndex(calendar);signals=pd.DatetimeIndex(signal_dates)
    if cal.empty or cal.hasnans or cal.has_duplicates or not cal.is_monotonic_increasing or cal.tz is not None or not cal.equals(cal.normalize()):
        raise ValueError('ordered normalized unique horizon calendar required')
    required={'date','symbol','open','close','preclose','tradestatus'}
    if not required.issubset(prices) or not {'symbol','ex_date','available_date','pay_date','cash_per_share','share_multiplier'}.issubset(actions):
        raise ValueError('raw horizon price/action schema incomplete')
    p=prices.copy();a=actions.copy();p['date']=pd.to_datetime(p.date);a['ex_date']=pd.to_datetime(a.ex_date)
    if p.empty or p[['symbol','date']].isna().any().any() or p.duplicated(['symbol','date']).any():
        raise ValueError('unique nonempty horizon quote keys required')
    if not p.tradestatus.astype(str).isin(['0','1']).all():raise ValueError('explicit horizon trading states required')
    if p.date.dt.tz is not None or not p.date.eq(p.date.dt.normalize()).all() or not p.date.isin(cal).all():
        raise ValueError('normalized horizon quotes must use supplied calendar')
    if a[['symbol','ex_date']].isna().any().any() or a.duplicated(['symbol','ex_date']).any() or a.ex_date.dt.tz is not None or not a.ex_date.eq(a.ex_date.dt.normalize()).all():
        raise ValueError('unique normalized horizon event keys required')
    p['open']=pd.to_numeric(p.open,errors='raise')
    if not np.isfinite(p.open).all() or p.open.lt(0).any():raise ValueError('invalid horizon open')
    observed=p.date.max();cutoff=observed if asof is None else pd.Timestamp(asof)
    if cutoff not in cal or cutoff>observed or cutoff not in set(p.date):raise ValueError('horizon asof must be an observed session')
    cal=cal[cal<=cutoff];p=p[p.date<=cutoff]
    if signals.empty or signals.hasnans or signals.has_duplicates or signals.tz is not None or not signals.equals(signals.normalize()) or not signals.isin(cal).all():
        raise ValueError('unique observed horizon signal sessions required')
    signals=signals.sort_values()
    intervals={date:{h.key:label_interval(cal,date,h) for h in horizons} for date in signals}
    event_groups=dict(tuple(a.groupby('symbol',sort=False)))
    records=[]
    for symbol,bars in p.groupby('symbol',sort=False):
        bars=bars.sort_values('date');lookup=bars.set_index('date')
        events=event_groups.get(symbol,a.iloc[:0]);event_dates=np.sort(events.ex_date.to_numpy(dtype='datetime64[ns]'))
        buckets={};requests=[]
        for signal in signals:
            for h in horizons:
                entry,end=intervals[signal][h.key]
                status='unmatured' if pd.isna(end) else 'valued'
                if pd.isna(entry):status='no_next_session'
                executable=(pd.notna(entry) and entry in lookup.index and str(lookup.at[entry,'tradestatus'])=='1' and lookup.at[entry,'open']>0)
                if pd.notna(end) and not executable:status='blocked_entry'
                record=dict(date=signal,symbol=symbol,horizon=h.key,horizon_unit=h.unit,horizon_length=h.length,
                    entry_date=entry,label_end=end,target=np.nan,valuation_return=np.nan,executable_exit=False,
                    status=status,return_basis='gross_new_entry_entitlements')
                index=len(requests);requests.append(record)
                if pd.notna(end) and executable:
                    bucket=int(np.searchsorted(event_dates,entry.to_datetime64(),side='right'))
                    buckets.setdefault(bucket,[]).append(index)
        for indices in buckets.values():
            first=min(requests[i]['entry_date'] for i in indices)
            last=max(requests[i]['label_end'] for i in indices)
            path=entry_return_path(bars,events,cal,symbol,first,last,reference_tick_size).set_index('date')
            for index in indices:
                record=requests[index];outcome=path.loc[record['label_end']]
                value=float(outcome.total_return_open/lookup.at[record['entry_date'],'open']-1)
                executable_exit=bool(outcome.executable_exit)
                record.update(valuation_return=value,executable_exit=executable_exit,
                    target=value if executable_exit else np.nan,status='valued' if executable_exit else 'blocked_exit')
        records.extend(requests)
    columns=['date','symbol','horizon','horizon_unit','horizon_length','entry_date','label_end','target',
             'valuation_return','executable_exit','status','return_basis']
    result=pd.DataFrame(records,columns=columns)
    return result.sort_values(['date','symbol','horizon']).reset_index(drop=True)


def write_horizon_label_files(prices_path, actions_path, calendar_path, signals_path, out,
                              horizon_keys=('5d','20d','12m'), reference_tick_size=None, asof=None):
    """Seal label outputs and input hashes; never certify a supplied data source."""
    from pathlib import Path
    from dataclasses import asdict
    import hashlib,json
    target=Path(out)
    if target.exists() and any(target.iterdir()):raise ValueError('horizon label output must be empty')
    inputs={'prices':Path(prices_path),'actions':Path(actions_path),'calendar':Path(calendar_path),'signals':Path(signals_path)}
    def digest(path):
        h=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(8*1024*1024),b''):h.update(block)
        return h.hexdigest()
    hashes={key:digest(path) for key,path in inputs.items()}
    prices=pd.read_csv(inputs['prices'],usecols=['date','symbol','open','close','preclose','tradestatus'],dtype={'symbol':str},low_memory=False)
    actions=pd.read_csv(inputs['actions'],dtype={'symbol':str},low_memory=False)
    c=pd.read_csv(inputs['calendar'],dtype=str)
    cal=pd.DatetimeIndex(pd.to_datetime(c.loc[c.is_trading_day.eq('1'),'calendar_date']
        if {'calendar_date','is_trading_day'}.issubset(c) else c['date']))
    signals=pd.DatetimeIndex(pd.to_datetime(pd.read_csv(inputs['signals'])['date']))
    labels=forward_horizon_labels(prices,actions,cal,signals,horizon_keys,reference_tick_size,asof)
    if any(digest(path)!=hashes[key] for key,path in inputs.items()):raise ValueError('horizon input changed during processing')
    target.mkdir(parents=True,exist_ok=True)
    labels.to_csv(target/'labels.csv',index=False)
    result=dict(schema='multi_horizon_labels_v1',source_data_kind='supplied_unverified',certified=False,model_ready=False,
        input_sha256=hashes,labels_sha256=digest(target/'labels.csv'),rows=len(labels),
        horizons=[asdict(h) for h in resolve_horizons(horizon_keys)],
        asof=str(pd.Timestamp(asof).date()) if asof is not None else str(pd.to_datetime(prices.date).max().date()),
        reference_tick_size=reference_tick_size,
        status_counts={key:group.status.value_counts().to_dict() for key,group in labels.groupby('horizon')},
        label_policy='next-session open entry; 5d/20d full trading intervals; 12m calendar anniversary or next session; unmatured never shortened',
        return_basis='gross_new_entry_entitlements; non-executable exit is not a training return',
        limitations=['labels are future outcomes, never features','source vintages and full-market coverage not certified',
            'label generation is not a model, portfolio backtest or evidence of profitability'])
    (target/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
    return result

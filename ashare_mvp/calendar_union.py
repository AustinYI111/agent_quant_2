"""Seal contiguous exchange calendars without asserting price-download completion."""
from pathlib import Path
from io import BytesIO
import hashlib
import json
import pandas as pd


def seal_calendar_union(directories, out):
    target=Path(out)
    if target.exists() and any(target.iterdir()):
        raise ValueError('calendar union output must be empty')
    if not directories:
        raise ValueError('calendar sources required')
    frames=[];sealed={};origins=[]
    for i,directory in enumerate(directories):
        p=Path(directory)
        manifest=(p/'manifest.json').read_bytes();meta=json.loads(manifest)
        if meta.get('schema')!='market_daily_raw_v1':
            raise ValueError('market calendar source schema mismatch')
        raw=(p/'calendar.csv').read_bytes()
        digest=hashlib.sha256(raw).hexdigest()
        if digest!=meta.get('files',{}).get('calendar.csv',{}).get('sha256'):
            raise ValueError('calendar source hash mismatch')
        f=pd.read_csv(BytesIO(raw),dtype=str)
        if not {'calendar_date','is_trading_day'}.issubset(f) or f.empty:
            raise ValueError('calendar schema empty or incomplete')
        dates=pd.DatetimeIndex(pd.to_datetime(f.calendar_date))
        start,end=pd.Timestamp(meta['start']),pd.Timestamp(meta['end'])
        if dates.tz is not None or dates.hasnans or start.tz is not None or end.tz is not None or start!=start.normalize() or end!=end.normalize() or not dates.equals(pd.date_range(start,end,freq='D')):
            raise ValueError('calendar must cover every requested day in order')
        if not f.is_trading_day.isin(['0','1']).all():
            raise ValueError('invalid exchange trading flag')
        f=f[['calendar_date','is_trading_day']].copy()
        f['calendar_date']=dates.strftime('%Y-%m-%d')
        frames.append(f)
        sealed[f'input_{i}_manifest.json']=manifest
        sealed[f'input_{i}_calendar.csv']=raw
        origins.append(dict(manifest_sha256=hashlib.sha256(manifest).hexdigest(),calendar_sha256=digest,
            start=meta['start'],end=meta['end'],price_acquisition_status=meta.get('status'),synthetic=bool(meta.get('synthetic',False))))
    combined=pd.concat(frames,ignore_index=True)
    if combined.groupby('calendar_date').is_trading_day.nunique().gt(1).any():
        raise ValueError('conflicting exchange flags on overlapping dates')
    combined=combined.drop_duplicates('calendar_date').sort_values('calendar_date').reset_index(drop=True)
    dates=pd.DatetimeIndex(pd.to_datetime(combined.calendar_date))
    if not dates.equals(pd.date_range(dates[0],dates[-1],freq='D')):
        raise ValueError('gap between supplied calendar ranges')
    sessions=combined.loc[combined.is_trading_day.eq('1'),'calendar_date']
    if sessions.empty:
        raise ValueError('calendar union has no trading sessions')
    target.mkdir(parents=True,exist_ok=True)
    for name,raw in sealed.items():
        (target/name).write_bytes(raw)
    combined.to_csv(target/'calendar.csv',index=False)
    summary=dict(schema='exchange_calendar_union_v1',start=combined.calendar_date.iloc[0],end=combined.calendar_date.iloc[-1],
        calendar_days=len(combined),sessions=len(sessions),sources=origins,synthetic=any(x['synthetic'] for x in origins),
        calendar_sha256=hashlib.sha256((target/'calendar.csv').read_bytes()).hexdigest(),
        sealed_sha256={name:hashlib.sha256(raw).hexdigest() for name,raw in sealed.items()},
        certified=False,model_ready=False,scope='calendar only; trading-day flags do not prove observed prices, event coverage or financial vintages')
    (target/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    return summary

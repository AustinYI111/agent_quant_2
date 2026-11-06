"""Extend a sealed CSI300 price benchmark without revising its historical rows."""
from pathlib import Path
from io import BytesIO
import hashlib
import json
import numpy as np
import pandas as pd
from .phase2_protocol import sha256


def extend_price_benchmark(previous, recent_csv, recent_sha256, calendar_path, out):
    previous,target=Path(previous),Path(out)
    if target.exists():raise ValueError('new benchmark output required')
    meta=json.loads((previous/'summary.json').read_text(encoding='utf8'))
    if (meta.get('schema')!='benchmark_raw_v1' or meta.get('return_kind')!='price_index'
            or meta.get('request',{}).get('symbol')!='sh.000300'):
        raise ValueError('sealed CSI300 price benchmark required')
    old_raw=(previous/'raw_prices.csv').read_bytes();new_raw=Path(recent_csv).read_bytes()
    if hashlib.sha256(old_raw).hexdigest()!=meta.get('raw_sha256') or hashlib.sha256(new_raw).hexdigest()!=recent_sha256:
        raise ValueError('benchmark source hash differs')
    frames=[pd.read_csv(BytesIO(raw),parse_dates=['date']) for raw in [old_raw,new_raw]]
    for frame in frames:
        if (frame.empty or not {'date','code','open','high','low','close'}.issubset(frame)
                or frame.date.isna().any() or frame.date.dt.tz is not None
                or not frame.date.eq(frame.date.dt.normalize()).all() or frame.date.duplicated().any()
                or not frame.code.eq('sh.000300').all()):
            raise ValueError('invalid CSI300 quote identity/dates')
        for name in ['open','high','low','close']:
            frame[name]=pd.to_numeric(frame[name],errors='raise')
            if not np.isfinite(frame[name]).all() or frame[name].le(0).any():
                raise ValueError('invalid CSI300 quote prices')
    old,new=frames
    common=old.date[old.date.isin(new.date)]
    if common.empty:raise ValueError('overlapping index quotes required to verify continuity')
    a=old.set_index('date').loc[common,['open','high','low','close']]
    b=new.set_index('date').loc[common,['open','high','low','close']]
    if not np.allclose(a.to_numpy(),b.to_numpy(),rtol=0,atol=1e-8):
        raise ValueError('overlapping index prices differ; never overwrite sealed history')
    appended=new[new.date.gt(old.date.max())]
    if appended.empty:raise ValueError('no new benchmark sessions supplied')
    merged=pd.concat([old,appended],ignore_index=True).sort_values('date')
    cal=pd.DatetimeIndex(pd.to_datetime(pd.read_csv(calendar_path).date))
    if (cal.empty or cal.tz is not None or cal.hasnans or cal.has_duplicates
            or not cal.is_monotonic_increasing or not cal.equals(cal.normalize())
            or not pd.DatetimeIndex(merged.date).equals(cal)):
        raise ValueError('benchmark does not cover the complete supplied calendar')
    target.mkdir(parents=True)
    merged.to_csv(target/'raw_prices.csv',index=False)
    proof=dict(schema='benchmark_raw_v1',source='Sealed BaoStock CSI300 price benchmark extension',
        request=dict(symbol='sh.000300',start=str(cal[0].date()),end=str(cal[-1].date()),frequency='d',adjustflag='3'),
        return_kind='price_index',rows=len(merged),overlap_rows=len(common),new_rows=len(appended),
        raw_sha256=sha256(target/'raw_prices.csv'),previous_raw_sha256=meta['raw_sha256'],
        recent_raw_sha256=recent_sha256,calendar_sha256=sha256(calendar_path),certified=False,model_ready=False,
        limitation='Price index excludes dividends; dividend-inclusive strategy differences are not pure stock-selection alpha.')
    (target/'summary.json').write_text(json.dumps(proof,indent=2),encoding='utf8')
    return proof

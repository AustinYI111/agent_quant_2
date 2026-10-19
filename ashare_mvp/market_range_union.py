"""Immutable union of adjacent raw daily-price acquisitions, preserving missing days."""
from pathlib import Path
from io import BytesIO
import hashlib,json,re
import pandas as pd
from .calendar_union import seal_calendar_union


def merge_market_ranges(directories,out):
    target=Path(out);paths=[Path(d) for d in directories]
    if target.exists() and any(target.iterdir()):raise ValueError('market range output must be empty')
    if not paths:raise ValueError('market range sources required')
    for p in paths:
        if target.resolve()==p.resolve() or target.resolve() in p.resolve().parents or p.resolve() in target.resolve().parents:
            raise ValueError('market range output must be separate from sources')
    # Freeze source manifests exactly once, even if acquisition is still running.
    lineage=target/'calendar_lineage';calendar=seal_calendar_union(paths,lineage)
    snapshots=[(lineage/f'input_{i}_manifest.json').read_bytes() for i in range(len(paths))]
    metas=[json.loads(raw) for raw in snapshots]
    if any(not isinstance(m.get('synthetic'),bool) for m in metas) or len({m['synthetic'] for m in metas})!=1:
        raise ValueError('explicit consistent synthetic declarations required')
    if len({m.get('source') for m in metas})!=1 or not metas[0].get('source'):
        raise ValueError('same raw provider contract required for range union')
    raw_calendar=(lineage/'calendar.csv').read_bytes()
    f=pd.read_csv(BytesIO(raw_calendar),dtype=str)
    sessions=pd.DatetimeIndex(pd.to_datetime(f.loc[f.is_trading_day.eq('1'),'calendar_date']))
    files={'calendar.csv':dict(rows=len(f),columns=list(f.columns),sha256=hashlib.sha256(raw_calendar).hexdigest())}
    queries={};origins={};rows=0
    for i,(path,meta) in enumerate(zip(paths,metas)):
        for name,info in sorted(meta['files'].items()):
            if name=='calendar.csv':continue
            match=re.fullmatch(r'daily_(\d{8})\.csv',name)
            if not match:raise ValueError('invalid raw daily filename')
            day=pd.to_datetime(match[1],format='%Y%m%d');key=str(day.date())
            if day not in sessions or not pd.Timestamp(meta['start'])<=day<=pd.Timestamp(meta['end']):
                raise ValueError('daily cache outside source request/session')
            if meta.get('queries',{}).get(key,{}).get('status')!='ok':
                raise ValueError('daily cache has no successful source query')
            raw=(path/name).read_bytes();digest=hashlib.sha256(raw).hexdigest()
            if digest!=info.get('sha256'):raise ValueError('market range daily hash mismatch')
            price=pd.read_csv(BytesIO(raw),dtype=str)
            required={'date','code','open','close','preclose','volume','adjustflag','tradestatus','isST','peTTM','pbMRQ'}
            if not required.issubset(price) or price.empty or len(price)!=info.get('rows'):
                raise ValueError('market range daily schema/count mismatch')
            if price.code.isna().any() or price.code.duplicated().any() or not price.date.eq(key).all():
                raise ValueError('market range daily date/security keys mismatch')
            if not price.adjustflag.eq('3').all() or not price.tradestatus.isin(['0','1']).all() or not price.isST.isin(['0','1']).all():
                raise ValueError('market range raw basis or historical state mismatch')
            if name in files:
                if files[name]['sha256']!=digest:raise ValueError('conflicting overlapping raw daily observations')
                origins[name].append(i)
            else:
                (target/name).write_bytes(raw)
                files[name]=dict(info,columns=list(price.columns));origins[name]=[i];rows+=len(price)
                queries[key]=dict(status='ok',rows=len(price))
    missing=[str(day.date()) for day in sessions if str(day.date()) not in queries]
    prefix=0
    for day in sessions:
        if str(day.date()) not in queries:break
        prefix+=1
    (target/'calendar.csv').write_bytes(raw_calendar)
    sealed={f'input_{i}_manifest.json':raw for i,raw in enumerate(snapshots)}
    for name,raw in sealed.items():(target/name).write_bytes(raw)
    pd.DataFrame({'date':missing}).to_csv(target/'missing_sessions.csv',index=False)
    manifest=dict(schema='market_daily_raw_v1',source=metas[0]['source'],synthetic=metas[0]['synthetic'],
        start=calendar['start'],end=calendar['end'],status='partial' if missing else 'downloaded_raw',
        read_only_snapshot=True,sessions_requested=len(sessions),sessions_saved=len(queries),rows=rows,
        files=files,queries=queries,file_origins=origins,certified=False,
        input_sha256={name:hashlib.sha256(raw).hexdigest() for name,raw in sealed.items()},
        calendar_lineage_sha256=hashlib.sha256((lineage/'summary.json').read_bytes()).hexdigest())
    summary=dict(schema='market_range_union_v1',status=manifest['status'],sessions_requested=len(sessions),
        sessions_saved=len(queries),missing_sessions=len(missing),continuous_prefix_sessions=prefix,rows=rows,
        overlapping_equal_sessions=sum(len(x)>1 for x in origins.values()),source_statuses=[m.get('status') for m in metas],
        certified=False,model_ready=False,limitation='raw session coverage only; security lifetime, terminal prices, corporate actions and financial vintages still require validation')
    (target/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    # Publish the usable manifest last. Interrupted/invalid exports have no manifest.
    (target/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    return summary

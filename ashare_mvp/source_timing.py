"""Require actual collection after the frozen close-data release cutoff."""
from pathlib import Path
from datetime import datetime,timezone
import json
import pandas as pd
from .phase2_protocol import utc,day,sha256


def audit_market_observations(markets,out):
    target=Path(out)
    if target.exists():raise ValueError('new source observation audit required')
    records=[];seen=set()
    for directory in markets:
        directory=Path(directory)
        manifest=json.loads((directory/'manifest.json').read_text(encoding='utf8'))
        if manifest.get('schema')!='market_daily_raw_v1' or manifest.get('status')!='downloaded_raw':
            raise ValueError('complete daily market capture required')
        parent=sha256(directory/'manifest.json')
        for name,info in manifest['files'].items():
            if not name.startswith('daily_'):continue
            if Path(name).name!=name or sha256(directory/name)!=info['sha256']:
                raise ValueError('market source evidence changed')
            date=day(pd.to_datetime(name.removeprefix('daily_').removesuffix('.csv'),format='%Y%m%d'))
            if date in seen:raise ValueError('duplicate observed market session')
            observed=utc(info['retrieved_at'])
            cutoff=(date+pd.Timedelta(hours=17,minutes=20)).tz_localize('Asia/Shanghai').tz_convert('UTC')
            if observed<cutoff:
                raise ValueError('quote collected before the close release cutoff; recollect, never relabel as final')
            seen.add(date)
            records.append(dict(date=str(date.date()),observed_at_utc=observed.isoformat(),
                                raw_sha256=info['sha256'],source_manifest_sha256=parent))
    if not records:raise ValueError('no observed market sessions')
    records.sort(key=lambda row:row['date'])
    proof=dict(schema='closed_market_observations_v1',created_at_utc=datetime.now(timezone.utc).isoformat(),
               sessions=len(records),records=records,latest_input_observed_at_utc=max(utc(row['observed_at_utc']) for row in records).isoformat(),
               certified=False,limitation='Post-cutoff observation only; not original supplier vintage or execution certification.')
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x',encoding='utf8') as stream:json.dump(proof,stream,indent=2)
    return proof

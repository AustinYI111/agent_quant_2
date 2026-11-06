"""Combine complete, nonoverlapping quarterly raw plan captures without edits."""
from pathlib import Path
import json
import shutil
import pandas as pd
from .phase2_protocol import day,sha256


def merge_dividend_plan_captures(sources,out):
    target=Path(out)
    if target.exists():raise ValueError('new raw plan union output required')
    if not sources:raise ValueError('complete raw dividend sources required')
    periods={};parents=[];files=[];kind=None
    for directory in sources:
        directory=Path(directory)
        manifest=json.loads((directory/'manifest.json').read_text(encoding='utf8'))
        if manifest.get('schema')!='market_dividend_plans_v1' or manifest.get('status')!='downloaded_raw':
            raise ValueError('complete raw plan capture required')
        if not isinstance(manifest.get('synthetic'),bool):raise ValueError('explicit dividend dataset kind required')
        if kind is not None and kind!=manifest['synthetic']:raise ValueError('different dividend dataset kinds')
        kind=manifest['synthetic']
        parents.append(dict(manifest_sha256=sha256(directory/'manifest.json'),start=manifest['start'],end=manifest['end']))
        for date,info in manifest['periods'].items():
            if date in periods:raise ValueError('overlapping plan versions require explicit reconciliation')
            if info.get('status')!='ok':raise ValueError('unfinished raw plan period')
            period=day(date);folder=f'{period.year}Q{period.quarter}'
            for name,expected in info['hashes'].items():
                file=directory/folder/name
                if Path(name).name!=name or sha256(file)!=expected:raise ValueError('raw plan source hash differs')
                files.append((file,folder,name,expected))
            periods[date]=info
    dates=sorted(periods)
    expected=pd.date_range(dates[0],dates[-1],freq='QE').strftime('%Y-%m-%d').tolist()
    if dates!=expected:raise ValueError('missing raw plan quarter in union')
    target.mkdir(parents=True)
    for source,folder,name,expected_hash in files:
        destination=target/folder/name;destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(source,destination)
        if sha256(destination)!=expected_hash:
            raise ValueError('raw plan changed during copy')
    proof=dict(schema='market_dividend_plans_v1',source='Sealed complete quarterly Eastmoney RPT_SHAREBONUS_DET union',start=dates[0],end=dates[-1],requested_periods=len(dates),periods={date:periods[date] for date in dates},sources=parents,status='downloaded_raw',synthetic=kind,certified=False,model_ready=False)
    (target/'manifest.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf8')
    return proof

"""Union sealed earnings ranges without choosing a preferred conflicting version."""
from pathlib import Path
from io import BytesIO
import hashlib,json
import numpy as np
import pandas as pd


def union_financial_panels(directories, calendar_directory, out):
    target=Path(out)
    if target.exists() and any(target.iterdir()):raise ValueError('financial union output must be empty')
    if not directories:raise ValueError('financial panel sources required')
    calpath=Path(calendar_directory)
    calendar_meta=(calpath/'summary.json').read_bytes();cm=json.loads(calendar_meta)
    calendar_raw=(calpath/'calendar.csv').read_bytes()
    calendar_sha=hashlib.sha256(calendar_raw).hexdigest()
    if cm.get('schema')!='exchange_calendar_union_v1' or calendar_sha!=cm.get('calendar_sha256'):
        raise ValueError('sealed union calendar hash/schema mismatch')
    c=pd.read_csv(BytesIO(calendar_raw),dtype=str)
    dates=pd.DatetimeIndex(pd.to_datetime(c.calendar_date))
    if c.empty or not dates.equals(pd.date_range(dates[0],dates[-1],freq='D')) or not c.is_trading_day.isin(['0','1']).all():
        raise ValueError('complete normalized union calendar required')
    sessions=pd.DatetimeIndex(pd.to_datetime(c.loc[c.is_trading_day.eq('1'),'calendar_date']))
    if sessions.empty:raise ValueError('financial union requires trading sessions')
    sealed={'input_calendar_summary.json':calendar_meta,'calendar.csv':calendar_raw}
    frames=[];sources=[];columns=None
    clocks=['report_date','publication_date','provider_update_date','provider_ingest_date','available_date']
    numeric=['roe','revenue_growth','earnings_growth','debt_ratio','eps','book_value_per_share','operating_cashflow_per_share']
    required={'symbol',*clocks,*numeric,'visibility_status','origin_quarter'}
    for i,directory in enumerate(directories):
        path=Path(directory);sr=(path/'summary.json').read_bytes();meta=json.loads(sr)
        lr=(path/'calendar_lineage.json').read_bytes();lineage=json.loads(lr)
        if lineage.get('calendar_sha256')!=calendar_sha or lineage.get('calendar_summary_sha256')!=hashlib.sha256(calendar_meta).hexdigest():
            raise ValueError('financial source calendar lineage differs')
        raw=(path/'financial_panel.csv').read_bytes();digest=hashlib.sha256(raw).hexdigest()
        if digest!=meta.get('panel_sha256'):raise ValueError('financial source panel hash mismatch')
        f=pd.read_csv(BytesIO(raw),dtype={'symbol':str},low_memory=False)
        if not required.issubset(f) or f.empty or len(f)!=meta.get('records') or any(x.startswith('_union_') for x in f):
            raise ValueError('financial source schema/count mismatch')
        if columns is None:columns=list(f.columns)
        if set(f.columns)!=set(columns):raise ValueError('financial source column contracts differ')
        f=f[columns]
        for col in clocks:
            f[col]=pd.to_datetime(f[col],errors='raise');known=f[col].dropna()
            if known.dt.tz is not None or not known.eq(known.dt.normalize()).all():raise ValueError('normalized financial clocks required')
        for col in numeric:
            f[col]=pd.to_numeric(f[col],errors='raise')
            if np.isinf(f[col]).any():raise ValueError('infinite financial value')
        visible=f.available_date.notna()
        unidentified=f.symbol.isna()
        if (unidentified & ~f.visibility_status.eq('unsupported_security_exchange')).any():raise ValueError('unidentified financial record lacks explicit status')
        if not f.loc[~unidentified,'symbol'].str.fullmatch(r'(?:sh|sz|bj)\.\d{6}').all():raise ValueError('invalid normalized financial identity')
        if f.loc[visible,['report_date','publication_date','provider_update_date']].isna().any().any() or not f.loc[visible,'available_date'].isin(sessions).all():
            raise ValueError('visible financial keys/session invalid')
        for col in ['report_date','publication_date','provider_update_date','provider_ingest_date']:
            if (f.loc[visible,col]>=f.loc[visible,'available_date']).any():raise ValueError('premature financial availability')
        if (f.loc[visible,'publication_date']<=f.loc[visible,'report_date']).any():raise ValueError('publication before report end')
        f['_union_source']=i;f['_union_row']=np.arange(len(f));frames.append(f)
        sealed[f'input_{i}_summary.json']=sr;sealed[f'input_{i}_calendar_lineage.json']=lr
        sources.append(dict(source_index=i,source=meta.get('source'),records=len(f),panel_sha256=digest,
            summary_sha256=hashlib.sha256(sr).hexdigest(),partial_source=bool(meta.get('partial_source',True))))
    merged=pd.concat(frames,ignore_index=True)
    keys=['symbol','report_date','available_date']
    visible=merged.symbol.notna()&merged.available_date.notna()
    duplicate=visible&merged.duplicated(keys,keep=False)
    remove=[];audit=[]
    for key,g in merged.loc[duplicate].groupby(keys,sort=False):
        if len(g[columns].drop_duplicates())!=1:raise ValueError('conflicting financial versions; no source preference permitted')
        remove.extend(g.index[1:].tolist())
        for idx in g.index[1:]:
            audit.append(dict(kept_source=int(g.iloc[0]._union_source),kept_input_row=int(g.iloc[0]._union_row),
                duplicate_source=int(merged.loc[idx,'_union_source']),duplicate_input_row=int(merged.loc[idx,'_union_row']),
                symbol=key[0],report_date=key[1],available_date=key[2]))
    kept=merged.drop(index=remove).reset_index(drop=True)
    origins=kept[['_union_source','_union_row']].rename(columns={'_union_source':'source_index','_union_row':'input_row'})
    origins.insert(0,'output_row',np.arange(len(kept)))
    result=kept[columns]
    target.mkdir(parents=True,exist_ok=True)
    for name,raw in sealed.items():(target/name).write_bytes(raw)
    result.to_csv(target/'financial_panel.csv',index=False)
    origins.to_csv(target/'record_origins.csv',index=False)
    pd.DataFrame(audit,columns=['kept_source','kept_input_row','duplicate_source','duplicate_input_row','symbol','report_date','available_date']).to_csv(target/'equal_version_coalescing.csv',index=False)
    summary=dict(schema='financial_panel_union_v1',source='sealed normalized financial range union',records=len(result),
        recognized_symbols=int(result.symbol.nunique()),visible_records=int((result.symbol.notna()&result.available_date.notna()).sum()),
        invisible_records=int(result.available_date.isna().sum()),unidentified_records=int(result.symbol.isna().sum()),
        not_visible_records=int((result.symbol.isna()|result.available_date.isna()).sum()),equal_duplicate_rows_removed=len(remove),sources=sources,
        calendar_sha256=calendar_sha,calendar_summary_sha256=hashlib.sha256(calendar_meta).hexdigest(),
        partial_source=any(s['partial_source'] for s in sources),certified=False,model_ready=False,
        panel_sha256=hashlib.sha256((target/'financial_panel.csv').read_bytes()).hexdigest(),
        record_origins_sha256=hashlib.sha256((target/'record_origins.csv').read_bytes()).hexdigest(),
        coalescing_sha256=hashlib.sha256((target/'equal_version_coalescing.csv').read_bytes()).hexdigest(),
        sealed_sha256={name:hashlib.sha256(raw).hexdigest() for name,raw in sealed.items()},
        limitation='original vintages unverified; unavailable records retained without backfill; issuer identity and balance dependencies must be joined separately')
    (target/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    (target/'calendar_lineage.json').write_text(json.dumps(dict(calendar_sha256=calendar_sha,calendar_summary_sha256=summary['calendar_summary_sha256']),indent=2),encoding='utf8')
    return summary

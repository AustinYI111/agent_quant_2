"""Disk-backed raw quote bridge for per-issuer causal feature reconstruction.

This is a raw input store, NOT an adjusted-price dataset or portfolio account.
"""
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3
import pandas as pd
import numpy as np

from .phase2_protocol import sha256
from .public_data import is_ashare
from .security_identity import stitch_code_prices


FIELDS = ['date','symbol','open','close','preclose','tradestatus','volume','quality_segment','trading_symbol','source','is_st','pe_ttm','pb_mrq']


def _insert(connection, frame):
    values = frame[FIELDS].copy()
    dates = pd.to_datetime(values.date, errors='raise')
    if dates.isna().any() or dates.dt.tz is not None or not dates.eq(dates.dt.normalize()).all():
        raise ValueError('normalized quote session dates required')
    values['date'] = dates.dt.strftime('%Y-%m-%d')
    if values.symbol.isna().any() or not values.symbol.map(is_ashare).all():
        raise ValueError('raw bridge requires canonical A-share identities')
    values['tradestatus'] = values.tradestatus.astype(str)
    if not values.tradestatus.isin(['0','1']).all():
        raise ValueError('unknown raw trading state')
    for name in ['open','close','preclose']:
        values[name] = pd.to_numeric(values[name], errors='raise')
        if not np.isfinite(values[name]).all() or values[name].lt(0).any():
            raise ValueError('invalid raw quote amount')
    # Preserve zero/unresolved raw values for downstream explicit validation.
    # Never synthesize historical volume or trading aliases absent from source.
    values = values.astype(object).where(pd.notna(values), None)
    with connection:
        connection.executemany('INSERT INTO quotes VALUES ('+','.join('?' for _ in FIELDS)+')', values.itertuples(index=False, name=None))


def build_raw_feature_store(baseline, markets, code_changes, out, chunksize=100000):
    if isinstance(chunksize,bool) or not isinstance(chunksize,int) or chunksize < 1:
        raise ValueError('positive integer chunk size required')
    baseline, target = Path(baseline), Path(out)
    if target.exists():
        raise ValueError('new raw feature-store output required')
    if not markets:
        raise ValueError('completed market captures required')
    meta = json.loads((baseline/'metadata.json').read_text(encoding='utf8'))
    if (meta.get('schema')!='public_ratios_v1' or meta.get('price_basis')!='causal_segment_feature_index'
            or not isinstance(meta.get('synthetic'),bool)):
        raise ValueError('sealed causal baseline required')
    for name in ['calendar.csv','event_raw_prices.csv']:
        if sha256(baseline/name)!=meta.get('converted_hashes',{}).get(name):
            raise ValueError('baseline raw evidence changed')
    old_calendar = pd.DatetimeIndex(pd.to_datetime(pd.read_csv(baseline/'calendar.csv').date))
    if (old_calendar.empty or old_calendar.tz is not None or old_calendar.hasnans
            or old_calendar.has_duplicates or not old_calendar.is_monotonic_increasing
            or not old_calendar.equals(old_calendar.normalize())):
        raise ValueError('invalid baseline session calendar')
    changes = pd.read_json(code_changes,convert_dates=False)
    target.mkdir(parents=True)
    proof = dict(schema='raw_feature_store_v1',status='building',created_at_utc=datetime.now(timezone.utc).isoformat(),
        synthetic=meta['synthetic'],certified=False,prediction_ready=False,
        baseline_metadata_sha256=sha256(baseline/'metadata.json'),
        baseline_raw_sha256=meta['converted_hashes']['event_raw_prices.csv'],
        code_changes_sha256=sha256(code_changes),sources=[],old_rows=0,new_rows=0,excluded_rows=0,
        limitations=['Raw inputs only; causal reconstruction and baseline equivalence must pass before scoring.',
                     'Original financial vintages, terminal values and execution are not certified.',
                     'Missing historical volume and trading aliases remain null, never fabricated.'])
    def checkpoint():
        pending=target/'manifest.pending.json'
        pending.write_text(json.dumps(proof,indent=2),encoding='utf8');pending.replace(target/'manifest.json')
    checkpoint()
    connection=sqlite3.connect(target/'quotes.sqlite')
    connection.execute('PRAGMA cache_size=-65536')
    connection.execute('''CREATE TABLE quotes (
        date TEXT NOT NULL,symbol TEXT NOT NULL,open REAL,close REAL,preclose REAL,
        tradestatus TEXT NOT NULL,volume REAL,quality_segment INTEGER,trading_symbol TEXT,source TEXT NOT NULL,
        is_st INTEGER,pe_ttm REAL,pb_mrq REAL,
        PRIMARY KEY(symbol,date)) WITHOUT ROWID''')
    sessions=set(old_calendar)
    try:
        for chunk in pd.read_csv(baseline/'event_raw_prices.csv',parse_dates=['date'],
                                 usecols=['date','symbol','open','close','preclose','tradestatus','quality_segment'],
                                 dtype={'symbol':str,'tradestatus':str},chunksize=chunksize):
            if not chunk.date.isin(old_calendar).all():
                raise ValueError('baseline quotes outside sealed calendar')
            chunk['volume']=np.nan;chunk['trading_symbol']=None;chunk['source']='baseline'
            for column in ['is_st','pe_ttm','pb_mrq']:chunk[column]=np.nan
            _insert(connection,chunk);proof['old_rows']+=len(chunk)
        print('Raw feature bridge: historical source indexed',flush=True)
        checkpoint()
        exclusions=[];seen_dates=set()
        for market in markets:
            market=Path(market);manifest=json.loads((market/'manifest.json').read_text(encoding='utf8'))
            if (manifest.get('schema')!='market_daily_raw_v1' or manifest.get('status')!='downloaded_raw'
                    or manifest.get('synthetic')!=meta['synthetic']):
                raise ValueError('complete market capture with matching dataset kind required')
            if sha256(market/'calendar.csv')!=manifest['files']['calendar.csv']['sha256']:
                raise ValueError('market calendar changed')
            calendar=pd.read_csv(market/'calendar.csv',dtype=str)
            expected=set(pd.to_datetime(calendar.loc[calendar.is_trading_day.eq('1'),'calendar_date']))
            files=[(name,info) for name,info in manifest['files'].items() if name.startswith('daily_')]
            captured=set()
            for position,(name,info) in enumerate(files,1):
                if Path(name).name!=name or sha256(market/name)!=info['sha256']:
                    raise ValueError('new market raw evidence changed')
                frame=pd.read_csv(market/name,dtype={'code':str,'tradestatus':str,'isST':str,'adjustflag':str},parse_dates=['date'])
                date=pd.Timestamp(frame.date.iloc[0]) if len(frame) else pd.NaT
                if (len(frame)!=info['rows'] or pd.isna(date) or not frame.date.eq(date).all()
                        or date not in expected or date<=old_calendar[-1] or date in seen_dates
                        or frame.code.duplicated().any() or not frame.adjustflag.eq('3').all()
                        or not frame.isST.isin(['0','1']).all()):
                    raise ValueError('new raw daily identity, basis, state or coverage differs')
                seen_dates.add(date);captured.add(date)
                selected=frame.code.map(is_ashare)
                outside=frame.loc[~selected,['date','code']].copy()
                outside['reason']='outside_explicit_ashare_universe';exclusions.append(outside)
                active,rejected=stitch_code_prices(frame.loc[selected].rename(columns={'code':'symbol'}),changes)
                if not rejected.empty:
                    exclusions.append(rejected[['date','symbol','identity_issue']].rename(columns={'symbol':'code','identity_issue':'reason'}))
                active['quality_segment']=np.nan;active['source']='new_market'
                active['is_st']=active.isST.astype(int)
                for original,column in [('peTTM','pe_ttm'),('pbMRQ','pb_mrq')]:
                    active[column]=pd.to_numeric(active[original],errors='raise') if original in active else np.nan
                _insert(connection,active);proof['new_rows']+=len(active)
                if position % 20 == 0:
                    checkpoint()
                    print(f'Raw feature bridge: {position}/{len(files)} source sessions indexed',flush=True)
            if captured!=expected:
                raise ValueError('market capture is missing requested sessions')
            sessions.update(expected)
            proof['sources'].append(dict(manifest_sha256=sha256(market/'manifest.json'),sessions=len(expected),raw_rows=sum(info['rows'] for _,info in files)))
            checkpoint()
        # Source hashes must still match after indexing, before publication.
        if sha256(baseline/'event_raw_prices.csv')!=proof['baseline_raw_sha256']:
            raise ValueError('baseline changed during indexing')
        excluded=pd.concat(exclusions,ignore_index=True) if exclusions else pd.DataFrame(columns=['date','code','reason'])
        excluded.to_csv(target/'excluded_quotes.csv',index=False)
        pd.DataFrame(dict(date=sorted(sessions))).to_csv(target/'calendar.csv',index=False)
        if connection.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
            raise ValueError('raw feature-store integrity check failed')
        proof.update(status='raw_indexed',excluded_rows=len(excluded),symbols=connection.execute('SELECT COUNT(DISTINCT symbol) FROM quotes').fetchone()[0],
            calendar_sha256=sha256(target/'calendar.csv'),excluded_sha256=sha256(target/'excluded_quotes.csv'))
        connection.close();connection=None
        proof['database_sha256']=sha256(target/'quotes.sqlite');checkpoint()
    except BaseException as exc:
        proof.update(status='failed',error=str(exc));checkpoint();raise
    finally:
        if connection is not None:connection.close()
    return proof


@contextmanager
def _verified_connection(directory):
    source=Path(directory)
    manifest=json.loads((source/'manifest.json').read_text(encoding='utf8'))
    if manifest.get('schema')!='raw_feature_store_v1' or manifest.get('status')!='raw_indexed':
        raise ValueError('incomplete raw feature store')
    database=source/'quotes.sqlite'
    connection=sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)
    try:
        connection.execute('BEGIN')
        connection.execute('SELECT symbol FROM quotes LIMIT 1').fetchone()
        wal=database.with_name(database.name+'-wal')
        if wal.exists() and wal.stat().st_size:
            raise ValueError('sealed raw store has an unsealed WAL')
        if sha256(database)!=manifest['database_sha256'] or sha256(source/'calendar.csv')!=manifest['calendar_sha256']:
            raise ValueError('raw feature store evidence changed')
        yield connection
    finally:connection.close()


def issuer_raw_quotes(directory,symbol):
    """Verify the frozen store, then read one issuer. Never reinterpret as adjusted."""
    with _verified_connection(directory) as connection:
        return pd.read_sql_query('SELECT * FROM quotes WHERE symbol=? ORDER BY date',connection,params=(symbol,),parse_dates=['date'])


def iter_issuer_raw_quotes(directory):
    """Verify once and hold a consistent read transaction during reconstruction."""
    with _verified_connection(directory) as connection:
        symbols=[row[0] for row in connection.execute('SELECT DISTINCT symbol FROM quotes ORDER BY symbol')]
        for symbol in symbols:
            yield symbol,pd.read_sql_query('SELECT * FROM quotes WHERE symbol=? ORDER BY date',connection,params=(symbol,),parse_dates=['date'])

"""Bounded-memory quote store and separate immutable reference audits.

Raw quotes are cached once; a new notice overlay creates only a small fault
audit, not another copy of the historical database. No price is repaired here.
"""
from pathlib import Path
from datetime import datetime,timezone
import csv,hashlib,json,shutil,sqlite3
import numpy as np
import pandas as pd
from .phase2_protocol import sha256
from .causal_quality import quality_inputs


def code_hashes(names):
    return {n:hashlib.sha256(Path(__file__).with_name(n).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest() for n in names}


def exchange_dates(values):
    cal=pd.DatetimeIndex(pd.to_datetime(values))
    if cal.empty or cal.hasnans or cal.has_duplicates or cal.tz is not None or not cal.is_monotonic_increasing or not cal.equals(cal.normalize()):raise ValueError('ordered unique normalized exchange dates required')
    return cal


def write_manifest(path,value):
    temp=Path(path).with_suffix('.next.json');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8');temp.replace(path)


def iter_groups(path,key,columns,chunksize=25000):
    """Sorted CSV, one complete issuer or decision date at a time."""
    if isinstance(chunksize,bool) or not isinstance(chunksize,int) or chunksize<1:raise ValueError('positive integer CSV chunk size required')
    carry=pd.DataFrame();last=None
    for chunk in pd.read_csv(path,usecols=columns,dtype={'symbol':str},float_precision='round_trip',chunksize=chunksize):
        if chunk[key].isna().any():raise ValueError('missing sorted CSV key')
        frame=pd.concat([carry,chunk],ignore_index=True) if not carry.empty else chunk
        if not frame[key].is_monotonic_increasing:raise ValueError('CSV must be ordered by '+key)
        final=frame[key].iloc[-1]
        for value,group in frame[frame[key].ne(final)].groupby(key,sort=False):
            if last is not None and value<=last:raise ValueError('repeated sorted CSV group')
            last=value;yield value,group.reset_index(drop=True)
        carry=frame[frame[key].eq(final)].copy()
    if not carry.empty:
        value=carry[key].iloc[0]
        if last is not None and value<=last:raise ValueError('repeated sorted CSV group')
        yield value,carry.reset_index(drop=True)


def _fresh(out,inputs):
    target=Path(out).resolve()
    if target.exists() or any(target.is_relative_to(p.resolve()) or p.resolve().is_relative_to(target) for p in inputs):raise ValueError('fresh cache directory outside its inputs required')
    target.mkdir(parents=True);return target


def build_quote_store(raw_path,calendar,out,chunksize=25000):
    source=Path(raw_path).resolve();cal=exchange_dates(calendar);target=_fresh(out,[source])
    codes=code_hashes(['disk_market.py']);proof=dict(schema='bounded_raw_quote_store_v1',status='building',started_at_utc=datetime.now(timezone.utc).isoformat(),raw_sha256=sha256(source),raw_basename=source.name,code_hashes=codes,issuers=0,rows=0,max_issuer_rows=0,csv_chunk_rows=chunksize,execution_flags='Original supplied flags when present; otherwise original raw trading-status/open policy. Candidate exclusions are applied by original eligibility keys.',prices_repaired=False)
    pd.DataFrame(dict(date=cal)).to_csv(target/'calendar.csv',index=False);proof['calendar_sha256']=sha256(target/'calendar.csv');write_manifest(target/'manifest.json',proof)
    connection=sqlite3.connect(target/'quotes.building.sqlite')
    connection.execute('PRAGMA cache_size=-16384');connection.execute('PRAGMA journal_mode=DELETE');connection.execute('PRAGMA temp_store=MEMORY')
    connection.execute('CREATE TABLE quotes(date TEXT,symbol TEXT,open REAL NOT NULL,close REAL NOT NULL,preclose REAL NOT NULL,tradestatus TEXT NOT NULL,terminal_date TEXT,can_buy INTEGER NOT NULL,can_sell INTEGER NOT NULL,PRIMARY KEY(date,symbol)) WITHOUT ROWID')
    connection.execute('CREATE TABLE issuers(symbol TEXT PRIMARY KEY,first_day TEXT,last_day TEXT,rows INTEGER) WITHOUT ROWID')
    required=['date','symbol','open','close','preclose','tradestatus'];header=pd.read_csv(source,nrows=0).columns
    if not set(required).issubset(header):connection.close();proof.update(status='failed',error='raw quote schema incomplete');write_manifest(target/'manifest.json',proof);raise ValueError('raw quote schema incomplete')
    columns=required+[n for n in ['terminal_date','can_buy','can_sell'] if n in header]
    try:
        for symbol,bars in iter_groups(source,'symbol',columns,chunksize):
            dates=exchange_dates(bars.date)
            if not dates.isin(cal).all() or not symbol or bars.symbol.isna().any():raise ValueError('invalid raw quote identity/calendar')
            state=bars.tradestatus.astype(str)
            if not state.isin(['0','1']).all():raise ValueError('known raw trading status required')
            for name in ['open','close','preclose']:
                bars[name]=pd.to_numeric(bars[name],errors='raise')
                if not np.isfinite(bars[name]).all() or bars[name].lt(0).any() or name!='open' and bars[name].eq(0).any():raise ValueError('invalid raw valuation')
            terminal=pd.to_datetime(bars.terminal_date,format='mixed') if 'terminal_date' in bars else pd.Series(pd.NaT,index=bars.index)
            known=terminal.dropna()
            if known.dt.tz is not None or not known.eq(known.dt.normalize()).all():raise ValueError('normalized terminal dates required')
            tradable=state.eq('1')&bars.open.gt(0);flags={}
            for name in ['can_buy','can_sell']:
                if name in bars:
                    values=bars[name].astype(str);mapping={'True':True,'False':False,'0':False,'1':True}
                    if not values.isin(mapping).all():raise ValueError('known binary execution flags required')
                    flags[name]=values.map(mapping)&tradable
                else:flags[name]=tradable
            rows=((str(d.date()),symbol,float(o),float(c),float(p),str(s),None if pd.isna(t) else str(t.date()),int(b),int(e)) for d,o,c,p,s,t,b,e in zip(dates,bars.open,bars.close,bars.preclose,state,terminal,flags['can_buy'],flags['can_sell']))
            connection.executemany('INSERT INTO quotes VALUES(?,?,?,?,?,?,?,?,?)',rows)
            connection.execute('INSERT INTO issuers VALUES(?,?,?,?)',(symbol,str(dates[0].date()),str(dates[-1].date()),len(bars)))
            proof['issuers']+=1;proof['rows']+=len(bars);proof['max_issuer_rows']=max(proof['max_issuer_rows'],len(bars))
            if proof['issuers']%250==0:connection.commit();write_manifest(target/'manifest.json',proof);print(json.dumps(dict(stage='quote_store',issuers=proof['issuers'],rows=proof['rows'])),flush=True)
        connection.commit()
        if proof['rows']==0 or connection.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError('empty or corrupt quote store')
        connection.close();connection=None
        if sha256(source)!=proof['raw_sha256'] or code_hashes(list(codes))!=codes:raise ValueError('raw source or cache code changed during build')
        (target/'quotes.building.sqlite').rename(target/'quotes.sqlite')
        proof.update(status='raw_quote_store_complete',completed_at_utc=datetime.now(timezone.utc).isoformat(),database_sha256=sha256(target/'quotes.sqlite'));write_manifest(target/'manifest.json',proof);return proof
    except BaseException as e:proof.update(status='failed',error=str(e));write_manifest(target/'manifest.json',proof);raise
    finally:
        if connection is not None:connection.close()


class QuoteStore:
    def __init__(self,directory):
        self.path=Path(directory).resolve();self.manifest=json.loads((self.path/'manifest.json').read_text(encoding='utf8'));m=self.manifest
        if m.get('schema')!='bounded_raw_quote_store_v1' or m.get('status')!='raw_quote_store_complete' or sha256(self.path/'quotes.sqlite')!=m.get('database_sha256') or sha256(self.path/'calendar.csv')!=m.get('calendar_sha256') or code_hashes(list(m['code_hashes']))!=m['code_hashes']:raise ValueError('complete unchanged quote store required')
        self.calendar=exchange_dates(pd.read_csv(self.path/'calendar.csv').date)
        self.connection=sqlite3.connect((self.path/'quotes.sqlite').as_uri()+'?mode=ro',uri=True);self.connection.execute('PRAGMA query_only=ON');self.connection.execute('PRAGMA cache_size=-4096')
    def close(self):self.connection.close()
    def has_day(self,date,symbols):return any(s in symbols for (s,) in self.connection.execute('SELECT symbol FROM quotes WHERE date=?',(str(pd.Timestamp(date).date()),)))
    def day(self,date,symbols):
        return {r[0]:dict(open=np.float64(r[1]),close=np.float64(r[2]),preclose=np.float64(r[3]),tradestatus=r[4],terminal_date=None if r[5] is None else pd.Timestamp(r[5]),can_buy=bool(r[6]),can_sell=bool(r[7])) for r in self.connection.execute('SELECT symbol,open,close,preclose,tradestatus,terminal_date,can_buy,can_sell FROM quotes WHERE date=? ORDER BY symbol',(str(pd.Timestamp(date).date()),)) if r[0] in symbols}


def load_actions(path):
    a=pd.read_csv(path,float_precision='round_trip',low_memory=False)
    for name in ['ex_date','available_date','pay_date']:a[name]=pd.to_datetime(a[name],format='mixed')
    return a


def build_reference_audit(store_directory,raw_path,actions_path,out,reference_tick_size=.01):
    source=Path(actions_path).resolve();raw=Path(raw_path).resolve();target=_fresh(out,[Path(store_directory),raw,source]);store=QuoteStore(store_directory)
    codes=code_hashes(['disk_market.py','causal_quality.py','price_action_audit.py','price_reference.py'])
    proof=dict(schema='bounded_reference_audit_v1',status='building',started_at_utc=datetime.now(timezone.utc).isoformat(),quote_store_manifest_sha256=sha256(store.path/'manifest.json'),quote_database_sha256=store.manifest['database_sha256'],raw_sha256=store.manifest['raw_sha256'],actions_sha256=sha256(source),reference_tick_size=reference_tick_size,code_hashes=codes,issuers=0,rows=0,reference_faults=0,max_issuer_rows=0,actual_holder_terms_changed=False)
    shutil.copyfile(source,target/'actions.csv');write_manifest(target/'manifest.json',proof)
    a=load_actions(source);indices=a.groupby('symbol',sort=False).indices;connection=sqlite3.connect(target/'faults.sqlite');connection.execute('CREATE TABLE faults(date TEXT,symbol TEXT,PRIMARY KEY(date,symbol)) WITHOUT ROWID')
    try:
        if sha256(raw)!=store.manifest['raw_sha256']:raise ValueError('reference audit must use the identical original raw source')
        for symbol,p in iter_groups(raw,'symbol',['date','symbol','open','close','preclose','tradestatus']):
            p['date']=pd.to_datetime(p.date)
            events=a if proof['issuers']==0 else a.iloc[indices.get(symbol,[])]
            checked,_,_=quality_inputs(p,events,store.calendar,reference_tick_size)
            faulty=checked[checked.reference_fault]
            connection.executemany('INSERT INTO faults VALUES(?,?)',((str(d.date()),s) for d,s in zip(faulty.date,faulty.symbol)))
            proof['issuers']+=1;proof['rows']+=len(p);proof['reference_faults']+=len(faulty);proof['max_issuer_rows']=max(proof['max_issuer_rows'],len(p))
            if proof['issuers']%250==0:connection.commit();write_manifest(target/'manifest.json',proof);print(json.dumps(dict(stage='reference_audit',issuers=proof['issuers'],rows=proof['rows'],faults=proof['reference_faults'])),flush=True)
        connection.commit();connection.close();connection=None
        if (proof['rows'],proof['issuers'])!=(store.manifest['rows'],store.manifest['issuers']) or sha256(raw)!=proof['raw_sha256'] or sha256(source)!=proof['actions_sha256'] or code_hashes(list(codes))!=codes or sha256(store.path/'quotes.sqlite')!=proof['quote_database_sha256']:raise ValueError('reference input or code changed')
        proof.update(status='reference_audit_complete',completed_at_utc=datetime.now(timezone.utc).isoformat(),fault_database_sha256=sha256(target/'faults.sqlite'));write_manifest(target/'manifest.json',proof);return proof
    except BaseException as e:proof.update(status='failed',error=str(e));write_manifest(target/'manifest.json',proof);raise
    finally:
        store.close()
        if connection is not None:connection.close()


class ReferenceAudit:
    def __init__(self,directory,store):
        self.path=Path(directory).resolve();m=json.loads((self.path/'manifest.json').read_text(encoding='utf8'));self.manifest=m
        if m.get('schema')!='bounded_reference_audit_v1' or m.get('status')!='reference_audit_complete' or m.get('quote_store_manifest_sha256')!=sha256(store.path/'manifest.json') or m.get('quote_database_sha256')!=store.manifest['database_sha256'] or sha256(self.path/'faults.sqlite')!=m.get('fault_database_sha256') or sha256(self.path/'actions.csv')!=m.get('actions_sha256') or code_hashes(list(m['code_hashes']))!=m['code_hashes']:raise ValueError('complete unchanged reference audit required')
        self.actions=load_actions(self.path/'actions.csv');self.connection=sqlite3.connect((self.path/'faults.sqlite').as_uri()+'?mode=ro',uri=True);self.connection.execute('PRAGMA query_only=ON')
    def close(self):self.connection.close()
    def day_faults(self,date):return {s for (s,) in self.connection.execute('SELECT symbol FROM faults WHERE date=?',(str(pd.Timestamp(date).date()),))}

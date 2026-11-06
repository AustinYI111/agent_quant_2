"""Append verified daily snapshot backups without recreating forecast clocks.

Expected manifest hashes must come from the original snapshot receipt or its
verified backup. Import time is separate from the original prediction time.
"""
from pathlib import Path
from datetime import datetime,timezone
import argparse,json,re,sqlite3
import pandas as pd
from .phase2_protocol import sha256,load_seal,utc
from .prediction_ledger import _connect,_json,backup_ledger
from .paper_tracking import _verified_batch
from .public_data import is_ashare
from .streaming_feature_index import ExchangeCalendar


def _source_unchanged(snapshot,expected_manifest,expected_backup):
    if sha256(snapshot/'manifest.json')!=expected_manifest or sha256(snapshot/'prediction_backup.sqlite')!=expected_backup:
        raise ValueError('daily snapshot changed or differs from expected hash')
    wal=Path(str(snapshot/'prediction_backup.sqlite')+'-wal')
    if wal.exists() and wal.stat().st_size:raise ValueError('daily source must be a closed consistent backup, not a live WAL database')


def validate_archive_destination(target):
    """Never initialize prediction tables inside another project database."""
    target=Path(target)
    if not target.exists():return
    if not target.is_file():raise ValueError('archive destination is not a database file')
    try:
        with sqlite3.connect(target.resolve().as_uri()+'?mode=ro',uri=True) as connection:
            tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            known={'batches','scores','archive_contract','archive_imports'}
            if not tables.issubset(known):raise ValueError('destination is not a prediction archive')
            if 'archive_contract' not in tables:
                for table in tables:
                    if connection.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]:raise ValueError('nonempty destination is not an initialized prediction archive')
    except sqlite3.DatabaseError as error:raise ValueError('destination is not a prediction archive') from error


def archive_daily_snapshot(snapshot,expected_manifest_sha256,archive,project,seal_directory,calendar,synthetic=False):
    """Transactionally append all three horizons, preserving receipts verbatim.

    Identical imports are idempotent. Any revision of an existing forecast,
    including its original clock, is rejected rather than replacing history.
    This validates transfer integrity, not upstream data quality or profitability.
    """
    root=Path(project).resolve();source=Path(snapshot).resolve();target=Path(archive).resolve()
    if not isinstance(synthetic,bool) or not re.fullmatch('[0-9a-f]{64}',expected_manifest_sha256):raise ValueError('explicit data kind and original manifest SHA-256 required')
    if not source.is_relative_to(root) or not target.is_relative_to(root):raise ValueError('prediction archive must remain within project')
    if target.is_relative_to(source) or source.is_relative_to(target):raise ValueError('archive must be separate from immutable source snapshot')
    if sha256(source/'manifest.json')!=expected_manifest_sha256:raise ValueError('daily snapshot manifest hash differs')
    manifest=json.loads((source/'manifest.json').read_text(encoding='utf8'))
    if manifest.get('schema')!='frozen_daily_research_snapshot_v1' or manifest.get('status')!='research_snapshot_saved' or manifest.get('model_selection_performed') is not False:
        raise ValueError('complete frozen daily inference snapshot required')
    backup=manifest['ledger_backup'];expected_backup=backup['sha256']
    if backup.get('schema')!='prediction_ledger_backup_v1' or not re.fullmatch('[0-9a-f]{64}',expected_backup):raise ValueError('sealed daily ledger backup required')
    _source_unchanged(source,expected_manifest_sha256,expected_backup)
    if (source/'prediction_backup.sqlite').stat().st_size!=backup['bytes']:raise ValueError('daily backup size differs')
    cal=ExchangeCalendar(calendar).dates;protocol,seal=load_seal(seal_directory,root)
    if manifest['protocol_sha256']!=seal['protocol_sha256']:raise ValueError('daily source uses another frozen protocol')
    expected=manifest['receipts']
    if len(expected)!=len(protocol['models']) or {r['horizon'] for r in expected}!=set(protocol['models']) or len({r['batch_id'] for r in expected})!=len(expected):
        raise ValueError('daily source must include each frozen horizon exactly once')
    started,completed=utc(manifest['started_at_utc']),utc(manifest['completed_at_utc'])
    now=datetime.now(timezone.utc)
    if started>completed or completed>utc(now):raise ValueError('daily completion clock is invalid or in the future')
    database=source/'prediction_backup.sqlite'
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as connection:
        if connection.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or connection.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('daily backup integrity failed')
        stored=connection.execute('SELECT batch_id,identity_sha256,content_sha256,receipt_json FROM batches').fetchall()
    if {row[0] for row in stored}!={r['batch_id'] for r in expected} or len(stored)!=len(expected):raise ValueError('daily backup batches differ from snapshot receipt')
    stored={row[0]:row for row in stored};incoming=[]
    for original in expected:
        batch_id=original['batch_id'];receipt,scores=_verified_batch(database,batch_id,protocol,seal,cal,synthetic)
        row=stored[batch_id]
        if receipt.get('strategy')!='frozen_baseline' or receipt.get('model_ready') is not False or receipt.get('score_is_expected_return') is not False:raise ValueError('frozen research forecast contract differs')
        if row[1]!=batch_id or row[2]!=receipt['content_sha256'] or receipt!=original:raise ValueError('daily batch receipt differs from manifest')
        if receipt['signal_date']!=manifest['signal_date'] or receipt['candidate_count']!=manifest['eligible']:raise ValueError('daily signal/candidate count differs from manifest')
        if not started<=utc(receipt['created_at_utc'])<=completed:raise ValueError('original forecast clock falls outside daily run')
        if scores['rank'].tolist()!=list(range(1,len(scores)+1)) or not scores.trading_symbol.map(is_ashare).all():raise ValueError('daily ranks or trading identities invalid')
        if synthetic and (receipt['role']!='synthetic_test' or receipt['prospective']):raise ValueError('synthetic source cannot impersonate a prospective forecast')
        incoming.append((receipt,scores,row[3]))
    _source_unchanged(source,expected_manifest_sha256,expected_backup)
    validate_archive_destination(target)
    connection=_connect(target)
    try:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('CREATE TABLE IF NOT EXISTS archive_contract (singleton INTEGER PRIMARY KEY CHECK(singleton=1),protocol_sha256 TEXT NOT NULL,synthetic INTEGER NOT NULL)')
        connection.execute('CREATE TABLE IF NOT EXISTS archive_imports (source_manifest_sha256 TEXT PRIMARY KEY,source_backup_sha256 TEXT NOT NULL,receipt_json TEXT NOT NULL)')
        contract=connection.execute('SELECT protocol_sha256,synthetic FROM archive_contract WHERE singleton=1').fetchone()
        desired=(seal['protocol_sha256'],int(synthetic))
        if contract is None:
            if connection.execute('SELECT COUNT(*) FROM batches').fetchone()[0]:raise ValueError('nonempty destination lacks a matching archive contract')
            connection.execute('INSERT INTO archive_contract VALUES (1,?,?)',desired)
        elif contract!=desired:raise ValueError('archive protocol or dataset kind differs')
        for table,key in [('archive_contract','singleton'),('archive_imports','source_manifest_sha256')]:
            connection.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'append-only archive'); END")
            connection.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'append-only archive'); END")
            connection.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table} WHEN EXISTS (SELECT 1 FROM {table} WHERE {key}=NEW.{key}) BEGIN SELECT RAISE(ABORT,'append-only archive'); END")
        new=[];duplicates=[]
        # Validate every conflict before inserting any batch in this transaction.
        for receipt,scores,raw_receipt in incoming:
            existing=connection.execute('SELECT receipt_json FROM batches WHERE batch_id=?',(receipt['batch_id'],)).fetchone()
            if existing:
                restored,_=_verified_batch(target,receipt['batch_id'],protocol,seal,cal,synthetic)
                if json.loads(existing[0])!=receipt or restored!=receipt:raise ValueError('archived forecast cannot be revised, replaced or reclocked')
                duplicates.append(receipt['batch_id'])
            else:new.append((receipt,scores,raw_receipt))
        cached=connection.execute('SELECT source_backup_sha256,receipt_json FROM archive_imports WHERE source_manifest_sha256=?',(expected_manifest_sha256,)).fetchone()
        if cached:
            if cached[0]!=expected_backup or new:raise ValueError('existing archive import is inconsistent')
            _source_unchanged(source,expected_manifest_sha256,expected_backup);connection.rollback();return json.loads(cached[1])
        for receipt,scores,raw_receipt in new:
            batch_id=receipt['batch_id']
            connection.execute('INSERT INTO batches VALUES (?,?,?,?)',(batch_id,batch_id,receipt['content_sha256'],raw_receipt))
            connection.executemany('INSERT INTO scores VALUES (?,?,?,?,?)',[(batch_id,r.symbol,r.trading_symbol,float(r.score),int(r.rank)) for r in scores.itertuples()])
        result=dict(schema='daily_prediction_archive_import_v1',imported_at_utc=now.isoformat(),source_manifest_sha256=expected_manifest_sha256,source_backup_sha256=expected_backup,protocol_sha256=seal['protocol_sha256'],synthetic=synthetic,new_batches=len(new),new_scores=sum(len(x[1]) for x in new),duplicate_batches=len(duplicates),batch_ids=[r['batch_id'] for r,_,_ in incoming],original_clocks_preserved=True,forecast_roles_preserved=True,certified=False,model_ready=False)
        _source_unchanged(source,expected_manifest_sha256,expected_backup)
        if connection.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('archive contains orphan scores')
        connection.execute('INSERT INTO archive_imports VALUES (?,?,?)',(expected_manifest_sha256,expected_backup,_json(result)))
        connection.commit();return result
    except BaseException:
        connection.rollback();raise
    finally:connection.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project',default='.')
    for name in ['snapshot','manifest-sha256','archive','seal','calendar']:parser.add_argument('--'+name,required=True)
    parser.add_argument('--backup-out');args=parser.parse_args();root=Path(args.project).resolve()
    def local(value):
        p=Path(value)
        if p.is_absolute() or '..' in p.parts or ':' in value:raise ValueError('portable project-relative archive paths required')
        result=(root/p).resolve()
        if not result.is_relative_to(root):raise ValueError('archive path escapes project')
        return result
    result=archive_daily_snapshot(local(args.snapshot),args.manifest_sha256,local(args.archive),root,local(args.seal),pd.to_datetime(pd.read_csv(local(args.calendar)).date))
    if args.backup_out:result=dict(result,backup=backup_ledger(local(args.archive),local(args.backup_out)))
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()

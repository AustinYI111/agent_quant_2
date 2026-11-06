"""Append-only research prediction receipts. Scores are not expected returns."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sqlite3

import numpy as np
import pandas as pd

from .phase2_protocol import day, forecast_role, load_seal, sha256, utc
from .public_data import is_ashare


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf8')).hexdigest()


def _now():
    # Production callers cannot pass a historical creation clock to the writer.
    return datetime.now(timezone.utc)


def _connect(path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target, timeout=30)
    connection.execute('PRAGMA foreign_keys=ON')
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS batches (
            batch_id TEXT PRIMARY KEY, identity_sha256 TEXT UNIQUE NOT NULL,
            content_sha256 TEXT NOT NULL, receipt_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS scores (
            batch_id TEXT NOT NULL REFERENCES batches(batch_id),
            symbol TEXT NOT NULL, trading_symbol TEXT NOT NULL,
            score REAL NOT NULL, rank INTEGER NOT NULL,
            PRIMARY KEY(batch_id, symbol), UNIQUE(batch_id, rank));
        CREATE TRIGGER IF NOT EXISTS batches_no_update BEFORE UPDATE ON batches
            BEGIN SELECT RAISE(ABORT, 'append-only predictions'); END;
        CREATE TRIGGER IF NOT EXISTS batches_no_delete BEFORE DELETE ON batches
            BEGIN SELECT RAISE(ABORT, 'append-only predictions'); END;
        CREATE TRIGGER IF NOT EXISTS scores_no_update BEFORE UPDATE ON scores
            BEGIN SELECT RAISE(ABORT, 'append-only predictions'); END;
        CREATE TRIGGER IF NOT EXISTS scores_no_delete BEFORE DELETE ON scores
            BEGIN SELECT RAISE(ABORT, 'append-only predictions'); END;
        CREATE TRIGGER IF NOT EXISTS batches_no_replace BEFORE INSERT ON batches
            WHEN EXISTS (SELECT 1 FROM batches WHERE batch_id=NEW.batch_id OR identity_sha256=NEW.identity_sha256)
            BEGIN SELECT RAISE(ABORT, 'append-only predictions'); END;
        CREATE TRIGGER IF NOT EXISTS scores_no_replace BEFORE INSERT ON scores
            WHEN EXISTS (SELECT 1 FROM scores WHERE batch_id=NEW.batch_id AND (symbol=NEW.symbol OR rank=NEW.rank))
            BEGIN SELECT RAISE(ABORT, 'append-only predictions'); END;
    ''')
    return connection


def record_predictions(database, *, project, seal_directory, horizon, signal_date,
                       scores, expected_symbols, sources, calendar, synthetic=False):
    """Persist all eligible scores with frozen model and hashed input receipts.

    sources: nonempty list of {path, sha256, observed_at_utc}; must include the
    scored feature artifact and universe/eligibility evidence. Quality of those
    upstream artifacts remains an explicit research limitation, not certification.
    expected_symbols is the upstream complete eligible universe, not the Top 20.
    Synthetic runs are labelled and cannot share a real batch identity.
    """
    created = utc(_now())
    protocol, seal = load_seal(seal_directory, project)
    if horizon not in protocol['models']:
        raise ValueError('unknown frozen horizon')
    if not isinstance(synthetic, bool):
        raise ValueError('explicit boolean dataset kind required')
    signal = str(day(signal_date).date())
    root = Path(project).resolve()
    if not sources:
        raise ValueError('hashed feature and source evidence required')
    verified = []
    for source in sources:
        relative = Path(source['path'])
        path = (root / relative).resolve()
        if relative.is_absolute() or ':' in str(relative) or '..' in relative.parts or not path.is_relative_to(root):
            raise ValueError('input must have a portable project-relative path')
        if sha256(path) != source['sha256']:
            raise ValueError('prediction source hash differs')
        verified.append(dict(path=relative.as_posix(), sha256=source['sha256'],
                             observed_at_utc=utc(source['observed_at_utc']).isoformat()))
    if len({item['path'] for item in verified}) != len(verified):
        raise ValueError('duplicate source evidence')
    verified.sort(key=lambda item: item['path'])
    observed = max(utc(item['observed_at_utc']) for item in verified)
    timing = forecast_role(signal, created, observed, calendar, protocol)
    required = {'symbol', 'trading_symbol', 'score', 'is_st', 'eligible'}
    if not required.issubset(scores):
        raise ValueError('scores require dated eligibility and trading identities')
    frame = scores.copy()
    expected = list(expected_symbols)
    if (not expected or any(not isinstance(item, str) or not item for item in expected)
            or len(set(expected)) != len(expected)):
        raise ValueError('complete unique eligible universe required')
    if (frame.symbol.isna().any() or frame.symbol.duplicated().any()
            or set(frame.symbol) != set(expected)):
        raise ValueError('scores must cover the complete eligible universe exactly once')
    if frame.trading_symbol.duplicated().any() or not frame.trading_symbol.map(
            lambda item: isinstance(item, str) and is_ashare(item)).all():
        raise ValueError('unique Shanghai/Shenzhen A-share identities required; Beijing excluded')
    for column, expected_flag in [('is_st', False), ('eligible', True)]:
        if not frame[column].map(lambda value: isinstance(value, (bool, np.bool_)) and bool(value) == expected_flag).all():
            raise ValueError('ineligible or ST candidate, or unknown eligibility')
    if not frame.score.map(lambda value: not isinstance(value, (bool, np.bool_)) and isinstance(value, (int, float, np.number))).all():
        raise ValueError('numeric finite scores required')
    if not np.isfinite(frame.score.to_numpy(dtype=float)).all():
        raise ValueError('numeric finite scores required')
    if 'date' in frame and not frame.date.map(lambda value: str(day(value).date()) == signal).all():
        raise ValueError('scores contain a different signal date')
    if len(frame) < 20:
        raise ValueError('at least 20 eligible scores required')
    frame = frame.sort_values(['score', 'symbol'], ascending=[False, True]).reset_index(drop=True)
    rows = [dict(symbol=str(row.symbol), trading_symbol=row.trading_symbol,
                 score=float(row.score), rank=index + 1) for index, row in enumerate(frame.itertuples())]
    identity = dict(protocol_sha256=seal['protocol_sha256'], signal_date=signal,
                    horizon=horizon, model_sha256=protocol['models'][horizon]['files_sha256']['model.txt'],
                    strategy='frozen_baseline', synthetic=synthetic)
    identity_sha = _digest(identity)
    content_sha = _digest(dict(identity=identity, sources=verified, scores=rows))
    receipt = dict(schema='prediction_ledger_batch_v1', **(identity | timing),
                   batch_id=identity_sha, content_sha256=content_sha, candidate_count=len(rows),
                   universe_sha256=_digest(sorted(expected)), sources=verified,
                   model_ready=False, score_is_expected_return=False,
                   limitations='Research only; source vintages and live execution are not certified.')
    if synthetic:
        receipt.update(role='synthetic_test', prospective=False)
    connection = _connect(database)
    try:
        connection.execute('BEGIN IMMEDIATE')
        existing = connection.execute('SELECT content_sha256,receipt_json FROM batches WHERE identity_sha256=?', (identity_sha,)).fetchone()
        if existing:
            if existing[0] != content_sha:
                raise ValueError('existing forecast cannot be revised or replaced')
            connection.rollback()
            return json.loads(existing[1])
        connection.execute('INSERT INTO batches VALUES (?,?,?,?)', (identity_sha, identity_sha, content_sha, _json(receipt)))
        connection.executemany('INSERT INTO scores VALUES (?,?,?,?,?)',
                               [(identity_sha, row['symbol'], row['trading_symbol'], row['score'], row['rank']) for row in rows])
        connection.commit()
    finally:
        connection.close()
    return receipt


def read_predictions(database, batch_id, top_k=None):
    if top_k is not None and (isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1):
        raise ValueError('positive integer top_k required')
    # Read-only connection must never create an empty database on a typo.
    connection = sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        found = connection.execute('SELECT receipt_json FROM batches WHERE batch_id=?', (batch_id,)).fetchone()
        if found is None:
            raise ValueError('unknown prediction batch')
        frame = pd.read_sql_query('SELECT symbol,trading_symbol,score,rank FROM scores WHERE batch_id=? ORDER BY rank', connection, params=(batch_id,))
    finally:
        connection.close()
    return json.loads(found[0]), frame if top_k is None else frame.head(top_k)


def backup_ledger(database, output):
    """Transaction-consistent SQLite backup including committed WAL contents."""
    target = Path(output)
    if target.exists():
        raise ValueError('backup output must be new')
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents a racing backup from overwriting another file.
    with target.open('xb'):
        pass
    try:
        with sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True) as source:
            with sqlite3.connect(target) as destination:
                source.backup(destination)
                destination.execute('PRAGMA journal_mode=DELETE')
                if destination.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('prediction backup integrity check failed')
                if destination.execute('PRAGMA foreign_key_check').fetchall():
                    raise ValueError('prediction backup has orphan scores')
        return dict(schema='prediction_ledger_backup_v1', sha256=sha256(target), bytes=target.stat().st_size)
    except BaseException:
        target.unlink(missing_ok=True)
        raise

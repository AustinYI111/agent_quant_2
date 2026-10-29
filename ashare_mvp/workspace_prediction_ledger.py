"""Original append-only prediction contract with explicit external evidence paths.

Model, timing, eligibility, identity, score hashing and transaction checks follow
prediction_ledger unchanged. External locations use portable @work references.
"""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from .prediction_ledger import _now, _connect, _digest, _json
from .phase2_protocol import utc, day, forecast_role, load_seal, sha256
from .public_data import is_ashare
from .workspace_paths import WorkspacePaths

def record_predictions(database, *, project, seal_directory, horizon, signal_date,
                       scores, expected_symbols, sources, calendar, synthetic=False, work_root=None):
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
    paths = WorkspacePaths(root, work_root)
    paths.name(database)
    if not sources:
        raise ValueError('hashed feature and source evidence required')
    verified = []
    for source in sources:
        path = paths.resolve(source['path'])
        relative = Path(paths.name(path))
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

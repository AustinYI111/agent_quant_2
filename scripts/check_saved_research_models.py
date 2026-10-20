"""Verify restored research models against sealed features; no feature rebuild or training."""
from pathlib import Path
import argparse
from datetime import datetime, timezone
import hashlib
import json
import numpy as np
import pandas as pd
from ashare_mvp.horizon_inference import check_bundle


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def verify(root, out):
    root, out = Path(root), Path(out)
    if out.exists() and any(out.iterdir()):
        raise ValueError('new empty verification output required')
    state = root / 'artifacts/migration_real_20261020'
    run = root / 'artifacts/three_horizon_real_causal_quality1'
    delivery = root / 'artifacts/real_research_delivery_20261020'
    inputs = json.loads((state / 'real_horizon_input_audit_20261019.json').read_text(encoding='utf8'))
    replay = json.loads((root / 'artifacts/three_horizon_valuation_replay5/valuation_replay_audit.json').read_text(encoding='utf8'))
    top_meta = json.loads((delivery / 'historical_top20_metadata.json').read_text(encoding='utf8'))
    factor_sha = digest(run / 'factor_samples.csv')
    if factor_sha != inputs['artifact_sha256']['factor_samples.csv'] or factor_sha != top_meta['source_factor_file_sha256']:
        raise ValueError('sealed factor file hash differs')
    if digest(run / 'models/rankings.csv') != replay['original_experiment_files_sha256']['models/rankings.csv']:
        raise ValueError('original forecasts hash differs')
    if digest(delivery / 'historical_top20_20251231.csv') != top_meta['ranking_csv_sha256']:
        raise ValueError('historical Top20 hash differs')
    inventory = json.loads((delivery / 'portable_model_inventory.json').read_text(encoding='utf8'))
    if len(inventory) != 9 or {x['horizon'] for x in inventory} != {'5d', '20d', '12m'}:
        raise ValueError('this saved experiment requires nine models and three horizons')
    models = []
    for record in inventory:
        directory = (root / record['relative_path']).resolve()
        if not directory.is_relative_to(root.resolve()):
            raise ValueError('model path escapes restored root')
        booster, meta, _, _ = check_bundle(directory)
        if meta['model_sha256'] != record['model_sha256'] or meta['horizon']['key'] != record['horizon']:
            raise ValueError('model inventory differs')
        if pd.Timestamp(meta['trained_asof']) != pd.Timestamp(record['fold']):
            raise ValueError('model fold clock differs')
        models.append((record, booster, meta))
    asof = pd.Timestamp(top_meta['asof'])
    dates = {pd.Timestamp(record['fold']) for record in inventory} | {asof}
    parts = []
    for chunk in pd.read_csv(run / 'factor_samples.csv', parse_dates=['date', 'available_date'], float_precision='round_trip', chunksize=100000):
        parts.append(chunk[chunk.date.isin(dates)])
    samples = pd.concat(parts, ignore_index=True)
    if samples.duplicated(['date', 'symbol']).any() or not samples.available_date.le(samples.date).all():
        raise ValueError('invalid saved feature identity or visibility')
    parts = []
    for chunk in pd.read_csv(run / 'models/rankings.csv', usecols=['date', 'symbol', 'horizon', 'score'], parse_dates=['date'], float_precision='round_trip', chunksize=100000):
        parts.append(chunk[chunk.date.isin(dates)])
    ranks = pd.concat(parts, ignore_index=True)
    records = []
    for record, booster, meta in models:
        fold = pd.Timestamp(record['fold'])
        current = samples[samples.date.eq(fold)].set_index('symbol').sort_index()
        expected = ranks[ranks.horizon.eq(record['horizon']) & ranks.date.eq(fold)].set_index('symbol').reindex(current.index).score
        if len(current) < 20 or expected.isna().any():
            raise ValueError('saved original forecasts incomplete')
        scores = booster.predict(current[meta['features']])
        np.testing.assert_allclose(scores, expected.to_numpy(), rtol=0, atol=1e-12)
        records.append(dict(horizon=record['horizon'], fold=record['fold'], predictions=len(current), max_absolute_error=float(np.max(np.abs(scores - expected.to_numpy())))))
    historical = pd.read_csv(delivery / 'historical_top20_20251231.csv', float_precision='round_trip')
    tables = []
    for horizon in ['5d', '20d', '12m']:
        record, booster, meta = max((x for x in models if x[0]['horizon'] == horizon), key=lambda x: x[0]['fold'])
        if asof < pd.Timestamp(meta['trained_asof']):
            raise ValueError('historical snapshot predates model')
        current = samples[samples.date.eq(asof)].set_index('symbol').sort_index()
        scores = booster.predict(current[meta['features']])
        if not np.isfinite(scores).all():
            raise ValueError('nonfinite prediction scores')
        table = pd.DataFrame(dict(symbol=current.index, score=scores)).sort_values(['score', 'symbol'], ascending=[False, True]).head(20).reset_index(drop=True)
        expected = historical[historical.horizon.eq(horizon)].sort_values('rank')
        if table.symbol.tolist() != expected.symbol.tolist():
            raise ValueError('historical Top20 members differ')
        np.testing.assert_allclose(table.score.to_numpy(), expected.score.to_numpy(), rtol=0, atol=1e-12)
        table['rank'] = np.arange(1, 21)
        table['horizon'] = horizon
        table['date'] = str(asof.date())
        tables.append(table)
    proof = dict(schema='saved_real_model_verification_v1', observed_at_utc=datetime.now(timezone.utc).isoformat(), models_checked=len(models), predictions_checked=sum(x['predictions'] for x in records), max_absolute_error=max(x['max_absolute_error'] for x in records), historical_top20_verified=60, asof=str(asof.date()), factor_sha256=factor_sha, records=records, retrained=False, indicators_recomputed=False, certified=False, formal_model=False, scope='Saved research features only. Stable issuer keys, not live trading instructions. Does not test fresh raw-data feature engineering or a clean Windows installation.')
    out.mkdir(parents=True, exist_ok=True)
    pd.concat(tables, ignore_index=True).to_csv(out / 'verified_historical_top20.csv', index=False)
    (out / 'verification.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf8')
    return proof


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='root containing restored artifacts/')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.root, args.out), ensure_ascii=False))

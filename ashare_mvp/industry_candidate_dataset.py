"""Causal research industry features on the exact original eligible sample keys."""
from pathlib import Path
from datetime import datetime, timezone
import argparse, hashlib, json
import numpy as np
import pandas as pd
from .phase2_protocol import sha256, load_seal, day
from .streaming_snapshot import iter_seed_bars
from .technical_candidates import technical_candidate_rows, BOX_FEATURES, MARKET_FEATURES
from .archived_industry_research import archived_research_industry_features, FEATURES

CONTEXT = ['raw_momentum_63', 'raw_momentum_126', 'close_above_ma50']


def research_industry_symbol_rows(history, states, calendar, signals, research_end):
    if history.date.max() > day(research_end):
        raise ValueError('independent price bars cannot enter industry research')
    rows = technical_candidate_rows(history, states, calendar)
    selected = rows.loc[rows.date.isin(signals), ['date', 'symbol'] + CONTEXT]
    if len(selected) != len(signals) or selected.duplicated(['date', 'symbol']).any():
        raise ValueError('industry context must preserve original eligible signal keys')
    return selected


def normalize_industry_snapshots(raw):
    if set(raw) != {'date', 'symbol', *FEATURES}:
        raise ValueError('only dated declared industry inputs accepted; no outcomes')
    result = raw.copy().reset_index(drop=True)
    result['date'] = pd.to_datetime(result.date)
    result['symbol'] = result.symbol.astype(str)
    dates = pd.DatetimeIndex(result.date)
    if dates.hasnans or dates.tz is not None or not dates.equals(dates.normalize()) or raw.symbol.isna().any() or result.duplicated(['date', 'symbol']).any():
        raise ValueError('unique normalized industry feature keys required')
    positions = result.groupby('date', sort=False, observed=True).indices
    for name in FEATURES:
        values = pd.to_numeric(result[name], errors='raise').astype(float).to_numpy(copy=True)
        if np.isinf(values).any():
            raise ValueError('infinite industry input rejected')
        for indices in positions.values():
            current = pd.Series(values[indices]); low, high = current.quantile([.01, .99])
            clipped = current.clip(low, high); std = clipped.std(ddof=0)
            values[indices] = ((clipped-clipped.mean())/std if std > 0 else clipped*0).to_numpy()
        result[name] = values
    return result.sort_values(['date', 'symbol']).reset_index(drop=True)


def build_industry_candidates(project, dataset, experiment, technical_cache, membership_inputs, seal_directory, out):
    root = Path(project).resolve()
    def local(value):
        p = Path(value)
        if p.is_absolute() or '..' in p.parts or ':' in str(p):
            raise ValueError('project-relative industry candidate paths required')
        result = (root/p).resolve()
        if not result.is_relative_to(root):
            raise ValueError('industry candidate path escapes project')
        return result
    data, old, technical, membership, sealed, target = map(local, [dataset, experiment, technical_cache, membership_inputs, seal_directory, out])
    if target.exists():
        raise ValueError('new industry feature output required')
    if any(target.is_relative_to(p) or p.is_relative_to(target) for p in [data, old, technical, membership, sealed]):
        raise ValueError('industry feature output overlaps immutable input')
    protocol, seal = load_seal(sealed, root); end = day(protocol['research_end'])
    sources = {}
    def bind(path, expected=None):
        digest = sha256(path)
        if expected is not None and digest != expected:
            raise ValueError('industry feature source changed: ' + path.name)
        sources[path.relative_to(root).as_posix()] = digest
    memberships = json.loads((membership/'manifest.json').read_text(encoding='utf8'))
    previous = json.loads((technical/'manifest.json').read_text(encoding='utf8'))
    definition = json.loads((technical/'definition.json').read_text(encoding='utf8'))
    if memberships.get('schema') != 'verified_archived_industry_memberships_v1' or memberships.get('status') != 'verified_archived_research_memberships_ready' or memberships.get('research_training_admission') is not True or memberships.get('independent_data_used') is not False or memberships.get('protocol_sha256') != seal['protocol_sha256']:
        raise ValueError('reviewed archived research membership admission required')
    if previous.get('schema') != 'phase2_research_candidate_cache_v1' or previous.get('status') != 'research_candidates_built' or previous.get('independent_data_used') is not False or previous.get('protocol_sha256') != seal['protocol_sha256']:
        raise ValueError('completed original technical candidate cache required')
    for parent, manifest in [(membership, memberships), (technical, previous)]:
        bind(parent/'manifest.json')
        for name, digest in manifest['source_hashes'].items():
            bind(local(name), digest)
        for name, digest in manifest['output_hashes'].items():
            bind(parent/name, digest)
        for name, digest in manifest['code_hashes'].items():
            raw = (root/'ashare_mvp'/name).read_text(encoding='utf-8-sig').replace('\r\n', '\n').encode()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError('admitted input construction code changed: ' + name)
    bind(technical/'definition.json', previous['definition_sha256'])
    meta = json.loads((data/'metadata.json').read_text(encoding='utf8'))
    if meta.get('synthetic') is not False or meta.get('quality_policy', {}).get('schema') != 'causal_quality_research_v1':
        raise ValueError('real original causal research prices required')
    bind(data/'metadata.json')
    for name in ['calendar.csv', 'prices.csv']:
        bind(data/name, meta['converted_hashes'][name])
    summary = json.loads((old/'models/summary.json').read_text(encoding='utf8'))
    bind(old/'models/summary.json')
    bind(old/'factor_samples.csv', summary['input_frame_sha256']['samples'])
    if definition['base_features'] != summary['features']:
        raise ValueError('original base feature order differs')
    declared = {g['id']: g['features'] for g in protocol['candidate_groups']}
    if declared['industry'] != FEATURES or declared['market_state'] != MARKET_FEATURES or declared['oneil_box'] != BOX_FEATURES:
        raise ValueError('industry or combined features differ from frozen protocol')
    calendar = pd.DatetimeIndex(pd.to_datetime(pd.read_csv(data/'calendar.csv').date))
    if calendar.max() > end:
        raise ValueError('independent calendar cannot enter industry research')
    states = pd.read_csv(technical/'market_states.csv', parse_dates=['date'], float_precision='round_trip')
    if not pd.DatetimeIndex(states.date).equals(calendar):
        raise ValueError('complete original market-state calendar required')
    keys = pd.read_csv(old/'factor_samples.csv', usecols=['date', 'symbol', 'available_date'], parse_dates=['date', 'available_date'])
    if keys[['date', 'symbol', 'available_date']].isna().any().any() or keys.duplicated(['date', 'symbol']).any() or keys.date.gt(end).any() or keys.available_date.gt(keys.date).any():
        raise ValueError('unchanged causal eligible keys required')
    dates_by_symbol = {s: pd.DatetimeIndex(f.date) for s, f in keys.groupby('symbol', sort=False)}
    rows = pd.read_csv(membership/'memberships.csv')
    releases = pd.read_csv(membership/'catalog.csv')
    coverage = pd.read_csv(membership/'signal_coverage.csv', parse_dates=['date']).set_index('date')
    if coverage.index.has_duplicates or len(coverage) != keys.date.nunique() or len(keys) != memberships['eligible_rows']:
        raise ValueError('membership gate coverage differs from original signals')
    code_names = ['industry_candidate_dataset.py', 'technical_candidates.py', 'streaming_snapshot.py', 'archived_industry_research.py', 'archived_industry_inputs.py', 'factors.py']
    def code_hashes():
        return {n: hashlib.sha256((root/'ashare_mvp'/n).read_text(encoding='utf-8-sig').replace('\r\n', '\n').encode()).hexdigest() for n in code_names}
    initial_code = code_hashes()
    target.mkdir(parents=True)
    combined = FEATURES + MARKET_FEATURES + BOX_FEATURES
    output_definition = dict(schema='phase2_archived_industry_candidate_inputs_v1', protocol_sha256=seal['protocol_sha256'],
        research_end=str(end.date()), base_features=summary['features'], groups={'industry': FEATURES, 'combined': combined},
        preprocessing=dict(stock_features='same-date 1/99 winsor and population z-score; native NaN', market_common='original unscaled market state and volatility',
                           industry_membership='last published complete archived release, usable next exchange session; actual observations retained; historical original vintages uncertified'),
        source_hashes=sources, code_hashes=initial_code, independent_data_used=False, model_selection_performed=False)
    (target/'definition.json').write_text(json.dumps(output_definition, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    proof = dict(schema='phase2_archived_industry_candidate_cache_v1', status='building', started_at_utc=datetime.now(timezone.utc).isoformat(),
                 protocol_sha256=seal['protocol_sha256'], source_hashes=sources, code_hashes=initial_code,
                 issuers=0, bars=0, eligible_feature_rows=0, expected_feature_rows=len(keys), independent_data_used=False,
                 models_trained=0, model_selection_performed=False, historical_original_vintages_certified=False, certified=False)
    def save():
        (target/'manifest.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf8')
    save()
    try:
        context_file = target/'raw_industry_context.csv'; header = True
        for symbol, history in iter_seed_bars(data/'prices.csv', chunksize=100000):
            selected = research_industry_symbol_rows(history, states, calendar, dates_by_symbol.get(symbol, pd.DatetimeIndex([])), end)
            if not selected.empty:
                selected.to_csv(context_file, mode='a', header=header, index=False); header = False
            proof['issuers'] += 1; proof['bars'] += len(history); proof['eligible_feature_rows'] += len(selected)
            if proof['issuers'] % 250 == 0:
                save(); print(json.dumps({k: proof[k] for k in ['issuers', 'bars', 'eligible_feature_rows']}), flush=True)
        context = pd.read_csv(context_file, parse_dates=['date'], dtype={'symbol': str}, float_precision='round_trip')
        expected = pd.MultiIndex.from_frame(keys[['date', 'symbol']]); actual = pd.MultiIndex.from_frame(context[['date', 'symbol']])
        if len(actual) != len(expected) or not expected.difference(actual).empty or not actual.difference(expected).empty:
            raise ValueError('industry context lost or added original candidate keys')
        feature_parts = []; diagnostics = []
        for date, frame in context.groupby('date', sort=True):
            calculated = archived_research_industry_features(frame, rows, releases, calendar, date, end)
            known = int(calculated.industry_known.sum())
            if len(calculated) != len(frame) or known != int(coverage.loc[date, 'known']):
                raise ValueError('industry calculation differs from admitted key/availability coverage')
            feature_parts.append(calculated[['date', 'symbol'] + FEATURES])
            diagnostics.append(dict(date=str(date.date()), eligible=len(frame), known=known, publication_age_days=int(coverage.loc[date, 'publication_age_days']),
                                    **{name+'_missing': int(calculated[name].isna().sum()) for name in FEATURES}))
        raw = pd.concat(feature_parts, ignore_index=True)
        raw.to_csv(target/'raw_industry_features.csv', index=False)
        normalized = normalize_industry_snapshots(raw)
        technical_features = pd.read_csv(technical/'candidate_features.csv', parse_dates=['date'], dtype={'symbol': str}, float_precision='round_trip')
        merged = technical_features.merge(normalized, on=['date', 'symbol'], how='outer', validate='one_to_one', indicator=True)
        if len(merged) != len(keys) or not merged.pop('_merge').eq('both').all():
            raise ValueError('industry/technical feature universes differ')
        merged.sort_values(['date', 'symbol']).to_csv(target/'candidate_features.csv', index=False)
        pd.DataFrame(diagnostics).to_csv(target/'industry_coverage.csv', index=False)
        for name, digest in sources.items():
            if sha256(root/name) != digest:
                raise ValueError('industry candidate source changed during build')
        if code_hashes() != initial_code:
            raise ValueError('industry candidate code changed during build')
        proof.update(status='archived_industry_candidates_built', completed_at_utc=datetime.now(timezone.utc).isoformat(),
                     signals=len(diagnostics), missing_counts={name: int(raw[name].isna().sum()) for name in FEATURES},
                     maximum_publication_age_days=max(r['publication_age_days'] for r in diagnostics),
                     definition_sha256=sha256(target/'definition.json'), output_hashes={p.name: sha256(p) for p in target.iterdir() if p.name != 'manifest.json'})
        save(); return proof
    except BaseException as error:
        proof.update(status='failed', error=str(error)); save(); raise


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--project', default='.')
    for name in ['dataset', 'experiment', 'technical-cache', 'membership-inputs', 'seal-directory', 'out']:
        parser.add_argument('--'+name, required=True)
    result = build_industry_candidates(**vars(parser.parse_args()))
    print(json.dumps({k: result[k] for k in ['status', 'issuers', 'bars', 'eligible_feature_rows', 'signals', 'missing_counts']}), flush=True)


if __name__ == '__main__':
    main()

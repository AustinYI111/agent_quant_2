"""Fit the fixed industry/combined family; reuse the verified matched controls."""
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
import argparse, hashlib, json, platform
import numpy as np
import pandas as pd
import lightgbm
from .phase2_protocol import sha256, load_seal, day
from .rolling_candidates import validate_labels, fold_frames, key_hash
from .model import fit_ranker
from .archived_industry_research import FEATURES
from .technical_candidates import MARKET_FEATURES, BOX_FEATURES


def check_matched_fold(control, train, test, cutoff):
    expected = dict(train_rows=len(train), train_dates=int(train.date.nunique()), prediction_rows=len(test), prediction_dates=int(test.date.nunique()),
                    train_keys_sha256=key_hash(train), prediction_keys_sha256=key_hash(test), max_label_end=str(train.label_end.max().date()), cutoff=str(cutoff.date()))
    if any(control.get(k) != v for k, v in expected.items()):
        raise ValueError('industry fold differs from the original matched control')
    if not train.label_end.max() < cutoff < test.date.min():
        raise ValueError('strict mature-label embargo required')
    return expected


def train_industry_candidates(project, inputs, experiment, matched_training, seal_directory, out):
    root = Path(project).resolve()
    def local(value):
        p = Path(value)
        if p.is_absolute() or '..' in p.parts or ':' in str(p):
            raise ValueError('project-relative industry training paths required')
        result = (root/p).resolve()
        if not result.is_relative_to(root):
            raise ValueError('industry training path escapes project')
        return result
    cache, old, controls, sealed, target = map(local, [inputs, experiment, matched_training, seal_directory, out])
    if target.exists() or any(target.is_relative_to(p) or p.is_relative_to(target) for p in [cache, old, controls, sealed]):
        raise ValueError('fresh industry output outside immutable inputs required')
    protocol, seal = load_seal(sealed, root)
    m = json.loads((cache/'manifest.json').read_text(encoding='utf8'))
    definition = json.loads((cache/'definition.json').read_text(encoding='utf8'))
    reference = json.loads((controls/'manifest.json').read_text(encoding='utf8'))
    if m.get('schema') != 'phase2_archived_industry_candidate_cache_v1' or m.get('status') != 'archived_industry_candidates_built' or m.get('independent_data_used') is not False or m.get('protocol_sha256') != seal['protocol_sha256'] or definition.get('schema') != 'phase2_archived_industry_candidate_inputs_v1' or definition.get('protocol_sha256') != seal['protocol_sha256'] or definition.get('independent_data_used') is not False:
        raise ValueError('completed admitted industry research cache required')
    if reference.get('status') != 'candidate_fits_complete_selection_pending' or reference.get('independent_data_used') is not False or reference.get('model_selection_performed') is not False or reference.get('formal_model_promoted') is not False or reference.get('protocol_sha256') != seal['protocol_sha256'] or len(reference.get('models', [])) != 90:
        raise ValueError('verified original ninety-model matched family required')
    sources = {}
    def bind(path, expected=None):
        digest = sha256(path)
        if expected is not None and digest != expected:
            raise ValueError('industry training input changed: ' + path.name)
        sources[path.relative_to(root).as_posix()] = digest
    bind(cache/'manifest.json'); bind(cache/'definition.json', m['definition_sha256']); bind(controls/'manifest.json')
    for name, digest in m['source_hashes'].items():
        bind(local(name), digest)
    for name, digest in m['output_hashes'].items():
        bind(cache/name, digest)
    for contract in [m['code_hashes'], reference['code_hashes']]:
        for name, digest in contract.items():
            raw = (root/'ashare_mvp'/name).read_text(encoding='utf-8-sig').replace('\r\n', '\n').encode()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError('industry or matched-control construction code changed: ' + name)
    summary = json.loads((old/'models/summary.json').read_text(encoding='utf8'))
    bind(old/'models/summary.json')
    for name, key in [('factor_samples.csv', 'samples'), ('labels.csv', 'labels')]:
        bind(old/name, summary['input_frame_sha256'][key])
    declared = {g['id']: g['features'] for g in protocol['candidate_groups']}
    base = summary['features']; combined = FEATURES + MARKET_FEATURES + BOX_FEATURES
    if definition['base_features'] != base or definition['groups'] != {'industry': FEATURES, 'combined': combined} or declared['industry'] != FEATURES or declared['market_state'] != MARKET_FEATURES or declared['oneil_box'] != BOX_FEATURES:
        raise ValueError('industry/combined feature definitions differ from fixed protocol')
    groups = {'industry': base+FEATURES, 'combined': base+combined}
    dependencies = dict(lightgbm=lightgbm.__version__, numpy=np.__version__, pandas=pd.__version__, python=platform.python_version())
    baseline = [r for r in reference['models'] if r['group'] == 'baseline']
    expected_controls = {(h, trees, start) for h in protocol['horizons'] for trees in protocol['validation']['candidate_estimators'] for start, end in protocol['validation']['research_validation_windows']}
    lookup = {(r['horizon'], r['n_estimators'], r['validation_start']): r for r in baseline}
    if len(baseline) != 30 or set(lookup) != expected_controls:
        raise ValueError('complete thirty matched baseline controls required')
    control_meta = {}
    for key, r in lookup.items():
        model = controls/r['relative_model_path']
        for path, digest in [(model, r['model_sha256']), (model.with_name('metadata.json'), r['metadata_sha256']), (model.with_name('rankings.csv'), r['rankings_sha256'])]:
            bind(path, digest)
        meta = json.loads(model.with_name('metadata.json').read_text(encoding='utf8'))
        if meta['features'] != base or meta['dependencies'] != dependencies:
            raise ValueError('identical baseline feature and dependency environment required')
        control_meta[key] = meta
    calendar_paths = [p for p in m['source_hashes'] if p.endswith('/calendar.csv')]
    if len(calendar_paths) != 1:
        raise ValueError('one original research calendar required')
    calendar = pd.DatetimeIndex(pd.to_datetime(pd.read_csv(root/calendar_paths[0]).date))
    research_end = day(protocol['research_end'])
    if calendar.max() > research_end:
        raise ValueError('independent calendar cannot enter candidate selection')
    x = pd.read_csv(old/'factor_samples.csv', parse_dates=['date', 'available_date'], dtype={'symbol': 'category'}, float_precision='round_trip')
    extra = pd.read_csv(cache/'candidate_features.csv', parse_dates=['date'], dtype={'symbol': 'category'}, float_precision='round_trip')
    if x.duplicated(['date', 'symbol']).any() or extra.duplicated(['date', 'symbol']).any() or len(x) != len(extra):
        raise ValueError('complete original eligible feature keys required')
    x = x.merge(extra, on=['date', 'symbol'], how='outer', validate='one_to_one', indicator=True)
    if not x.pop('_merge').eq('both').all() or x[['date', 'symbol', 'available_date']].isna().any().any() or x.available_date.gt(x.date).any() or x.date.gt(research_end).any():
        raise ValueError('industry feature eligibility or availability differs from baseline')
    del extra
    for name in list(dict.fromkeys(sum(groups.values(), []))):
        if np.isinf(pd.to_numeric(x[name], errors='raise')).any():
            raise ValueError('infinite industry candidate feature')
    labels = validate_labels(pd.read_csv(old/'labels.csv', dtype={'symbol': 'category', 'horizon': 'category'}, float_precision='round_trip'), calendar, research_end)
    code_names = ['rolling_industry_candidates.py', 'rolling_candidates.py', 'model.py', 'horizon_labels.py', 'phase2_protocol.py']
    def code_hashes():
        return {n: hashlib.sha256((root/'ashare_mvp'/n).read_text(encoding='utf-8-sig').replace('\r\n', '\n').encode()).hexdigest() for n in code_names}
    initial_code = code_hashes(); target.mkdir(parents=True)
    proof = dict(schema='phase2_matched_industry_training_v1', status='training', started_at_utc=datetime.now(timezone.utc).isoformat(),
                 protocol_sha256=seal['protocol_sha256'], source_hashes=sources, code_hashes=initial_code, research_end=str(research_end.date()),
                 independent_data_used=False, model_selection_performed=False, formal_model_promoted=False, certified=False, groups=groups, models=[],
                 reused_control_models=30, maximum_publication_age_days=m['maximum_publication_age_days'], historical_original_vintages_certified=False,
                 selection_status='pending complete fixed-family net-cost, timing and equal-weight comparisons; no automatic promotion')
    def save():
        (target/'manifest.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf8')
    save()
    try:
        for horizon in protocol['horizons']:
            y = labels[labels.horizon.eq(horizon)]
            frame = x[x.date.isin(y.date)].merge(y[['date', 'symbol', 'target', 'label_end']], on=['date', 'symbol'], how='left', validate='one_to_one')
            for start, end in protocol['validation']['research_validation_windows']:
                train, test, cutoff = fold_frames(frame, calendar, start, end, protocol['validation'])
                for trees in protocol['validation']['candidate_estimators']:
                    reference_record = lookup[(horizon, trees, start)]
                    matched = check_matched_fold(reference_record, train, test, cutoff)
                    reference_metadata = control_meta[(horizon, trees, start)]
                    for group, ordered in groups.items():
                        model = fit_ranker(train, ordered, SimpleNamespace(n_estimators=trees, seed=protocol['validation']['seed']))
                        if model.get_params() != reference_metadata['hyperparameters']:
                            raise ValueError('candidate hyperparameters differ from matched control')
                        folder = target/horizon/start.replace('-', '')/str(trees)/group; folder.mkdir(parents=True)
                        model.booster_.save_model(str(folder/'model.txt'))
                        scores = test[['date', 'symbol', 'available_date']].copy(); scores['score'] = model.predict(test[ordered])
                        restored = lightgbm.Booster(model_file=str(folder/'model.txt'))
                        if not np.isfinite(scores.score).all() or not np.array_equal(restored.predict(test[ordered], num_threads=1), scores.score.to_numpy()):
                            raise ValueError('industry candidate reload scores differ')
                        scores = scores.sort_values(['date', 'score', 'symbol'], ascending=[True, False, True])
                        scores['rank'] = scores.groupby('date', observed=True).cumcount()+1
                        scores['selected'] = scores['rank'].le(protocol['execution']['top_k']); scores['horizon'] = horizon
                        scores.to_csv(folder/'rankings.csv', index=False)
                        meta = dict(schema='phase2_candidate_ranker_v1', horizon=horizon, group=group, features=ordered, n_estimators=trees,
                                    validation_start=start, validation_end=end, **matched,
                                    min_feature_date=str(train.date.min().date()), max_feature_date=str(train.date.max().date()),
                                    missing_feature_values={c: int(train[c].isna().sum()) for c in ordered}, trained_at_utc=datetime.now(timezone.utc).isoformat(),
                                    protocol_sha256=seal['protocol_sha256'], input_manifest_sha256=sha256(cache/'manifest.json'), source_hashes=sources, code_hashes=initial_code,
                                    model_sha256=sha256(folder/'model.txt'), rankings_sha256=sha256(folder/'rankings.csv'), reload_max_abs_error=0.,
                                    matched_control_model_sha256=reference_record['model_sha256'], hyperparameters=model.get_params(), preprocessing=definition['preprocessing'],
                                    dependencies=dependencies, independent_data_used=False, model_selection_performed=False, formal_model=False, certified=False,
                                    historical_original_vintages_certified=False, maximum_publication_age_days=m['maximum_publication_age_days'])
                        (folder/'metadata.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf8')
                        record = {k: meta[k] for k in ['horizon', 'group', 'n_estimators', 'validation_start', 'validation_end', 'train_rows', 'train_dates', 'prediction_rows', 'prediction_dates', 'train_keys_sha256', 'prediction_keys_sha256', 'max_label_end', 'cutoff', 'model_sha256', 'rankings_sha256', 'reload_max_abs_error', 'matched_control_model_sha256']}
                        record.update(metadata_sha256=sha256(folder/'metadata.json'), relative_model_path=(folder/'model.txt').relative_to(target).as_posix())
                        proof['models'].append(record); save(); print(json.dumps(dict(models=len(proof['models']), horizon=horizon, group=group, trees=trees, start=start)), flush=True)
                        del model, restored, scores
        for name, digest in sources.items():
            if sha256(root/name) != digest:
                raise ValueError('industry training input changed during fitting')
        if code_hashes() != initial_code:
            raise ValueError('industry training code changed during fitting')
        if len(proof['models']) != 60:
            raise ValueError('complete sixty-model industry/combined family required')
        proof.update(status='candidate_fits_complete_selection_pending', completed_at_utc=datetime.now(timezone.utc).isoformat()); save(); return proof
    except BaseException as error:
        proof.update(status='failed', error=str(error)); save(); raise


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--project', default='.')
    for name in ['inputs', 'experiment', 'matched-training', 'seal-directory', 'out']:
        parser.add_argument('--'+name, required=True)
    result = train_industry_candidates(**vars(parser.parse_args()))
    print(json.dumps(dict(status=result['status'], models=len(result['models']))), flush=True)


if __name__ == '__main__':
    main()

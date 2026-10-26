"""Independently audit the complete industry family without fitting any model."""
from pathlib import Path
from datetime import datetime, timezone
import argparse, hashlib, json, platform
import numpy as np
import pandas as pd
import lightgbm
from ashare_mvp.phase2_protocol import sha256, load_seal
from ashare_mvp.rolling_candidates import validate_labels, fold_frames, key_hash


def verify(project, out):
    root = Path(project).resolve()
    destination = Path(out)
    if destination.is_absolute() or '..' in destination.parts:
        raise ValueError('project-relative fresh report required')
    destination = root / destination
    if destination.exists():
        raise ValueError('verification report already exists')
    family = root/'artifacts/phase2/matched_industry_models_20261023_v1'
    controls = root/'artifacts/phase2/matched_candidate_models_20261022_v2'
    cache = root/'artifacts/phase2/research_industry_inputs_20261023_v2'
    old = root/'artifacts/three_horizon_real_causal_quality1'
    m = json.loads((family/'manifest.json').read_text(encoding='utf8'))
    baseline = json.loads((controls/'manifest.json').read_text(encoding='utf8'))
    protocol, seal = load_seal(root/'artifacts/phase2/protocol_20261020', root)
    assert m['status'] == 'candidate_fits_complete_selection_pending'
    assert len(m['models']) == 60 and m['reused_control_models'] == 30
    assert m['protocol_sha256'] == seal['protocol_sha256']
    assert not any(m[k] for k in ['independent_data_used','model_selection_performed','formal_model_promoted'])
    initial_manifest_sha = sha256(family/'manifest.json')
    for name, digest in m['source_hashes'].items():
        assert sha256(root/name) == digest, name
    for name, digest in m['code_hashes'].items():
        assert hashlib.sha256((root/'ashare_mvp'/name).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest() == digest
    lookup = {(r['horizon'],r['n_estimators'],r['validation_start']):r for r in baseline['models'] if r['group']=='baseline'}
    expected = {(h,t,s,g) for h in protocol['horizons'] for t in protocol['validation']['candidate_estimators'] for s,e in protocol['validation']['research_validation_windows'] for g in ['industry','combined']}
    assert len(lookup) == 30
    assert {(r['horizon'],r['n_estimators'],r['validation_start'],r['group']) for r in m['models']} == expected
    calendar = pd.DatetimeIndex(pd.to_datetime(pd.read_csv(root/'artifacts/market_ratios_causal_quality1/calendar.csv').date))
    assert calendar.max() <= pd.Timestamp('2025-12-31')
    x = pd.read_csv(old/'factor_samples.csv',parse_dates=['date','available_date'],dtype={'symbol':str},float_precision='round_trip')
    extra = pd.read_csv(cache/'candidate_features.csv',parse_dates=['date'],dtype={'symbol':str},float_precision='round_trip')
    x = x.merge(extra,on=['date','symbol'],validate='one_to_one',how='outer',indicator=True)
    assert x.pop('_merge').eq('both').all()
    del extra
    labels = validate_labels(pd.read_csv(old/'labels.csv',dtype={'symbol':str},float_precision='round_trip',low_memory=False),calendar,pd.Timestamp('2025-12-31'))
    deps = dict(lightgbm=lightgbm.__version__,numpy=np.__version__,pandas=pd.__version__,python=platform.python_version())
    checks=[]
    fields=['train_rows','train_dates','prediction_rows','prediction_dates','train_keys_sha256','prediction_keys_sha256','max_label_end','cutoff']
    for h in protocol['horizons']:
        y=labels[labels.horizon.eq(h)]
        frame=x[x.date.isin(y.date)].merge(y[['date','symbol','target','label_end']],on=['date','symbol'],how='left',validate='one_to_one')
        for start,end in protocol['validation']['research_validation_windows']:
            train,test,cutoff=fold_frames(frame,calendar,start,end,protocol['validation'])
            actual=dict(train_rows=len(train),train_dates=int(train.date.nunique()),prediction_rows=len(test),prediction_dates=int(test.date.nunique()),train_keys_sha256=key_hash(train),prediction_keys_sha256=key_hash(test),max_label_end=str(train.label_end.max().date()),cutoff=str(cutoff.date()))
            assert train.label_end.max()<cutoff<test.date.min()
            sample=test.iloc[np.unique(np.linspace(0,len(test)-1,20,dtype=int))]
            for r in [r for r in m['models'] if r['horizon']==h and r['validation_start']==start]:
                control=lookup[(h,r['n_estimators'],start)]
                p=family/r['relative_model_path']
                for file,digest in [(p,r['model_sha256']),(p.with_name('metadata.json'),r['metadata_sha256']),(p.with_name('rankings.csv'),r['rankings_sha256'])]:
                    assert sha256(file)==digest
                meta=json.loads(p.with_name('metadata.json').read_text(encoding='utf8'))
                control_path=controls/control['relative_model_path']
                assert sha256(control_path)==control['model_sha256']
                assert sha256(control_path.with_name('metadata.json'))==control['metadata_sha256']
                control_meta=json.loads(control_path.with_name('metadata.json').read_text(encoding='utf8'))
                assert all(r[k]==control[k]==actual[k]==meta[k] for k in fields)
                assert meta['dependencies']==deps==control_meta['dependencies']
                assert meta['hyperparameters']==control_meta['hyperparameters']
                assert meta['features']==m['groups'][r['group']]
                assert meta['matched_control_model_sha256']==control['model_sha256']
                assert not any(meta[k] for k in ['independent_data_used','model_selection_performed','formal_model'])
                ranks=pd.read_csv(p.with_name('rankings.csv'),parse_dates=['date','available_date'],dtype={'symbol':str},float_precision='round_trip')
                assert len(ranks)==len(test) and not ranks.duplicated(['date','symbol']).any()
                assert key_hash(ranks.sort_values(['date','symbol']))==actual['prediction_keys_sha256']
                assert ranks.horizon.eq(h).all() and ranks.available_date.le(ranks.date).all()
                sorted_ranks=ranks.sort_values(['date','score','symbol'],ascending=[True,False,True])
                assert sorted_ranks.index.equals(ranks.index)
                np.testing.assert_array_equal(ranks['rank'],ranks.groupby('date').cumcount()+1)
                assert ranks.selected.eq(ranks['rank'].le(protocol['execution']['top_k'])).all()
                booster=lightgbm.Booster(model_file=str(p))
                assert booster.feature_name()==meta['features']
                predicted=booster.predict(sample[meta['features']],num_threads=1)
                saved=ranks.set_index(['date','symbol']).loc[pd.MultiIndex.from_frame(sample[['date','symbol']]),'score'].to_numpy()
                np.testing.assert_array_equal(predicted,saved)
                checks.append(dict(horizon=h,group=r['group'],trees=r['n_estimators'],start=start,reloaded_scores=len(sample),max_absolute_error=0.,prediction_rows=len(ranks)))
                print(json.dumps(dict(verified_models=len(checks),horizon=h,group=r['group'],start=start)),flush=True)
    assert sha256(family/'manifest.json')==initial_manifest_sha
    result=dict(schema='independent_industry_training_verification_v1',verified_at_utc=datetime.now(timezone.utc).isoformat(),status='sixty_models_verified_selection_pending',training_manifest_sha256=initial_manifest_sha,models=60,reused_matched_controls=30,independently_reloaded_scores=sum(c['reloaded_scores'] for c in checks),reload_max_absolute_error=0.,all_rankings_and_ordered_fold_keys_verified=True,strict_label_embargo_verified=True,independent_data_used=False,model_selection_performed=False,formal_model_promoted=False,checks=checks)
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',default='.')
    parser.add_argument('--out',required=True)
    result=verify(**vars(parser.parse_args()))
    print(json.dumps({k:v for k,v in result.items() if k!='checks'}))

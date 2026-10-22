"""Fixed research folds for matched baseline and prespecified candidate rankers.

Fits every declared tree count; never selects a winner from Rank IC or 2026.
Cost-aware paired portfolio evaluation is a separate required selection stage.
"""
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
import argparse
import hashlib
import json
import platform

import lightgbm
import numpy as np
import pandas as pd
from .phase2_protocol import load_seal, sha256, day
from .horizon_labels import HORIZONS, label_interval
from .model import training_slice, fit_ranker


def validate_labels(labels, calendar, research_end):
    required={'date','symbol','horizon','entry_date','label_end','target','executable_exit','horizon_unit','horizon_length'}
    if not required.issubset(labels):
        raise ValueError('explicit complete horizon labels required')
    y=labels.copy()
    for field in ['date','entry_date','label_end']:
        y[field]=pd.to_datetime(y[field])
        dates=pd.DatetimeIndex(y[field].dropna())
        if dates.tz is not None or not dates.equals(dates.normalize()):
            raise ValueError('normalized label dates required')
    if y[['date','symbol','horizon']].isna().any().any() or y.duplicated(['date','symbol','horizon']).any():
        raise ValueError('unique nonmissing label identities required')
    if not y.horizon.isin(HORIZONS).all() or not y.date.isin(calendar).all() or y.date.gt(day(research_end)).any():
        raise ValueError('research-only known horizon sessions required')
    y['target']=pd.to_numeric(y.target,errors='raise')
    if np.isinf(y.target).any() or (y.target.notna() & (~y.executable_exit.eq(True) | y.label_end.isna())).any():
        raise ValueError('finite executable matured targets required')
    if y.label_end.dropna().gt(day(research_end)).any():
        raise ValueError('independent outcome cannot enter research')
    for (date,key),group in y.groupby(['date','horizon'],sort=False,observed=True):
        horizon=HORIZONS[key]
        entry,end=label_interval(calendar,date,horizon)
        for column,expected in [('entry_date',entry),('label_end',end)]:
            valid=group[column].eq(expected) | (group[column].isna() & pd.isna(expected))
            if not valid.all():
                raise ValueError('label interval differs from declared horizon')
        if not group.horizon_unit.eq(horizon.unit).all() or not group.horizon_length.eq(horizon.length).all():
            raise ValueError('label unit differs from declared horizon')
    return y


def fold_frames(frame, calendar, start, end, validation):
    """Purge by actual target end before the embargo; keep unknown test rows."""
    start,end=day(start),day(end)
    if start not in calendar or end not in calendar or end<start:
        raise ValueError('fold bounds must be ordered research sessions')
    config=SimpleNamespace(train_months=validation['train_months'],
        embargo_sessions=validation['embargo_sessions'],min_group_size=20)
    train,cutoff=training_slice(frame,start,calendar,config)
    test=frame.loc[frame.date.between(start,end)].copy().sort_values(['date','symbol'])
    if train.date.nunique()<validation['min_train_dates'] or test.empty:
        raise ValueError('insufficient mature training groups or empty validation fold')
    if train.label_end.ge(cutoff).any() or train.date.ge(start).any() or train.available_date.gt(train.date).any():
        raise ValueError('rolling training leakage')
    return train,test,cutoff


def key_hash(frame):
    return hashlib.sha256(frame[['date','symbol']].to_csv(index=False).encode()).hexdigest()


def train_candidates(project, inputs, experiment, seal_directory, out):
    root=Path(project).resolve()
    def local(value):
        p=Path(value)
        if p.is_absolute() or '..' in p.parts or ':' in str(p):
            raise ValueError('project-relative training paths required')
        result=(root/p).resolve()
        if not result.is_relative_to(root):raise ValueError('training path escapes project')
        return result
    cache,old,sealed,target=map(local,[inputs,experiment,seal_directory,out])
    if target.exists():raise ValueError('new candidate training output required')
    if any(target.is_relative_to(p) or p.is_relative_to(target) for p in [cache,old,sealed]):
        raise ValueError('candidate training output overlaps immutable inputs')
    protocol,seal=load_seal(sealed,root)
    manifest=json.loads((cache/'manifest.json').read_text(encoding='utf8'))
    definition=json.loads((cache/'definition.json').read_text(encoding='utf8'))
    if manifest.get('schema')!='phase2_research_candidate_cache_v1' or definition.get('schema')!='phase2_candidate_inputs_v1' or manifest.get('status')!='research_candidates_built' or manifest.get('independent_data_used') is not False or definition.get('protocol_sha256')!=seal['protocol_sha256'] or manifest.get('protocol_sha256')!=seal['protocol_sha256']:
        raise ValueError('completed sealed research-only candidate inputs required')
    if set(definition.get('code_hashes',{}))!={'candidate_dataset.py','technical_candidates.py','streaming_snapshot.py','factors.py'}:
        raise ValueError('complete candidate calculation code contract required')
    if sha256(cache/'definition.json')!=manifest['definition_sha256']:
        raise ValueError('candidate definition changed')
    sources={}
    for name,digest in manifest['source_hashes'].items():
        if sha256(local(name))!=digest:raise ValueError('candidate original source changed')
        sources[name]=digest
    for name,digest in manifest['output_hashes'].items():
        if sha256(cache/name)!=digest:raise ValueError('candidate cache changed')
        sources[(cache/name).relative_to(root).as_posix()]=digest
    for p in [cache/'manifest.json',cache/'definition.json']:
        sources[p.relative_to(root).as_posix()]=sha256(p)
    for name,digest in definition['code_hashes'].items():
        raw=(root/'ashare_mvp'/name).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('candidate calculation code changed')
    model_summary=json.loads((old/'models/summary.json').read_text(encoding='utf8'))
    for name,key in [('factor_samples.csv','samples'),('labels.csv','labels')]:
        p=old/name
        if sources.get(p.relative_to(root).as_posix())!=model_summary['input_frame_sha256'][key]:
            raise ValueError('candidate training experiment differs from cache')
    dataset_calendar=[name for name in manifest['source_hashes'] if name.endswith('/calendar.csv')]
    if len(dataset_calendar)!=1:raise ValueError('one research calendar required')
    calendar=pd.DatetimeIndex(pd.to_datetime(pd.read_csv(root/dataset_calendar[0]).date))
    research_end=day(protocol['research_end'])
    if calendar.empty or calendar.has_duplicates or not calendar.is_monotonic_increasing or calendar.max()>research_end:
        raise ValueError('research calendar includes unknown or independent sessions')
    base=definition['base_features']
    declared={g['id']:g['features'] for g in protocol['candidate_groups']}
    if base!=model_summary['features'] or any(definition['groups'].get(g)!=declared[g] for g in ['market_state','oneil_box']):
        raise ValueError('training features differ from frozen definitions')
    groups={'baseline':base,'market_state':base+declared['market_state'],'oneil_box':base+declared['oneil_box']}
    features=list(dict.fromkeys(sum(groups.values(),[])))
    x=pd.read_csv(old/'factor_samples.csv',parse_dates=['date','available_date'],dtype={'symbol':'category'},float_precision='round_trip')
    extra=pd.read_csv(cache/'candidate_features.csv',parse_dates=['date'],dtype={'symbol':'category'},float_precision='round_trip')
    if x.duplicated(['date','symbol']).any() or extra.duplicated(['date','symbol']).any() or len(x)!=len(extra):
        raise ValueError('candidate/original keys ambiguous or incomplete')
    x=x.merge(extra,on=['date','symbol'],how='outer',validate='one_to_one',indicator=True)
    if not x.pop('_merge').eq('both').all() or x[['date','symbol','available_date']].isna().any().any() or x.available_date.gt(x.date).any() or x.date.gt(research_end).any():
        raise ValueError('candidate/original eligibility or availability differs')
    del extra
    if any(np.isinf(pd.to_numeric(x[c],errors='raise')).any() for c in features):
        raise ValueError('infinite candidate training feature')
    labels=validate_labels(pd.read_csv(old/'labels.csv',dtype={'symbol':'category','horizon':'category'},float_precision='round_trip'),calendar,research_end)
    code_names=['rolling_candidates.py','model.py','horizon_labels.py','phase2_protocol.py']
    code_hashes={n:hashlib.sha256((root/'ashare_mvp'/n).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest() for n in code_names}
    target.mkdir(parents=True)
    proof=dict(schema='phase2_matched_candidate_training_v1',status='training',started_at_utc=datetime.now(timezone.utc).isoformat(),
        protocol_sha256=seal['protocol_sha256'],source_hashes=sources,code_hashes=code_hashes,
        research_end=str(research_end.date()),independent_data_used=False,model_selection_performed=False,
        formal_model_promoted=False,certified=False,groups=groups,models=[],
        pending_groups=definition['pending_groups'],selection_status='pending paired net-cost portfolio evaluation; Rank IC is diagnostic only')
    def save():
        (target/'manifest.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
    save()
    try:
        for key in protocol['horizons']:
            hlabels=labels.loc[labels.horizon.eq(key)]
            source=x.loc[x.date.isin(hlabels.date)]
            frame=source.merge(hlabels[['date','symbol','target','label_end']],on=['date','symbol'],how='left',validate='one_to_one')
            for start,end in protocol['validation']['research_validation_windows']:
                train,test,cutoff=fold_frames(frame,calendar,start,end,protocol['validation'])
                train_digest=key_hash(train);test_digest=key_hash(test)
                for trees in protocol['validation']['candidate_estimators']:
                    for group,ordered in groups.items():
                        model=fit_ranker(train,ordered,SimpleNamespace(n_estimators=trees,seed=protocol['validation']['seed']))
                        bundle=target/key/start.replace('-','')/str(trees)/group
                        bundle.mkdir(parents=True)
                        model.booster_.save_model(str(bundle/'model.txt'))
                        scores=test[['date','symbol','available_date']].copy()
                        scores['score']=model.predict(test[ordered])
                        if not np.isfinite(scores.score).all():raise ValueError('nonfinite research prediction')
                        restored=lightgbm.Booster(model_file=str(bundle/'model.txt'))
                        error=float(np.max(np.abs(restored.predict(test[ordered])-scores.score.to_numpy())))
                        if error!=0:raise ValueError('candidate reload prediction differs')
                        scores=scores.sort_values(['date','score','symbol'],ascending=[True,False,True])
                        scores['rank']=scores.groupby('date',observed=True).cumcount()+1
                        scores['selected']=scores['rank'].le(protocol['execution']['top_k'])
                        scores['horizon']=key
                        scores.to_csv(bundle/'rankings.csv',index=False)
                        meta=dict(schema='phase2_candidate_ranker_v1',horizon=key,group=group,features=ordered,n_estimators=trees,
                            validation_start=start,validation_end=end,cutoff=str(cutoff.date()),max_label_end=str(train.label_end.max().date()),
                            min_feature_date=str(train.date.min().date()),max_feature_date=str(train.date.max().date()),
                            train_rows=len(train),train_dates=int(train.date.nunique()),prediction_rows=len(test),prediction_dates=int(test.date.nunique()),
                            train_keys_sha256=train_digest,prediction_keys_sha256=test_digest,missing_feature_values={c:int(train[c].isna().sum()) for c in ordered},
                            trained_at_utc=datetime.now(timezone.utc).isoformat(),protocol_sha256=seal['protocol_sha256'],
                            input_manifest_sha256=sources[(cache/'manifest.json').relative_to(root).as_posix()],source_hashes=sources,code_hashes=code_hashes,
                            model_sha256=sha256(bundle/'model.txt'),rankings_sha256=sha256(bundle/'rankings.csv'),reload_max_abs_error=error,
                            hyperparameters=model.get_params(),preprocessing=definition['preprocessing'],
                            dependencies=dict(lightgbm=lightgbm.__version__,numpy=np.__version__,pandas=pd.__version__,python=platform.python_version()),
                            independent_data_used=False,model_selection_performed=False,formal_model=False,certified=False)
                        (bundle/'metadata.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
                        record={k:meta[k] for k in ['horizon','group','n_estimators','validation_start','validation_end','train_rows','train_dates','prediction_rows','prediction_dates','train_keys_sha256','prediction_keys_sha256','max_label_end','cutoff','model_sha256','rankings_sha256','reload_max_abs_error']}
                        record['metadata_sha256']=sha256(bundle/'metadata.json')
                        record['relative_model_path']=(bundle/'model.txt').relative_to(target).as_posix()
                        proof['models'].append(record);save()
                        print(json.dumps(dict(models=len(proof['models']),**record)),flush=True)
                        del restored,model,scores
                del train,test
            del frame,source,hlabels
        for name,digest in sources.items():
            if sha256(root/name)!=digest:raise ValueError('training input changed during fit')
        for name,digest in code_hashes.items():
            if hashlib.sha256((root/'ashare_mvp'/name).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest()!=digest:
                raise ValueError('training code changed during fit')
        proof.update(status='candidate_fits_complete_selection_pending',completed_at_utc=datetime.now(timezone.utc).isoformat())
        save();return proof
    except BaseException as error:
        proof.update(status='failed',error=str(error));save();raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',default='.')
    for name in ['inputs','experiment','seal-directory','out']:p.add_argument('--'+name,required=True)
    train_candidates(**vars(p.parse_args()))


if __name__=='__main__':main()

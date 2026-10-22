import numpy as np
import pandas as pd
import pytest
from types import SimpleNamespace
from ashare_mvp.rolling_candidates import fold_frames, validate_labels, key_hash
from ashare_mvp.horizon_labels import HORIZONS, label_interval
from ashare_mvp.model import fit_ranker


def fold_input():
    cal=pd.bdate_range('2018-01-01','2022-12-30')
    dates=cal[::20]
    rows=[]
    for date in dates:
        for number in range(20):
            rows.append(dict(date=date,symbol=f'{number:06d}',available_date=date,
                label_end=cal[min(len(cal)-1,cal.get_loc(date)+21)],target=number/100,
                original=float(number),candidate=np.nan if number==0 else float(number*2)))
    return cal,pd.DataFrame(rows)


def test_purge_by_label_end_and_embargo_with_unknown_prediction_rows_preserved():
    cal,frame=fold_input();start=pd.Timestamp('2021-07-30');end=pd.Timestamp('2022-06-30')
    frame.loc[frame.date.ge(start),'target']=np.nan
    config=dict(train_months=60,embargo_sessions=1,min_train_dates=12)
    train,test,cutoff=fold_frames(frame,cal,start,end,config)
    assert cutoff==cal[cal.get_loc(start)-1]
    assert train.label_end.lt(cutoff).all() and train.date.lt(start).all()
    assert test.target.isna().all() and len(test)==len(frame.loc[frame.date.between(start,end)])
    assert train.candidate.isna().any() and test.candidate.isna().any()
    altered=frame.copy();altered.loc[altered.date.ge(start),'target']=100000
    same,_,_=fold_frames(altered,cal,start,end,config)
    pd.testing.assert_frame_equal(train,same)
    assert key_hash(train)==key_hash(same)


def test_retrain_baseline_and_candidate_use_identical_queries_and_native_missing(tmp_path):
    import lightgbm
    cal,frame=fold_input()
    train,test,_=fold_frames(frame,cal,'2021-07-30','2022-06-30',dict(train_months=60,embargo_sessions=1,min_train_dates=12))
    original=key_hash(train)
    for features in [['original'],['original','candidate']]:
        model=fit_ranker(train,features,SimpleNamespace(n_estimators=3,seed=42))
        path=tmp_path/('model'+str(len(features))+'.txt');model.booster_.save_model(str(path))
        np.testing.assert_array_equal(model.predict(test[features]),lightgbm.Booster(model_file=str(path)).predict(test[features]))
        assert key_hash(train)==original


def labels():
    cal=pd.bdate_range('2020-01-01','2022-12-30');rows=[]
    for key,horizon in HORIZONS.items():
        date=cal[30];entry,end=label_interval(cal,date,horizon)
        rows.append(dict(date=date,symbol='sh.600000',horizon=key,entry_date=entry,label_end=end,
            target=.1,executable_exit=True,horizon_unit=horizon.unit,horizon_length=horizon.length))
    return cal,pd.DataFrame(rows)


def test_horizon_contract_and_unknown_labels_not_zeroed():
    cal,y=labels();y.loc[0,['target','executable_exit']]=[np.nan,False]
    valid=validate_labels(y,cal,cal[-1])
    assert pd.isna(valid.loc[0,'target'])


@pytest.mark.parametrize('fault',['entry','end','unit','exit','duplicate','independent','infinite','missing_end'])
def test_bad_or_future_labels_fail_before_training(fault):
    cal,y=labels()
    if fault=='entry':y.loc[0,'entry_date']+=pd.Timedelta(days=1)
    elif fault=='end':y.loc[0,'label_end']+=pd.Timedelta(days=1)
    elif fault=='unit':y.loc[0,'horizon_unit']='calendar_months'
    elif fault=='exit':y.loc[0,'executable_exit']=False
    elif fault=='duplicate':y=pd.concat([y,y.iloc[:1]],ignore_index=True)
    elif fault=='independent':y.loc[0,'label_end']=pd.Timestamp('2026-01-01')
    elif fault=='infinite':y.loc[0,'target']=np.inf
    elif fault=='missing_end':y.loc[0,'label_end']=pd.NaT
    with pytest.raises(ValueError):validate_labels(y,cal,cal[-1])


def test_training_orchestration_persists_matched_research_models_without_selection(tmp_path,monkeypatch):
    import hashlib,json,shutil
    from pathlib import Path
    from ashare_mvp import rolling_candidates as module
    from ashare_mvp.technical_candidates import BOX_FEATURES,MARKET_FEATURES
    from ashare_mvp.phase2_protocol import sha256
    cal,frame=fold_input()
    old=tmp_path/'experiment';cache=tmp_path/'cache';dataset=tmp_path/'dataset';code=tmp_path/'ashare_mvp'
    for p in [old/'models',cache,dataset,code]:p.mkdir(parents=True)
    names=['candidate_dataset.py','technical_candidates.py','streaming_snapshot.py','factors.py','rolling_candidates.py','model.py','horizon_labels.py','phase2_protocol.py']
    for name in names:shutil.copyfile(Path(module.__file__).with_name(name),code/name)
    def digest(name):return hashlib.sha256((code/name).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest()
    samples=frame[['date','symbol','available_date','original']]
    samples.to_csv(old/'factor_samples.csv',index=False)
    ys=[]
    for row in frame.itertuples():
        entry,end=label_interval(cal,row.date,HORIZONS['5d'])
        ys.append(dict(date=row.date,symbol=row.symbol,horizon='5d',entry_date=entry,label_end=end,
            target=row.target if pd.notna(end) else np.nan,executable_exit=pd.notna(end),horizon_unit='trading_sessions',horizon_length=5))
    pd.DataFrame(ys).to_csv(old/'labels.csv',index=False)
    pd.DataFrame({'date':cal}).to_csv(dataset/'calendar.csv',index=False)
    extra=frame[['date','symbol']].copy()
    for feature in BOX_FEATURES+MARKET_FEATURES:extra[feature]=frame.candidate
    extra['index_above_ma200']=1.;extra['index_volatility_63']=.02
    extra.to_csv(cache/'candidate_features.csv',index=False)
    summary=dict(features=['original'],input_frame_sha256=dict(samples=sha256(old/'factor_samples.csv'),labels=sha256(old/'labels.csv')))
    (old/'models/summary.json').write_text(json.dumps(summary),encoding='utf8')
    definition=dict(schema='phase2_candidate_inputs_v1',protocol_sha256='sealed-test',base_features=['original'],
        groups=dict(market_state=MARKET_FEATURES,oneil_box=BOX_FEATURES),preprocessing={'missing':'native NaN'},
        pending_groups={'industry':'no historical membership','combined':'pending industry'},
        code_hashes={name:digest(name) for name in names[:4]})
    (cache/'definition.json').write_text(json.dumps(definition),encoding='utf8')
    sources={p.relative_to(tmp_path).as_posix():sha256(p) for p in [dataset/'calendar.csv',old/'factor_samples.csv',old/'labels.csv']}
    manifest=dict(schema='phase2_research_candidate_cache_v1',status='research_candidates_built',independent_data_used=False,
        protocol_sha256='sealed-test',definition_sha256=sha256(cache/'definition.json'),source_hashes=sources,
        output_hashes={'candidate_features.csv':sha256(cache/'candidate_features.csv')})
    (cache/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')
    protocol=dict(research_end='2022-12-30',horizons=['5d'],candidate_groups=[dict(id=k,features=v) for k,v in definition['groups'].items()],
        validation=dict(train_months=60,min_train_dates=12,embargo_sessions=1,seed=42,candidate_estimators=[2],
            research_validation_windows=[['2021-07-30','2022-06-30']]),execution=dict(top_k=20))
    monkeypatch.setattr(module,'load_seal',lambda *args:(protocol,dict(protocol_sha256='sealed-test')))
    result=module.train_candidates(tmp_path,'cache','experiment','seal','fitted')
    assert result['status']=='candidate_fits_complete_selection_pending'
    assert len(result['models'])==3 and result['independent_data_used'] is False
    assert result['model_selection_performed'] is False and result['formal_model_promoted'] is False
    assert len({r['train_keys_sha256'] for r in result['models']})==1
    assert len({r['prediction_keys_sha256'] for r in result['models']})==1
    for record in result['models']:
        assert pd.Timestamp(record['max_label_end'])<pd.Timestamp(record['cutoff'])
        assert record['reload_max_abs_error']==0
    with pytest.raises(ValueError,match='new candidate training output'):
        module.train_candidates(tmp_path,'cache','experiment','seal','fitted')

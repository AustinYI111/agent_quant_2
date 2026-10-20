import hashlib,json,platform
from dataclasses import replace
from importlib.metadata import version
import numpy as np
import pandas as pd
import pytest
from ashare_mvp.demo import make_demo
from ashare_mvp.factors import default_library,technical_panel
from ashare_mvp.public_conversion import public_snapshot,load_public_data
from ashare_mvp.horizon_labels import HORIZONS
from ashare_mvp.model import fit_ranker
from ashare_mvp.research_provenance import bind_dataset_policy
from ashare_mvp.horizon_inference import export_bundle,check_bundle,predict_data


@pytest.fixture
def portable(tmp_path):
    from dataclasses import asdict
    from types import SimpleNamespace
    data=make_demo(stocks=24,periods=320)
    names={name:f'sh.{600000+i}' for i,name in enumerate(sorted(data.prices.symbol.unique()))}
    data.prices['symbol']=data.prices.symbol.map(names)
    data.fundamentals['symbol']=data.fundamentals.symbol.map(names)
    data.prices['quality_segment']=1
    data.prices['trading_symbol']=data.prices.symbol
    data.prices.loc[data.prices.symbol.eq('sh.600000') & data.prices.date.ge(data.calendar[309]),'trading_symbol']='sh.601234'
    data.metadata.update(schema='public_ratios_v1',certified=False,price_basis='causal_segment_feature_index',
        quality_policy={'schema':'causal_quality_research_v1'},security_identity_policy={'sha256':'fixture-policy'})
    folder=tmp_path/'data';folder.mkdir()
    for name,frame in [('prices.csv',data.prices),('fundamentals.csv',data.fundamentals),('calendar.csv',pd.DataFrame({'date':data.calendar}))]:frame.to_csv(folder/name,index=False)
    data.metadata['converted_hashes']={name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in ['prices.csv','fundamentals.csv','calendar.csv']}
    (folder/'metadata.json').write_text(json.dumps(data.metadata),encoding='utf8')
    technical=technical_panel(data);rows=[];features=['roe','momentum_63']
    for date in data.calendar[[280,285,290]]:
        values=default_library().compute(public_snapshot(data,technical,date))[features]
        values['target']=np.arange(len(values))/100;values['date']=date
        rows.append(values.rename_axis('symbol').reset_index())
    train=pd.concat(rows,ignore_index=True).sort_values(['date','symbol'])
    model=fit_ranker(train,features,SimpleNamespace(n_estimators=4,seed=42))
    source=tmp_path/'source';source.mkdir()
    model.booster_.save_model(str(source/'model.txt'))
    meta=dict(schema='horizon_ranker_v1',horizon=asdict(HORIZONS['5d']),features=features,
        trained_asof=str(data.calendar[300].date()),
        dependencies={**{name:version(name) for name in ['lightgbm','numpy','pandas']},'python':platform.python_version()},
        model_sha256=hashlib.sha256((source/'model.txt').read_bytes()).hexdigest())
    (source/'metadata.json').write_text(json.dumps(meta),encoding='utf8')
    bind_dataset_policy(source,data.metadata,features)
    result=export_bundle(source,folder,tmp_path/'bundle')
    assert result['retrained'] is False and result['role']=='research_candidate'
    return data,folder,source,tmp_path/'bundle'


def test_portable_native_model_reproduces_scores_and_signal_date_codes(portable,tmp_path,monkeypatch):
    data,folder,source,bundle=portable
    monkeypatch.setattr('ashare_mvp.model.fit_ranker',lambda *args,**kwargs:pytest.fail('inference retrained'))
    restored,meta,_,_=check_bundle(bundle)
    snap=public_snapshot(data,technical_panel(data),data.calendar[-1])
    values=default_library().compute(snap)[meta['features']]
    picks,evidence=predict_data(bundle,load_public_data(folder),tmp_path/'picks',top_k=24,exploratory=True)
    np.testing.assert_allclose(picks.set_index('symbol').score.reindex(values.index),restored.predict(values),rtol=0,atol=1e-12)
    assert picks.score.is_monotonic_decreasing
    assert picks.set_index('symbol').at['sh.600000','trading_symbol']=='sh.601234'
    assert evidence['retrained'] is False and not evidence['model_ready']


def test_future_rows_cannot_change_earlier_prediction(portable,tmp_path):
    data,_,_,bundle=portable;date=data.calendar[310]
    earlier,_=predict_data(bundle,data,tmp_path/'earlier',asof=date,top_k=24,exploratory=True)
    changed=replace(data,prices=data.prices.copy(),fundamentals=data.fundamentals.copy())
    changed.prices.loc[changed.prices.date.gt(date),'close']*=100
    changed.fundamentals.loc[changed.fundamentals.available_date.gt(date),'roe']=1000
    again,_=predict_data(bundle,changed,tmp_path/'again',asof=date,top_k=24,exploratory=True)
    pd.testing.assert_frame_equal(earlier,again)


@pytest.mark.parametrize('key,value',[('synthetic',False),('price_basis','unrelated'),('schema','standard_dataset'),('security_identity_policy',{'sha256':'changed'})])
def test_changed_dataset_contract_rejected(portable,tmp_path,key,value):
    data,_,_,bundle=portable
    changed=replace(data,metadata={**data.metadata,key:value})
    with pytest.raises(ValueError,match='differs'):
        predict_data(bundle,changed,tmp_path/'bad',exploratory=True)
    assert not (tmp_path/'bad').exists()


def test_metadata_feature_code_dependencies_and_model_integrity_checked(portable):
    _,_,_,bundle=portable
    file=bundle/'training_dataset_metadata.json';raw=file.read_bytes()
    meta=json.loads(raw);meta['source']='tampered';file.write_text(json.dumps(meta),encoding='utf8')
    with pytest.raises(ValueError,match='dataset metadata hash'):check_bundle(bundle)
    file.write_bytes(raw)
    file=bundle/'metadata.json';raw=file.read_bytes();meta=json.loads(raw)
    meta['dependencies']['numpy']='0.invalid';file.write_text(json.dumps(meta),encoding='utf8')
    with pytest.raises(ValueError,match='numpy runtime'):check_bundle(bundle)
    file.write_bytes(raw)
    file=bundle/'feature_input_policy.json';raw=file.read_bytes();policy=json.loads(raw)
    policy['code_sha256']['factors.py']='0'*64;file.write_text(json.dumps(policy),encoding='utf8')
    with pytest.raises(ValueError,match='implementation differs'):check_bundle(bundle)
    file.write_bytes(raw)
    file=bundle/'model.txt';file.write_bytes(file.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='model hash'):check_bundle(bundle)


def test_no_exploration_early_prediction_or_overwrite(portable,tmp_path):
    data,_,_,bundle=portable
    with pytest.raises(ValueError,match='explicit exploratory'):predict_data(bundle,data,tmp_path/'no')
    with pytest.raises(ValueError,match='after model creation'):predict_data(bundle,data,tmp_path/'early',asof=data.calendar[299],exploratory=True)
    predict_data(bundle,data,tmp_path/'one',top_k=5,exploratory=True)
    with pytest.raises(ValueError,match='empty prediction'):predict_data(bundle,data,tmp_path/'one',top_k=5,exploratory=True)


def test_export_requires_original_training_metadata(portable,tmp_path):
    _,folder,source,_=portable
    path=folder/'metadata.json';d=json.loads(path.read_text(encoding='utf8'));d['source']='different'
    path.write_text(json.dumps(d),encoding='utf8')
    with pytest.raises(ValueError,match='different training dataset'):export_bundle(source,folder,tmp_path/'wrong_bundle')
    assert not (tmp_path/'wrong_bundle').exists()

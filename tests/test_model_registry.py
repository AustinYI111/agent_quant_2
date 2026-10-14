import json
import numpy as np
import pandas as pd
import pytest
from ashare_mvp.demo import make_demo
from ashare_mvp.model import Config
from ashare_mvp.pipeline import run
from ashare_mvp.model_registry import export_model,predict_model,check_model


def test_persisted_model_reload_matches_original_predictions(tmp_path):
    data=make_demo(stocks=20,periods=750)
    run(data,tmp_path/'run',Config(min_train_groups=3,n_estimators=3))
    info=export_model(tmp_path/'run',tmp_path/'bundle')
    assert info['synthetic'] and info['role']=='candidate'
    result=predict_model(tmp_path/'bundle',data,tmp_path/'reloaded.csv')
    original=pd.read_csv(tmp_path/'run/latest_top20.csv')
    assert result.symbol.tolist()==original.symbol.tolist()
    np.testing.assert_allclose(result.score,original.score,rtol=0,atol=1e-12)
    from ashare_mvp.backup import backup,restore
    backup(tmp_path,["bundle"],tmp_path/"model.pqr","private-migration-test-password")
    restore(tmp_path/"model.pqr",tmp_path/"other-computer","private-migration-test-password")
    recovered=predict_model(tmp_path/"other-computer/bundle",data,tmp_path/"recovered.csv")
    np.testing.assert_allclose(recovered.score,result.score,rtol=0,atol=1e-12)
    meta=json.loads((tmp_path/'bundle/model.json').read_text())
    assert meta['dataset_hashes'] and meta['feature_contract']['feature_source']
    assert pd.Timestamp(meta['training_label_end']) < pd.Timestamp(meta['training_cutoff'])
    model=tmp_path/'bundle/model.txt'; model.write_text(model.read_text()+'changed')
    with pytest.raises(ValueError,match='hash mismatch'): check_model(tmp_path/'bundle')

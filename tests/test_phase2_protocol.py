import json
from pathlib import Path
import pandas as pd
import pytest
from ashare_mvp.phase2_protocol import (sha256, seal, load_seal, validate_protocol,
    require_selection_rows, completed_date_limit, forecast_role)


@pytest.fixture
def experiment(tmp_path):
    models = {}
    for horizon in ['5d','20d','12m']:
        folder=tmp_path/'models'/horizon;folder.mkdir(parents=True)
        (folder/'model.txt').write_text('explicit synthetic hash-test fixture',encoding='utf8')
        (folder/'metadata.json').write_text(json.dumps(dict(horizon=dict(key=horizon),trained_asof='2025-01-02')),encoding='utf8')
        for name in ['feature_input_policy.json','training_dataset_metadata.json']:(folder/name).write_text('{}',encoding='utf8')
        models[horizon]=dict(relative_path='models/'+horizon,trained_asof='2025-01-02',files_sha256={p.name:sha256(p) for p in folder.iterdir()})
    protocol=dict(schema='phase2_research_protocol_v1',frozen_at_utc='2026-10-20T02:00:00Z',research_end='2025-12-31',independent_start='2026-01-01',prospective_start='2026-10-20',models=models,candidate_groups=[dict(id=g) for g in ['industry','market_state','oneil_box','combined']],validation=dict(independent_data_may_select_parameters=False,unknown_outcomes='unmatured_or_unresolved_never_zero'))
    source=tmp_path/'source.json';source.write_text(json.dumps(protocol),encoding='utf8')
    return tmp_path,source,protocol


def test_protocol_seal_binds_all_models_and_rejects_modified_copy(experiment):
    root,source,protocol=experiment;out=root/'sealed'
    evidence=seal(source,out,root)
    assert load_seal(out,root)[0]==protocol
    assert evidence['protocol_sha256']==sha256(source)
    (out/'protocol.json').write_text('{}',encoding='utf8')
    with pytest.raises(ValueError,match='protocol changed'):load_seal(out,root)


def test_modified_model_blocks_an_existing_valid_seal(experiment):
    root,source,_=experiment;out=root/'sealed';seal(source,out,root)
    (root/'models/20d/model.txt').write_text('changed',encoding='utf8')
    with pytest.raises(ValueError,match='frozen model contract changed'):load_seal(out,root)


def test_sealing_never_overwrites_existing_run(experiment):
    root,source,_=experiment;out=root/'sealed';seal(source,out,root)
    with pytest.raises(ValueError,match='must be new'):seal(source,out,root)


def test_independent_rows_and_cross_boundary_labels_cannot_select_parameters(experiment):
    _,_,protocol=experiment
    assert require_selection_rows(pd.DataFrame(dict(date=['2024-01-02'],label_end=['2025-01-02'])),protocol)
    for dates,ends in [(['2026-01-02'],['2026-01-09']),(['2025-01-02'],['2026-01-02']),(['2024-01-02'],[None])]:
        with pytest.raises(ValueError,match='selection contains'):require_selection_rows(pd.DataFrame(dict(date=dates,label_end=ends)),protocol)


def test_model_from_inside_independent_period_is_not_frozen_baseline(experiment):
    _,_,protocol=experiment;protocol['models']['5d']['trained_asof']='2026-01-02'
    with pytest.raises(ValueError,match='inside independent'):validate_protocol(protocol)


def test_protocol_rejects_overlap_and_selection_on_holdout(experiment):
    _,_,protocol=experiment;protocol['research_end']='2026-01-01'
    with pytest.raises(ValueError,match='overlap'):validate_protocol(protocol)
    protocol['research_end']='2025-12-31';protocol['validation']['independent_data_may_select_parameters']=True
    with pytest.raises(ValueError,match='cannot select'):validate_protocol(protocol)


def test_completed_data_uses_china_clock_instead_of_host_date():
    assert str(completed_date_limit('2026-10-20T03:00:00Z').date())=='2026-10-19'
    assert str(completed_date_limit('2026-10-20T09:20:00Z').date())=='2026-10-20'
    assert str(completed_date_limit('2026-10-19T23:30:00-07:00').date())=='2026-10-19'
    with pytest.raises(ValueError,match='timezone'):completed_date_limit('2026-10-20 10:00:00')


def test_forecast_requires_real_next_session_not_next_weekday(experiment):
    _,_,protocol=experiment
    calendar=pd.to_datetime(['2026-10-20','2026-10-23'])
    result=forecast_role('2026-10-20','2026-10-20T10:00:00Z','2026-10-20T09:30:00Z',calendar,protocol)
    assert result['prospective'] and result['next_entry_open_utc']=='2026-10-23T01:30:00+00:00'
    with pytest.raises(ValueError,match='next exchange session'):forecast_role('2026-10-23','2026-10-23T10:00:00Z','2026-10-23T09:30:00Z',calendar,protocol)


def test_retrospective_score_cannot_be_reported_as_prospective(experiment):
    _,_,protocol=experiment;calendar=pd.to_datetime(['2026-10-20','2026-10-21','2026-10-22'])
    result=forecast_role('2026-10-20','2026-10-21T03:00:00Z','2026-10-21T02:00:00Z',calendar,protocol)
    assert not result['prospective'] and result['role']=='retrospective_independent_snapshot'


def test_forecast_rejects_future_input_and_preclose_publication(experiment):
    _,_,protocol=experiment;calendar=pd.to_datetime(['2026-10-20','2026-10-21'])
    with pytest.raises(ValueError,match='future'):forecast_role('2026-10-20','2026-10-20T10:00:00Z','2026-10-20T11:00:00Z',calendar,protocol)
    with pytest.raises(ValueError,match='release cutoff'):forecast_role('2026-10-20','2026-10-20T08:00:00Z','2026-10-20T07:00:00Z',calendar,protocol)


def test_forecast_clock_must_not_precede_registration(experiment):
    _,_,protocol=experiment;calendar=pd.to_datetime(['2026-10-19','2026-10-20'])
    with pytest.raises(ValueError,match='precedes'):forecast_role('2026-10-19','2026-10-19T10:00:00Z','2026-10-19T09:30:00Z',calendar,protocol)


def test_frozen_model_paths_cannot_escape_project(experiment):
    _,_,protocol=experiment;protocol['models']['5d']['relative_path']='../outside'
    with pytest.raises(ValueError,match='relative model paths'):validate_protocol(protocol)

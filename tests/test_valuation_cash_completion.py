import json
from copy import deepcopy
import pandas as pd
import pytest
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.valuation_cash_completion import complete_research_cash


@pytest.fixture
def source(tmp_path):
    parent=pd.DataFrame([dict(symbol='sh.600001',ex_date='2023-06-05',available_date='2023-05-25',pay_date='2023-06-05',cash_per_share=.442,share_multiplier=1.,reference_cash_per_share=None,reference_share_multiplier=None),dict(symbol='sh.600002',ex_date='2023-07-05',available_date='2023-06-30',pay_date='2023-07-05',cash_per_share=.5,share_multiplier=1.,reference_cash_per_share=None,reference_share_multiplier=None)])
    parent.to_csv(tmp_path/'parent.csv',index=False)
    (tmp_path/'notice.PDF').write_bytes(b'%PDF-fixture-only')
    url='https://static.cninfo.com.cn/finalpage/2023-05-31/notice.PDF';observed='2026-10-20T03:11:00+00:00'
    capture=dict(schema='original_candidate_notice_capture_v1',code='600001',ex_date='2023-06-05',notices=[dict(url=url,sha256=sha256(tmp_path/'notice.PDF'),file='notice.PDF',observed_at_utc=observed)])
    (tmp_path/'capture.json').write_text(json.dumps(capture),encoding='utf8')
    row=dict(symbol='sh.600001',ex_date='2023-06-05',announcement_date='2023-05-31',available_date='2023-06-01',pay_date='2023-06-05',cash_per_share=.555,share_multiplier=1.,reference_cash_per_share=.555,payout_scope='ordinary_a_share_holders',components=[dict(id='annual',cash_per_share=.442),dict(id='special',cash_per_share=.113)],expected_existing=dict(cash_per_share=.442,share_multiplier=1.,pay_date='2023-06-05',available_date='2023-05-25',reference_cash_per_share=None,reference_share_multiplier=None),reviewed=True,source_url=url,source_sha256=sha256(tmp_path/'notice.PDF'),source_page=1,payment_source_page=2,source_file='notice.PDF',source_capture_file='capture.json',source_capture_sha256=sha256(tmp_path/'capture.json'),observed_at_utc=observed)
    config=dict(schema='official_research_cash_completion_v1',expected_parent_actions_sha256=sha256(tmp_path/'parent.csv'),events=[row])
    actions=pd.read_csv(tmp_path/'parent.csv',float_precision='round_trip')
    for name in ['ex_date','available_date','pay_date']:actions[name]=pd.to_datetime(actions[name])
    return actions,config


def execute(tmp_path,actions,config):
    (tmp_path/'review.json').write_text(json.dumps(config),encoding='utf8')
    return complete_research_cash(actions,'parent.csv','review.json',tmp_path,pd.bdate_range('2023-01-01','2025-12-31'),'2025-12-31')


def test_primary_components_complete_actual_cash_once_and_keep_unrelated_events(source,tmp_path):
    actions,config=source
    result,proof=execute(tmp_path,actions,config)
    assert result.loc[0,'cash_per_share']==.555 and result.loc[0,'reference_cash_per_share']==.555
    assert result.loc[0,'cash_completion_old_cash_per_share']==.442
    assert result.loc[0,'available_date']==pd.Timestamp('2023-06-01')
    assert result.loc[0,'share_multiplier']==1. and result.loc[0,'pay_date']==actions.loc[0,'pay_date']
    pd.testing.assert_series_equal(result.loc[1,actions.columns],actions.loc[1])
    assert proof['existing_partial_entitlements_changed'] and proof['unrelated_events_unchanged']
    assert not proof['training_features_or_labels_changed'] and not proof['independent_payment_confirmation']
    with pytest.raises(AssertionError):execute(tmp_path,result,config)


@pytest.mark.parametrize('fault',['parent','partial_anchor','component_sum','duplicate_component','missing_old_component','shares','pay_date','observation','unreviewed','publication','reference','duplicate_event','source_hash','capture_hash','independent'])
def test_cash_cannot_be_completed_from_wrong_or_ambiguous_evidence(source,tmp_path,fault):
    actions,config=source;config=deepcopy(config);r=config['events'][0]
    if fault=='parent':config['expected_parent_actions_sha256']='0'*64
    elif fault=='partial_anchor':r['expected_existing']['cash_per_share']=.443
    elif fault=='component_sum':r['cash_per_share']=.556
    elif fault=='duplicate_component':r['components'][1]['id']='annual'
    elif fault=='missing_old_component':r['components']=[dict(id='a',cash_per_share=.4),dict(id='b',cash_per_share=.155)]
    elif fault=='shares':r['share_multiplier']=1.4
    elif fault=='pay_date':r['pay_date']='2023-06-06'
    elif fault=='observation':r['observed_at_utc']='2023-05-31T08:00:00+00:00'
    elif fault=='unreviewed':r['reviewed']=False
    elif fault=='publication':r['announcement_date']='2023-05-30'
    elif fault=='reference':r['reference_cash_per_share']=.5
    elif fault=='duplicate_event':config['events'].append(deepcopy(r))
    elif fault=='source_hash':r['source_sha256']='0'*64
    elif fault=='capture_hash':r['source_capture_sha256']='0'*64
    elif fault=='independent':r['ex_date']='2026-06-05'
    with pytest.raises(ValueError):execute(tmp_path,actions,config)

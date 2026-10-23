import json
import pandas as pd
import pytest
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.valuation_payments import supplement_missing_valuation_cash


def inputs(tmp_path):
    parent=pd.DataFrame([dict(symbol='sh.600001',ex_date='2023-05-08',available_date='2023-05-04',pay_date='2023-05-08',cash_per_share=.2,share_multiplier=1.)])
    parent.to_csv(tmp_path/'parent.csv',index=False)
    (tmp_path/'notice.PDF').write_bytes(b'%PDF-fixture-only')
    observed='2026-10-20T03:10:59+00:00';url='https://static.cninfo.com.cn/finalpage/2023-05-31/fixture.PDF'
    capture=dict(schema='original_candidate_notice_capture_v1',code='600000',ex_date='2023-06-07',notices=[dict(file='notice.PDF',url=url,sha256=sha256(tmp_path/'notice.PDF'),observed_at_utc=observed)])
    (tmp_path/'capture.json').write_text(json.dumps(capture))
    row=dict(symbol='sh.600000',ex_date='2023-06-07',announcement_date='2023-05-31',available_date='2023-06-01',pay_date='2023-06-07',cash_per_share=.5,share_multiplier=1.,payout_scope='ordinary_a_share_holders',source_url=url,source_sha256=sha256(tmp_path/'notice.PDF'),source_page=1,payment_source_page=2,reviewed=True,source_file='notice.PDF',source_capture_file='capture.json',source_capture_sha256=sha256(tmp_path/'capture.json'),observed_at_utc=observed)
    config=dict(schema='official_missing_research_cash_v1',expected_parent_actions_sha256=sha256(tmp_path/'parent.csv'),events=[row])
    return parent,config


def execute(tmp_path,config):
    (tmp_path/'review.json').write_text(json.dumps(config))
    return supplement_missing_valuation_cash('parent.csv','review.json',tmp_path,pd.bdate_range('2023-01-01','2025-12-31'),'2025-12-31')


def test_missing_cash_is_added_with_original_observation_and_old_entitlements_untouched(tmp_path):
    parent,config=inputs(tmp_path);result,proof=execute(tmp_path,config)
    assert len(result)==2 and result.iloc[-1].cash_per_share==.5 and result.iloc[-1].share_multiplier==1.
    assert result.iloc[-1].first_observed_at_utc==config['events'][0]['observed_at_utc']
    assert result.iloc[0].symbol==parent.iloc[0].symbol and result.iloc[0].cash_per_share==.2
    assert proof['existing_entitlements_unchanged'] and proof['missing_entitlements_corrected']
    assert proof['independent_payment_confirmation'] is False and proof['training_features_or_labels_changed'] is False


@pytest.mark.parametrize('fault',['parent','existing','observation','payment','independent','shares'])
def test_missing_payment_requires_exact_parent_new_key_real_capture_and_valid_clocks(tmp_path,fault):
    parent,config=inputs(tmp_path);row=config['events'][0]
    if fault=='parent':config['expected_parent_actions_sha256']='0'*64
    elif fault=='existing':row.update(symbol='sh.600001',ex_date='2023-05-08',announcement_date='2023-05-03',available_date='2023-05-04',pay_date='2023-05-08')
    elif fault=='observation':row['observed_at_utc']='2023-05-31T00:00:00+00:00'
    elif fault=='payment':row['pay_date']='2023-06-06'
    elif fault=='independent':row['ex_date']='2026-01-01'
    elif fault=='shares':row['share_multiplier']=1.4
    with pytest.raises(ValueError):execute(tmp_path,config)

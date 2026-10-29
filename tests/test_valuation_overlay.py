import json
import pandas as pd
import pytest
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.valuation_overlay import reviewed_valuation_references


def fixture(tmp_path):
    (tmp_path/'source.pdf').write_bytes(b'%PDF-unit-fixture')
    observed='2026-10-20T03:10:59+00:00';url='https://static.cninfo.com.cn/finalpage/2023-05-31/fixture.PDF'
    capture=dict(schema='original_candidate_notice_capture_v1',code='600000',ex_date='2023-06-07',notices=[dict(file='source.pdf',url=url,sha256=sha256(tmp_path/'source.pdf'),observed_at_utc=observed)])
    (tmp_path/'capture.json').write_text(json.dumps(capture))
    row=dict(symbol='sh.600000',ex_date='2023-06-07',reference_cash_per_share=.48,expected_cash_per_share=.5,
        reference_share_multiplier=1.,expected_share_multiplier=1.,announcement_date='2023-05-31',available_date='2023-06-01',
        source_url=url,source_sha256=sha256(tmp_path/'source.pdf'),source_page=2,reviewed=True,
        source_file='source.pdf',source_capture_file='capture.json',source_capture_sha256=sha256(tmp_path/'capture.json'),observed_at_utc=observed)
    actions=pd.DataFrame([dict(symbol='sh.600000',ex_date=pd.Timestamp('2023-06-07'),available_date=pd.Timestamp('2023-06-01'),pay_date=pd.Timestamp('2023-06-07'),cash_per_share=.5,share_multiplier=1.)])
    return actions,row


def test_primary_reference_overlay_keeps_actual_cash_shares_and_observation(tmp_path):
    actions,row=fixture(tmp_path);(tmp_path/'review.json').write_text(json.dumps([row]))
    result,proof=reviewed_valuation_references(actions,'review.json',tmp_path,pd.bdate_range('2023-01-01','2025-12-31'),'2025-12-31')
    assert result.cash_per_share.tolist()==[.5] and result.share_multiplier.tolist()==[1.]
    assert result.reference_cash_per_share.tolist()==[.48]
    assert proof['events']==1 and proof['first_observed_clocks_preserved']
    assert proof['training_features_or_labels_changed'] is False


@pytest.mark.parametrize('fault',['hash','observation','host','actual','independent'])
def test_invalid_reference_source_or_economic_change_is_rejected(tmp_path,fault):
    actions,row=fixture(tmp_path)
    if fault=='hash':row['source_sha256']='0'*64
    elif fault=='observation':row['observed_at_utc']='2023-05-31T00:00:00+00:00'
    elif fault=='host':row['source_url']='https://example.com/source.PDF'
    elif fault=='actual':row['expected_cash_per_share']=.48
    elif fault=='independent':row['ex_date']='2026-01-01'
    (tmp_path/'review.json').write_text(json.dumps([row]))
    with pytest.raises(ValueError):reviewed_valuation_references(actions,'review.json',tmp_path,pd.bdate_range('2023-01-01','2025-12-31'),'2025-12-31')

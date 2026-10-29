import pytest
import json
from copy import deepcopy
import pandas as pd
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.primary_reference_derivation import allocated_a_share_cash
from ashare_mvp.primary_reference_derivation import verify_primary_reference_derivations


def test_reference_uses_all_a_shares_including_treasury_and_excludes_h_shares():
    value=allocated_a_share_cash('0.32',6672070922,423956766,1581964548,8677992236)
    assert value==pytest.approx(.3008813929306641,rel=0,abs=1e-15)
    assert value!=pytest.approx(.32*8254035470/8677992236)


@pytest.mark.parametrize('change',[dict(eligible_a_shares=0),dict(excluded_a_shares=-1),dict(excluded_a_shares=True),dict(h_shares=1581964549),dict(issuer_total_shares=8677992235),dict(eligible_a_shares=6672070922.0),dict(cash_per_eligible_share='NaN'),dict(cash_per_eligible_share='Infinity'),dict(cash_per_eligible_share=0),dict(cash_per_eligible_share=True)])
def test_ambiguous_counts_classes_or_cash_cannot_generate_a_reference(change):
    args=dict(cash_per_eligible_share='0.32',eligible_a_shares=6672070922,excluded_a_shares=423956766,h_shares=1581964548,issuer_total_shares=8677992236)
    args.update(change)
    with pytest.raises(ValueError):allocated_a_share_cash(**args)


def reviewed_sources(tmp_path):
    declarations=[]
    for i,(role,pub) in enumerate([('holder_cash_and_share_classes','2023-05-31'),('excluded_a_share_scope','2023-06-01')]):
        pdf=tmp_path/f'{i}.PDF';pdf.write_bytes(b'%PDF-fixture-only')
        url=f'https://static.cninfo.com.cn/finalpage/{pub}/{i}.PDF';observed='2026-10-20T03:00:00+00:00'
        capture=tmp_path/f'{i}.json';capture.write_text(json.dumps(dict(schema='original_candidate_notice_capture_v1',code='000157',ex_date='2023-06-05',notices=[dict(sha256=sha256(pdf),url=url,file=pdf.name,observed_at_utc=observed)])),encoding='utf8')
        declarations.append(dict(role=role,source_file=pdf.name,source_sha256=sha256(pdf),source_capture_file=capture.name,source_capture_sha256=sha256(capture),source_url=url,source_page=1,announcement_date=pub,observed_at_utc=observed,reviewed=True))
    d=dict(schema='primary_a_share_cash_allocation_v1',cash_per_eligible_share='0.32',eligible_a_shares=50,excluded_a_shares=10,h_shares=40,issuer_total_shares=100,primary_sources=declarations,reference_terms_literal=False)
    row=dict(symbol='sz.000157',ex_date='2023-06-05',expected_cash_per_share=.32,expected_share_multiplier=1.,reference_cash_per_share=allocated_a_share_cash('0.32',50,10,40,100),reference_share_multiplier=1.,available_date='2023-06-02',announcement_date='2023-06-01',reference_derivation=d)
    return row


def test_derivation_binds_both_pre_event_sources_and_latest_available_session(tmp_path):
    row=reviewed_sources(tmp_path)
    proof=verify_primary_reference_derivations([row],tmp_path,pd.bdate_range('2023-01-01','2025-12-31'),'2025-12-31')
    assert proof['events']==1 and len(proof['source_hashes'])==4
    assert proof['derivations'][0]['available_date']=='2023-06-02'
    assert not proof['actual_holder_economics_changed'] and not proof['prices_or_outcomes_used_in_derivation']


@pytest.mark.parametrize('fault',['class_count','holder_cash','derived_cash','missing_scope','duplicate_role','observation','late_source','early_availability','quote_input','source_hash'])
def test_derived_reference_cannot_hide_future_sources_or_market_fitted_parameters(tmp_path,fault):
    row=deepcopy(reviewed_sources(tmp_path));d=row['reference_derivation']
    if fault=='class_count':d['h_shares']=41
    elif fault=='holder_cash':row['expected_cash_per_share']=.31
    elif fault=='derived_cash':row['reference_cash_per_share']=.3
    elif fault=='missing_scope':d['primary_sources'].pop()
    elif fault=='duplicate_role':d['primary_sources'][1]['role']=d['primary_sources'][0]['role']
    elif fault=='observation':d['primary_sources'][0]['observed_at_utc']='2023-05-31T03:00:00+00:00'
    elif fault=='late_source':d['primary_sources'][1]['announcement_date']='2023-06-05'
    elif fault=='early_availability':row['available_date']='2023-06-01'
    elif fault=='quote_input':d['observed_stock_preclose']=6.68
    elif fault=='source_hash':d['primary_sources'][1]['source_sha256']='0'*64
    with pytest.raises(ValueError):verify_primary_reference_derivations([row],tmp_path,pd.bdate_range('2023-01-01','2025-12-31'),'2025-12-31')

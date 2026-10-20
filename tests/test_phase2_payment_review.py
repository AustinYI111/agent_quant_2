import hashlib
import json
import pandas as pd
import pytest
from ashare_mvp.phase2_payment_review import reconcile_phase2_payments


def fixture(tmp_path):
    plans=tmp_path/'plans'; payments=tmp_path/'payments'; notices=tmp_path/'notices'
    folder=plans/'2025Q4'
    for directory in [folder,payments,notices]:directory.mkdir(parents=True)
    cal=tmp_path/'calendar.csv'
    pd.DataFrame({'date':pd.bdate_range('2026-01-05','2026-01-16')}).to_csv(cal,index=False)
    rows=[dict(SECUCODE=code,REPORT_DATE='2025-12-31',EX_DIVIDEND_DATE='2026-01-16',NOTICE_DATE='2026-01-12',ASSIGN_PROGRESS='实施分配',IMPL_PLAN_PROFILE=description,PRETAX_BONUS_RMB=amount,BONUS_RATIO=0.,IT_RATIO=0.) for code,amount,description in [('600000.SH',1.,'10派1元'),('600001.SH',2.,'10派1元'),('600002.SH',3.,'10派3元'),('689009.SH',1.,'10派1元')]]
    pd.DataFrame(rows).to_csv(folder/'records.csv',index=False)
    digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    (plans/'manifest.json').write_text(json.dumps(dict(schema='market_dividend_plans_v1',status='downloaded_raw',periods={'2025-12-31':dict(status='ok',rows=4,hashes={'records.csv':digest(folder/'records.csv')})})),encoding='utf8')
    pd.DataFrame([dict(symbol=symbol,ex_date='2026-01-16',pay_date='2026-01-16',available_date='2026-01-13',announcement_date='2026-01-12',cash_per_share=cash,share_multiplier=1.) for symbol,cash in [('sh.600000',.1),('sh.600001',.2),('sh.600003',.4)]]).to_csv(payments/'actions.csv',index=False)
    (payments/'summary.json').write_text(json.dumps(dict(actions_sha256=digest(payments/'actions.csv'))),encoding='utf8')
    pdf=notices/'notice.PDF';pdf.write_bytes(b'%PDF-test-sealed-fixture')
    source=dict(source_url='https://example.org/notice.PDF',source_sha256=digest(pdf),source_page=1,reviewed=True)
    common=dict(ex_date='2026-01-16',announcement_date='2026-01-12',available_date='2026-01-13',pay_date='2026-01-16',share_multiplier=1.,**source)
    standalone=dict(**common,symbol='sh.600001',source_period='2025-12-31',cash_per_share=.2,expected_provider_cash_per_share=.2,expected_provider_share_multiplier=1.,expected_provider_description='10派1元')
    group=dict(**common,symbol='sh.600002',cash_per_share=.3,reference_cash_per_share=.29,reference_source_page=1,payment_source_page=1,expected_existing=None,components=[dict(source_period='2025-12-31',expected_cash_per_share=.3,expected_description='10派3元',expected_status='missing_payment_evidence')])
    reference=dict(symbol='sh.600001',ex_date='2026-01-16',expected_cash_per_share=.2,reference_cash_per_share=.19,announcement_date='2026-01-12',available_date='2026-01-13',**source)
    scope=dict(symbol='sh.689009',instrument_type='cdr',effective_from='2026-01-16',valid_through='2026-01-16',announcement_date='2026-01-12',source_locator='PDF page 1 CDR',**source)
    config=dict(schema='phase2_official_payment_review_v1',groups=[group],standalone=[standalone],references=[reference],instrument_scope=[scope],notices=[dict(file=pdf.name,sha256=digest(pdf),url=source['source_url'])],limitations=['fixture'])
    review=tmp_path/'review.json';review.write_text(json.dumps(config),encoding='utf8')
    changes=tmp_path/'changes.json'
    changes.write_text(json.dumps({name:[] for name in ['old_symbol','new_symbol','announcement_date','effective_date','share_ratio','source_url']}),encoding='utf8')
    return [plans,payments,cal,review,notices,tmp_path/'out',changes]


def test_sealed_review_keeps_actual_cash_separate_and_retains_unknown_events(tmp_path):
    args=fixture(tmp_path)
    result=reconcile_phase2_payments(*args)
    actions=pd.read_csv(args[5]/'actions.csv').set_index('symbol')
    assert result['events']==3 and result['unmatched_accepted_payment_events']==1
    assert actions.loc['sh.600001','cash_per_share']==.2
    assert actions.loc['sh.600001','reference_cash_per_share']==.19
    assert actions.loc['sh.600002','cash_per_share']==.3
    assert actions.loc['sh.600002','reference_cash_per_share']==.29
    assert result['status_counts']['excluded_instrument']==1
    assert not {'unresolved','missing_payment_evidence'}&set(result['status_counts'])
    assert not result['model_ready'] and not result['certified']
    assert pd.read_csv(args[5]/'unmatched_accepted_payment_events.csv').symbol.tolist()==['sh.600003']
    with pytest.raises(ValueError,match='new reviewed payment output'):reconcile_phase2_payments(*args)


@pytest.mark.parametrize('fault',['pdf_hash','plan_hash','unsealed_notice','path_escape','missing_notices'])
def test_review_rejects_unverified_evidence_before_export(tmp_path,fault):
    args=fixture(tmp_path)
    review=json.loads(args[3].read_text(encoding='utf8'))
    if fault=='pdf_hash':(args[4]/'notice.PDF').write_bytes(b'tampered')
    if fault=='plan_hash':(args[0]/'2025Q4/records.csv').write_bytes(b'tampered')
    if fault=='unsealed_notice':review['groups'][0]['source_sha256']='a'*64
    if fault=='path_escape':review['notices'][0]['file']='../notice.PDF'
    if fault=='missing_notices':review['notices']=[]
    args[3].write_text(json.dumps(review),encoding='utf8')
    with pytest.raises(ValueError):reconcile_phase2_payments(*args)
    assert not args[5].exists()

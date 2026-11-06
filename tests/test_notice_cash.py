import json
import pandas as pd
import pytest
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.notice_cash import append_notice_cash


def fixture(tmp_path):
    data=tmp_path/'data';provider=tmp_path/'provider';plans=tmp_path/'plans';notices=tmp_path/'notices'
    for folder in [data,provider,plans/'2025Q4',notices]:folder.mkdir(parents=True)
    pd.DataFrame([dict(symbol='sh.600000',ex_date='2026-02-11',pay_date='2026-02-11',available_date='2026-02-05',cash_per_share=.1,share_multiplier=1.)]).to_csv(data/'actions.csv',index=False)
    (data/'summary.json').write_text(json.dumps(dict(schema='phase2_reference_reviewed_payments_v1',output_hashes={'actions.csv':sha256(data/'actions.csv')})),encoding='utf8')
    raw=provider/'sh.600001.json';raw.write_text(json.dumps(dict(resultcode=200,count=0,total=0,records=[])),encoding='utf8')
    pd.DataFrame(columns=['F020D','F023D']).to_csv(provider/'sh.600001.csv',index=False)
    vendor=dict(schema='cninfo_dividend_history_v1',status='downloaded_raw',symbols=['sh.600001'],securities={'sh.600001':dict(status='ok',rows=0,hashes={p.name:sha256(p) for p in provider.iterdir()})})
    (provider/'manifest.json').write_text(json.dumps(vendor),encoding='utf8')
    pd.DataFrame({'SECUCODE':['600000.SH']}).to_csv(plans/'2025Q4/records.csv',index=False)
    (plans/'manifest.json').write_text(json.dumps(dict(schema='market_dividend_plans_v1',status='downloaded_raw',periods={'2025-12-31':dict(status='ok',rows=1,hashes={'records.csv':sha256(plans/'2025Q4/records.csv')})})),encoding='utf8')
    pdf=notices/'notice.PDF';pdf.write_bytes(b'%PDF-fixture-sealed')
    event=dict(symbol='sh.600001',ex_date='2026-02-11',announcement_date='2026-02-04',available_date='2026-02-05',pay_date='2026-02-11',cash_per_share=.04,share_multiplier=1.,payout_scope='ordinary_a_share_holders',source_url='https://example.org/notice.PDF',source_sha256=sha256(pdf),source_page=1,payment_source_page=1,reviewed=True,expected_vendor_json_sha256=sha256(raw),expected_vendor_csv_sha256=sha256(provider/'sh.600001.csv'))
    config=dict(schema='notice_cash_for_empty_sources_v1',events=[event],notices=[dict(file='notice.PDF',url=event['source_url'],sha256=event['source_sha256'])])
    review=tmp_path/'review.json';review.write_text(json.dumps(config),encoding='utf8')
    calendar=tmp_path/'calendar.csv';pd.DataFrame({'date':pd.bdate_range('2026-02-02','2026-02-13')}).to_csv(calendar,index=False)
    return dict(data=data,provider=provider,plans=plans,calendar=calendar,review=review,notice_root=notices,out=tmp_path/'out'),config


def test_official_cash_cures_vendor_omission_without_claiming_independent_confirmation(tmp_path):
    args,_=fixture(tmp_path);old=pd.read_csv(args['data']/'actions.csv')
    result=append_notice_cash(**args)
    assert result['events']==2 and result['supplements']==1
    assert result['existing_entitlements_unchanged']
    assert not result['independent_payment_confirmation'] and not result['model_ready']
    current=pd.read_csv(args['out']/'actions.csv')
    assert current.iloc[-1].cash_per_share==.04 and current.iloc[-1].share_multiplier==1
    assert current.iloc[-1].event_evidence_type=='official_notice_cash_vendor_sources_empty'
    pd.testing.assert_series_equal(old.cash_per_share,current.iloc[:1].cash_per_share,check_names=False)
    assert pd.read_csv(args['provider']/'sh.600001.csv').empty
    with pytest.raises(ValueError,match='new official'):append_notice_cash(**args)


@pytest.mark.parametrize('fault',['provider_hash','nonempty_vendor','plan_has_issuer','unreviewed','shares','late_notice','existing_event','pdf_hash','source_path'])
def test_primary_fallback_rejects_conflicts_and_unsafe_sources(tmp_path,fault):
    args,c=fixture(tmp_path)
    if fault=='provider_hash':(args['provider']/'sh.600001.csv').write_bytes(b'tampered')
    if fault=='nonempty_vendor':
        p=args['provider']/'manifest.json';v=json.loads(p.read_text(encoding='utf8'));v['securities']['sh.600001']['rows']=1;p.write_text(json.dumps(v),encoding='utf8')
    if fault=='plan_has_issuer':
        file=args['plans']/'2025Q4/records.csv';pd.DataFrame({'SECUCODE':['600001.SH']}).to_csv(file,index=False)
        p=args['plans']/'manifest.json';v=json.loads(p.read_text(encoding='utf8'));v['periods']['2025-12-31']['hashes']['records.csv']=sha256(file);p.write_text(json.dumps(v),encoding='utf8')
    if fault=='unreviewed':c['events'][0]['reviewed']=False
    if fault=='shares':c['events'][0]['share_multiplier']=1.4
    if fault=='late_notice':c['events'][0]['announcement_date']='2026-02-11'
    if fault=='existing_event':c['events'][0]['symbol']='sh.600000'
    if fault=='pdf_hash':(args['notice_root']/'notice.PDF').write_bytes(b'tampered')
    if fault=='source_path':c['notices'][0]['file']='../notice.PDF'
    args['review'].write_text(json.dumps(c),encoding='utf8')
    with pytest.raises(ValueError):append_notice_cash(**args)
    assert not args['out'].exists()

import importlib.util,json
from pathlib import Path
import pandas as pd
import pytest
from ashare_mvp.phase2_protocol import sha256


def fixture(tmp_path):
    spec=importlib.util.spec_from_file_location('reference_cli',Path(__file__).resolve().parents[1]/'scripts/apply_reviewed_references.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    data=tmp_path/'data';data.mkdir();notices=tmp_path/'notices';notices.mkdir()
    a=pd.DataFrame([dict(symbol='sh.600000',ex_date='2026-02-11',available_date='2026-02-05',pay_date='2026-02-11',cash_per_share=.03,share_multiplier=1.),dict(symbol='sh.600001',ex_date='2026-02-12 00:00:00',available_date='2026-02-06 00:00:00',pay_date='2026-02-12',cash_per_share=.1,share_multiplier=1.)])
    a.to_csv(data/'actions.csv',index=False)
    (data/'summary.json').write_text(json.dumps(dict(schema='reviewed_cash_supplemented_payments_v1',output_hashes={'actions.csv':sha256(data/'actions.csv')})),encoding='utf8')
    pdf=notices/'notice.PDF';pdf.write_bytes(b'%PDF-fixture-sealed')
    source=dict(source_url='https://example.org/notice.PDF',source_sha256=sha256(pdf),source_page=1,reviewed=True)
    override=dict(symbol='sh.600000',ex_date='2026-02-11',announcement_date='2026-02-04',available_date='2026-02-05',reference_cash_per_share=.02,expected_cash_per_share=.03,expected_share_multiplier=1.,reference_share_multiplier=1.,**source)
    config=dict(schema='phase2_reviewed_reference_overrides_v1',overrides=[override],notices=[dict(file='notices/notice.PDF',sha256=sha256(pdf),url=source['source_url'])])
    review=tmp_path/'review.json';review.write_text(json.dumps(config),encoding='utf8')
    calendar=tmp_path/'calendar.csv';pd.DataFrame({'date':pd.bdate_range('2026-02-02','2026-02-13')}).to_csv(calendar,index=False)
    return module,dict(data=data,calendar=calendar,review=review,notice_root=tmp_path,out=tmp_path/'out'),config,a


def test_reference_files_accept_sibling_output_and_mixed_dates_preserving_assets(tmp_path):
    module,args,config,original=fixture(tmp_path)
    result=module.run(**args)
    assert result['reference_overrides']==1 and result['events']==2 and result['shareholder_entitlements_unchanged']
    assert not result['model_ready']
    current=pd.read_csv(args['out']/'actions.csv')
    assert current.reference_cash_per_share.iloc[0]==.02
    for column in ['symbol','cash_per_share','share_multiplier','pay_date']:
        pd.testing.assert_series_equal(current[column],original[column])
    assert result['output_hashes']['actions.csv']==sha256(args['out']/'actions.csv')
    with pytest.raises(ValueError,match='new reference'):module.run(**args)


@pytest.mark.parametrize('fault',['pdf_changed','payment_changed','unsealed_notice','path_escape','unreviewed','cash_conflict','late_notice'])
def test_reference_files_reject_conflicts_before_export(tmp_path,fault):
    module,args,config,_=fixture(tmp_path)
    if fault=='pdf_changed':(tmp_path/'notices/notice.PDF').write_bytes(b'tampered')
    if fault=='payment_changed':(args['data']/'actions.csv').write_bytes(b'tampered')
    if fault=='unsealed_notice':config['overrides'][0]['source_sha256']='b'*64
    if fault=='path_escape':config['notices'][0]['file']='../outside.PDF'
    if fault=='unreviewed':config['overrides'][0]['reviewed']=False
    if fault=='cash_conflict':config['overrides'][0]['expected_cash_per_share']=.04
    if fault=='late_notice':config['overrides'][0]['announcement_date']='2026-02-11'
    args['review'].write_text(json.dumps(config),encoding='utf8')
    with pytest.raises(ValueError):module.run(**args)
    assert not args['out'].exists()

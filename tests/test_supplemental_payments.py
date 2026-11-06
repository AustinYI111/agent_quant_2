import pandas as pd
import pytest
from ashare_mvp.supplemental_payments import supplement_cash_payments


def inputs():
    a=pd.DataFrame([dict(symbol='sh.600000',ex_date='2026-02-06',pay_date='2026-02-06',cash_per_share=.1,share_multiplier=1.)])
    u=pd.DataFrame([dict(symbol='sh.600930',ex_date='2026-02-11',available_date='2026-02-05',pay_date='2026-02-11',cash_per_share=.03,share_multiplier=1.,source_file='sh.600930.csv',source_row=1)])
    e=pd.DataFrame([dict(symbol='sh.600930',ex_date='2026-02-11',announcement_date='2026-02-04',available_date='2026-02-05',pay_date='2026-02-11',cash_per_share=.03,reference_cash_per_share=.03,expected_cash_per_share=.03,expected_pay_date='2026-02-11',expected_source_file='sh.600930.csv',expected_source_row=1,source_url='https://example.org/notice.pdf',source_sha256='a'*64,source_page=1,payment_source_page=1,reference_source_page=1,reviewed=True)])
    return a,u,e,pd.bdate_range('2026-02-02','2026-02-13')


def test_cash_supplement_requires_independent_payment_and_is_added_once():
    a,u,e,cal=inputs();before=a.copy(deep=True)
    result,remaining,audit=supplement_cash_payments(a,u,e,cal)
    assert len(result)==2 and remaining.empty and len(audit)==1
    assert result.iloc[-1].cash_per_share==.03
    assert result.iloc[-1].share_multiplier==1
    assert pd.isna(result.iloc[-1].source_period)
    assert not result.iloc[-1].event_vintage_certified
    assert result.iloc[-1].event_evidence_type=='notice_confirmed_independent_cash_supplement'
    pd.testing.assert_frame_equal(a,before)
    with pytest.raises(ValueError,match='replace'):supplement_cash_payments(result,u,e,cal)


@pytest.mark.parametrize('fault',['cash','pay_date','row','file','unreviewed','page','url','hash','late_notice','late_available','shares','unknown_payment'])
def test_wrong_anchor_or_unproven_cash_fails(fault):
    a,u,e,cal=inputs()
    if fault=='cash':e.loc[0,'cash_per_share']=.04
    if fault=='pay_date':e.loc[0,'pay_date']='2026-02-12'
    if fault=='row':e.loc[0,'expected_source_row']=2
    if fault=='file':e.loc[0,'expected_source_file']='other.csv'
    if fault=='unreviewed':e.loc[0,'reviewed']=False
    if fault=='page':e.loc[0,'source_page']=0
    if fault=='url':e.loc[0,'source_url']='file:///notice.pdf'
    if fault=='hash':e.loc[0,'source_sha256']='bad'
    if fault=='late_notice':e.loc[0,'announcement_date']='2026-02-11'
    if fault=='late_available':e.loc[0,'available_date']='2026-02-12'
    if fault=='shares':u.loc[0,'share_multiplier']=1.908
    if fault=='unknown_payment':u=u.iloc[:0]
    with pytest.raises(ValueError):supplement_cash_payments(a,u,e,cal)


def test_other_unmatched_event_is_preserved_and_reference_is_separate():
    a,u,e,cal=inputs()
    u=pd.concat([u,u.assign(symbol='sh.600999')],ignore_index=True)
    e['reference_cash_per_share']=.029
    result,remaining,_=supplement_cash_payments(a,u,e,cal)
    assert remaining.symbol.tolist()==['sh.600999']
    assert result.iloc[-1].reference_cash_per_share==.029
    assert result.iloc[-1].cash_per_share==.03


@pytest.mark.parametrize('fault',[None,'source_changed','pdf_changed','unsealed_pdf'])
def test_file_workflow_seals_notice_and_payment_evidence(tmp_path,fault):
    import importlib.util,json
    from pathlib import Path
    from ashare_mvp.phase2_protocol import sha256
    spec=importlib.util.spec_from_file_location('supplement_cli',Path(__file__).resolve().parents[1]/'scripts/supplement_reviewed_cash.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    a,u,e,cal=inputs();data=tmp_path/'data';notices=tmp_path/'notices'
    data.mkdir();notices.mkdir()
    a.to_csv(data/'actions.csv',index=False);u.to_csv(data/'unmatched_accepted_payment_events.csv',index=False)
    pd.DataFrame([dict(status='not_implemented')]).to_csv(data/'coverage.csv',index=False)
    (data/'summary.json').write_text(json.dumps(dict(schema='phase2_reviewed_payments_v1',output_hashes={p.name:sha256(p) for p in data.glob('*.csv')})),encoding='utf8')
    calendar=tmp_path/'calendar.csv';pd.DataFrame({'date':cal}).to_csv(calendar,index=False)
    pdf=notices/'notice.PDF';pdf.write_bytes(b'%PDF-fixture-sealed')
    e['source_sha256']=sha256(pdf)
    review=tmp_path/'review.json'
    config=dict(schema='reviewed_cash_supplements_v1',events=e.to_dict('records'),notices=[dict(file=pdf.name,sha256=sha256(pdf),url=e.iloc[0].source_url)])
    if fault=='source_changed':(data/'actions.csv').write_bytes(b'tampered')
    if fault=='pdf_changed':pdf.write_bytes(b'tampered')
    if fault=='unsealed_pdf':config['events'][0]['source_sha256']='b'*64
    review.write_text(json.dumps(config),encoding='utf8');out=tmp_path/'out'
    if fault:
        with pytest.raises(ValueError):module.run(data,calendar,review,notices,out)
        assert not out.exists()
    else:
        r=module.run(data,calendar,review,notices,out)
        assert r['events']==2 and r['supplements']==1 and r['remaining_unmatched_payments']==0
        assert not r['model_ready']
        assert r['output_hashes']['actions.csv']==sha256(out/'actions.csv')
        pd.testing.assert_frame_equal(pd.read_csv(data/'actions.csv'),a)

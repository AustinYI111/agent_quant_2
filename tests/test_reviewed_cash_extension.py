import json
import pandas as pd
import pytest
from ashare_mvp.company_action_intake import intake
from ashare_mvp.reviewed_cash_extension import extend
from ashare_mvp.phase2_protocol import sha256
from test_company_action_intake import fixture as intake_fixture,row


def fixture(tmp_path,differential=False,unknown_provider=False):
    args,root,work,config,_=intake_fixture(tmp_path)
    parent=root/'actions/actions.csv';prior=pd.read_csv(parent);prior['available_date']=str((pd.Timestamp(config['signal_date'])-pd.Timedelta(days=2)).date());prior.to_csv(parent,index=False)
    (root/'actions/summary.json').write_text(json.dumps(dict(output_hashes={'actions.csv':sha256(parent)})))
    def fetch(date,page):
        data=row('600001.SH',REPORT_DATE=date,EX_DIVIDEND_DATE=config['signal_date'],NOTICE_DATE=str((pd.Timestamp(config['signal_date'])-pd.Timedelta(days=2)).date()))
        if unknown_provider:data['PRETAX_BONUS_RMB']=None
        return dict(success=True,result=dict(count=1,pages=1,data=[data]))
    args['fetch']=fetch;intake(**args)
    source=work/'notice';source.mkdir();pdf=source/'1.PDF';pdf.write_bytes(b'%PDF-1.4\nexplicit unit fixture: source hash contract only')
    now=pd.Timestamp.now(tz='UTC').isoformat();signal=pd.Timestamp(config['signal_date']);pub=str((signal-pd.Timedelta(days=2)).date())
    url='https://static.cninfo.com.cn/finalpage/'+pub+'/1.PDF'
    capture=source/'capture.json';capture.write_text(json.dumps(dict(code='600001',ex_date=config['signal_date'],notices=[dict(title='权益分派实施公告',url=url,sha256=sha256(pdf),observed_at_utc=now)])))
    e=dict(symbol='sh.600001',record_date=str((signal-pd.Timedelta(days=1)).date()),ex_date=config['signal_date'],pay_date=config['signal_date'],announcement_date=pub,available_date=config['signal_date'],
           source_period=config['report_end'],cash_per_share=.19153 if differential else .1,share_multiplier=1.,reference_cash_per_share=.19112 if differential else .1,reference_share_multiplier=1.,
           differential_distribution=differential,reference_basis='issuer_declared_rounded_reference' if differential else 'ordinary_cash_distribution_no_share_change',
           source_file='@work/notice/1.PDF',source_sha256=sha256(pdf),capture_file='@work/notice/capture.json',capture_sha256=sha256(capture),source_url=url,
           first_observed_at_utc=now,reviewed_at_utc=now,reviewed=True,reviewed_pages=[1,2],bank_receipt_verified=False,original_vintages_verified=False,
           parameter_pages={k:1 for k in ['cash_per_share','share_multiplier','record_date','ex_date','pay_date','reference_cash_per_share']})
    review=dict(schema='current_primary_company_action_review_v2',status='terms_reviewed_quote_check_pending',raw_quote_check_completed=False,events=[e])
    (work/'review.json').write_text(json.dumps(review))
    return dict(project=str(root),work_root=str(work),parent_actions='actions',intake='@work/intake',review='@work/review.json',calendar='calendar.csv',out='@work/staged'),root,work,review


@pytest.mark.parametrize('differential',[False,True])
def test_new_cash_is_staged_without_changing_parent_and_reference_is_separate(tmp_path,differential):
    args,root,work,_=fixture(tmp_path,differential);before=sha256(root/'actions/actions.csv');result=extend(**args)
    assert sha256(root/'actions/actions.csv')==before and result['new_events']==1 and result['events']==2
    assert not result['prediction_ready'] and not result['raw_quote_check_completed']
    saved=pd.read_csv(work/'staged/actions.csv',float_precision='round_trip')
    assert saved.cash_per_share.iloc[0]==.1
    assert saved.cash_per_share.iloc[1]==(.19153 if differential else .1)
    assert saved.reference_cash_per_share.iloc[1]==(.19112 if differential else .1)
    assert saved.pay_date.str.fullmatch(r'\d{4}-\d{2}-\d{2}').all()
    parsed=pd.read_csv(work/'staged/actions.csv',parse_dates=['ex_date','available_date','pay_date'])
    assert all(pd.api.types.is_datetime64_any_dtype(parsed[c]) for c in ['ex_date','available_date','pay_date'])


def test_missing_provider_number_remains_unknown_even_when_primary_cash_is_reviewed(tmp_path):
    args,root,work,_=fixture(tmp_path,unknown_provider=True);result=extend(**args)
    event=result['event_audit'][0]
    assert event['provider_cash_per_share'] is None and event['primary_cash_differs_from_provider'] is None
    assert event['cash_per_share']==.1 and not result['prediction_ready']


@pytest.mark.parametrize('fault',['tampered_pdf','wrong_issuer','backdated','future_clock','unreviewed','missing_page','boolean_page','share_change','bad_ordinary_reference','claimed_bank_receipt','already_quote_checked'])
def test_unproven_or_changed_primary_cash_evidence_refused(tmp_path,fault):
    args,root,work,review=fixture(tmp_path);e=review['events'][0]
    if fault=='tampered_pdf':(work/'notice/1.PDF').write_bytes(b'%PDF-modified')
    elif fault=='wrong_issuer':e['symbol']='sh.600002'
    elif fault=='backdated':e['available_date']=e['announcement_date']
    elif fault=='future_clock':e['reviewed_at_utc']='2099-01-01T00:00:00Z'
    elif fault=='unreviewed':e['reviewed']=False
    elif fault=='missing_page':e['parameter_pages']['cash_per_share']=3
    elif fault=='boolean_page':e['parameter_pages']['cash_per_share']=True
    elif fault=='share_change':e['share_multiplier']=1.3
    elif fault=='bad_ordinary_reference':e['reference_cash_per_share']=.05
    elif fault=='claimed_bank_receipt':e['bank_receipt_verified']=True
    elif fault=='already_quote_checked':review['raw_quote_check_completed']=True
    (work/'review.json').write_text(json.dumps(review))
    with pytest.raises(ValueError):extend(**args)
    assert not (work/'staged').exists()

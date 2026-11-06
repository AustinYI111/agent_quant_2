from copy import deepcopy
from pathlib import Path
import json
import pandas as pd
import pytest
from ashare_mvp.company_action_intake import classify_plans,intake
from ashare_mvp.phase2_protocol import sha256


def row(code='600000.SH',**updates):
    r=dict(SECUCODE=code,REPORT_DATE='2026-06-30',EX_DIVIDEND_DATE='2026-10-29',NOTICE_DATE='2026-10-26',PLAN_NOTICE_DATE='2026-08-01',
           ASSIGN_PROGRESS='实施分配',PRETAX_BONUS_RMB=1.,BONUS_RATIO=0.,IT_RATIO=0.,IMPL_PLAN_PROFILE='10派1元',
           source_period='2026-06-30',source_file='fixture.csv',source_row=0,observed_at_utc='2026-10-29T00:00:00Z')
    r.update(updates);return r


def classify(rows,parent=None):
    prior=pd.DataFrame(parent or [dict(symbol='sh.600000',ex_date='2026-10-29',cash_per_share=.1,share_multiplier=1.,pay_date='2026-10-30')])
    return classify_plans(pd.DataFrame(rows),prior,pd.bdate_range('2026-10-01','2026-11-10'),'2026-10-28','2026-11-06')


def test_matched_terms_new_events_and_proposals_never_become_payments():
    result,queue,absent=classify([row(),row('600001.SH'),row('600002.SH',ASSIGN_PROGRESS='预案',EX_DIVIDEND_DATE=None)])
    assert result.status.tolist()==['existing_terms_match','new_event_needs_notice','not_implemented']
    assert len(queue)==1 and absent.empty and not result.candidate_applied.any() and not result.payment_date_supplied.any()


def test_changed_terms_unknown_dates_and_numeric_description_conflicts_are_queued():
    for change in [dict(PRETAX_BONUS_RMB=2.,IMPL_PLAN_PROFILE='10派2元'),dict(EX_DIVIDEND_DATE=None),dict(IMPL_PLAN_PROFILE='10派9元'),dict(NOTICE_DATE='2026-10-30')]:
        result,queue,_=classify([row(**change)])
        assert len(queue)==1 and queue.status.iloc[0] in ['existing_terms_conflict','unresolved_provider_terms_or_dates']


def test_multiple_periods_same_execution_are_not_added_or_cherry_picked():
    first=row();second=row(REPORT_DATE='2026-03-31',source_period='2026-03-31')
    result,queue,_=classify([first,second])
    assert len(queue)==2 and queue.status.eq('overlapping_plan_rows_need_notice').all()


def test_beijing_cdr_and_old_windows_are_explicit_but_old_events_survive():
    result,queue,absent=classify([row('870001.BJ'),row('689009.SH'),row(EX_DIVIDEND_DATE='2026-10-20')])
    assert result.status.tolist()==['excluded_instrument','excluded_instrument','outside_review_window']
    assert queue.empty and absent.symbol.tolist()==['sh.600000']


def test_ambiguous_prior_keys_and_empty_sources_cannot_certify_no_actions():
    parent=[dict(symbol='sh.600000',ex_date='2026-10-29',cash_per_share=.1,share_multiplier=1.)]*2
    with pytest.raises(ValueError,match='prior'):classify([row()],parent)
    with pytest.raises(ValueError,match='nonempty'):classify([])


def fixture(tmp_path,failure=False):
    root=tmp_path/'project';work=tmp_path/'work';root.mkdir();work.mkdir();(root/'actions').mkdir()
    signal=pd.Timestamp.now(tz='Asia/Shanghai').tz_localize(None).normalize();period=pd.Period(signal,freq='Q').start_time-pd.Timedelta(days=1)
    pd.DataFrame([dict(symbol='sh.600000',ex_date=str(signal.date()),cash_per_share=.1,share_multiplier=1.,pay_date=str((signal+pd.Timedelta(days=1)).date()))]).to_csv(root/'actions/actions.csv',index=False)
    (root/'actions/summary.json').write_text(json.dumps(dict(output_hashes={'actions.csv':sha256(root/'actions/actions.csv')})))
    cal=pd.date_range(signal-pd.Timedelta(days=10),signal+pd.Timedelta(days=10));pd.DataFrame(dict(calendar_date=cal,is_trading_day='1')).to_csv(root/'calendar.csv',index=False)
    pd.DataFrame([dict(old_symbol='sz.300114',new_symbol='sz.302132',announcement_date='2025-01-01',effective_date='2025-01-02',share_ratio=1,source_url='https://example.test/identity')]).to_json(root/'changes.json',orient='records')
    config=dict(schema='company_action_intake_inputs_v1',signal_date=str(signal.date()),report_start=str(period.date()),report_end=str(period.date()),
                window_start=str(signal.date()),window_end=str((signal+pd.Timedelta(days=5)).date()),calendar='calendar.csv',code_changes='changes.json',parent_actions='actions')
    (root/'inputs.json').write_text(json.dumps(config));calls=[]
    def fetch(date,page):
        calls.append((date,page))
        if failure:return dict(success=False,message='fixture outage')
        data=row(REPORT_DATE=date,EX_DIVIDEND_DATE=str(signal.date()),NOTICE_DATE=str((signal-pd.Timedelta(days=2)).date()))
        return dict(success=True,result=dict(count=1,pages=1,data=[data]))
    return dict(project=str(root),work_root=str(work),inputs='inputs.json',out='@work/intake',fetch=fetch),root,work,config,calls


def test_intake_is_readonly_and_contains_hashed_raw_candidate_evidence(tmp_path):
    args,root,work,_,_=fixture(tmp_path);before=sha256(root/'actions/actions.csv');result=intake(**args)
    assert result['status']=='raw_candidates_classified_primary_review_pending' and not result['candidates_applied'] and not result['prediction_ready']
    assert result['raw_rows']==1 and sha256(root/'actions/actions.csv')==before
    assert not (work/'intake/actions.csv').exists()
    for name,digest in result['output_hashes'].items():assert sha256(work/'intake'/name)==digest
    with pytest.raises(ValueError,match='fresh'):intake(**args)


def test_partial_requests_do_not_produce_an_admissible_queue(tmp_path):
    args,root,work,_,_=fixture(tmp_path,True);result=intake(**args)
    assert result['status']=='partial_raw_no_candidate_admission' and not (work/'intake/notice_review_queue.csv').exists()


@pytest.mark.parametrize('fault',['changed_parent','backdated','project_output'])
def test_invalid_inputs_rejected_before_network(tmp_path,fault):
    args,root,work,config,calls=fixture(tmp_path)
    if fault=='changed_parent':(root/'actions/actions.csv').write_text('modified')
    if fault=='backdated':config['signal_date']='2000-01-01'
    if fault=='project_output':args['out']='local'
    (root/'inputs.json').write_text(json.dumps(config))
    with pytest.raises(ValueError):intake(**args)
    assert not calls and not (work/'intake').exists()

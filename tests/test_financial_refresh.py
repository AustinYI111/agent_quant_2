from datetime import datetime,timezone
from pathlib import Path
import importlib.util
import json
import pandas as pd
import pytest
from ashare_mvp.financial_refresh import refresh,CODES
from ashare_mvp.phase2_protocol import sha256


def fixture(tmp_path,fault=None):
    root=tmp_path/'project';work=tmp_path/'work';root.mkdir();work.mkdir()
    (root/'ashare_mvp').mkdir();code=Path(__file__).resolve().parents[1]/'ashare_mvp'
    for name in CODES:(root/'ashare_mvp'/name).write_bytes((code/name).read_bytes())
    today=pd.Timestamp.now(tz='Asia/Shanghai').tz_localize(None).normalize()
    quarter=pd.Period(today,freq='Q').start_time-pd.Timedelta(days=1)
    oldperiod=pd.Period(quarter,freq='Q').start_time-pd.Timedelta(days=1)
    prior=root/'prior';prior.mkdir();observed=(pd.Timestamp.now(tz='UTC')-pd.Timedelta(days=7)).isoformat()
    available=pd.Timestamp(observed).tz_convert('Asia/Shanghai').tz_localize(None).normalize()
    for family,name in [('earnings','financial_panel.csv'),('balances','balance_panel.csv')]:
        folder=prior/family;folder.mkdir()
        row=dict(symbol='sh.600000',report_date=str(oldperiod.date()),available_date=str(available.date()),first_observed_at_utc=observed)
        row.update(dict(roe=.1,revenue_growth=.2,earnings_growth=.3) if family=='earnings' else dict(debt_ratio=.4))
        pd.DataFrame([row]).to_csv(folder/name,index=False)
        (folder/'summary.json').write_text(json.dumps(dict(partial_source=False,panel_sha256=sha256(folder/name))))
    cal=pd.date_range(pd.Timestamp(year=today.year-1,month=1,day=1),today+pd.Timedelta(days=10))
    pd.DataFrame(dict(calendar_date=cal,is_trading_day='1')).to_csv(root/'calendar.csv',index=False)
    pd.DataFrame([dict(old_symbol='sz.300114',new_symbol='sz.302132',announcement_date='2025-01-01',effective_date='2025-01-02',share_ratio=1,source_url='https://example.test/identity')]).to_json(root/'changes.json',orient='records')
    config=dict(schema='observed_financial_refresh_inputs_v1',signal_date=str(today.date()),calendar='calendar.csv',code_changes='changes.json',
                report_start=str(quarter.date()),report_end=str(quarter.date()),history=[dict(earnings='prior/earnings',balances='prior/balances')])
    (root/'inputs.json').write_text(json.dumps(config));calls=[]
    def earnings(date,page):
        calls.append(('earnings',page))
        if fault=='partial':return dict(success=False,message='fixture unavailable')
        notice=str((quarter+pd.Timedelta(days=1)).date())
        row=dict(SECUCODE='600000.SH',REPORTDATE=date,NOTICE_DATE=notice,UPDATE_DATE=notice,EITIME=notice,WEIGHTAVG_ROE=20,YSTZ=30,SJLTZ=40)
        return dict(success=True,result=dict(count=1,pages=1,data=[row]))
    def balances(date,page):
        calls.append(('balances',page));notice=str((quarter+pd.Timedelta(days=1)).date())
        if fault=='late_balance':notice=str((today+pd.Timedelta(days=2)).date())
        row=dict(SECUCODE='600000.SH',SECURITY_CODE='600000',REPORT_DATE=date,NOTICE_DATE=notice,TOTAL_ASSETS=100,TOTAL_LIABILITIES=50)
        return dict(success=True,result=dict(count=1,pages=1,data=[row]))
    return dict(project=str(root),work_root=str(work),inputs='inputs.json',out='@work/refresh',earnings_fetch=earnings,balance_fetch=balances),root,work,config,calls


def test_actual_clocks_prior_versions_and_portable_history_are_preserved(tmp_path):
    args,root,work,_,calls=fixture(tmp_path);before=sha256(root/'prior/earnings/financial_panel.csv')
    result=refresh(**args)
    assert result['status']=='dependencies_refreshed_before_join' and not result['prediction_generated']
    assert sha256(root/'prior/earnings/financial_panel.csv')==before
    panel=pd.read_csv(work/'refresh/history/combined/financial_panel.csv')
    assert len(panel)==2 and panel.report_date.nunique()==2 and panel.debt_ratio.tolist()==[.4,.5]
    observed=pd.to_datetime(panel.first_observed_at_utc,utc=True)
    assert pd.to_datetime(panel.available_date).ge(observed.dt.tz_convert('Asia/Shanghai').dt.tz_localize(None).dt.normalize()).all()
    summary=json.loads((work/'refresh/history/combined/summary.json').read_text())
    assert all(s['directory'].startswith('@work/') for s in summary['sources'])
    for name,digest in result['output_hashes'].items():assert sha256(work/'refresh'/name)==digest
    with pytest.raises(ValueError,match='fresh'):refresh(**args)


def test_late_balance_does_not_backfill_the_new_earnings_version(tmp_path):
    args,root,work,_,_=fixture(tmp_path,'late_balance');refresh(**args)
    panel=pd.read_csv(work/'refresh/history/combined/financial_panel.csv',parse_dates=['available_date','report_date'])
    latest=panel[panel.report_date.eq(panel.report_date.max())].sort_values('available_date')
    assert len(latest)==2 and pd.isna(latest.debt_ratio.iloc[0]) and latest.debt_ratio.iloc[1]==.5


def test_partial_pages_are_retained_without_building_a_usable_history(tmp_path):
    args,root,work,_,calls=fixture(tmp_path,'partial');result=refresh(**args)
    assert result['status']=='partial_raw_no_normalization' and not (work/'refresh/history').exists()
    assert len(calls)==2 and (work/'refresh/raw/earnings/manifest.json').is_file()


@pytest.mark.parametrize('fault',['changed_prior','backdated_day','future_period','duplicate_history','project_output'])
def test_invalid_refresh_refused_before_network(tmp_path,fault):
    args,root,work,config,calls=fixture(tmp_path)
    if fault=='changed_prior':(root/'prior/earnings/financial_panel.csv').write_text('modified')
    if fault=='backdated_day':config['signal_date']=str((pd.Timestamp(config['signal_date'])-pd.Timedelta(days=1)).date())
    if fault=='future_period':config['report_end']='2099-12-31'
    if fault=='duplicate_history':config['history'].append(config['history'][0])
    if fault=='project_output':args['out']='local'
    (root/'inputs.json').write_text(json.dumps(config))
    with pytest.raises(ValueError):refresh(**args)
    assert not calls and not (work/'refresh').exists()


@pytest.mark.parametrize('fault',[None,'factor','future_dependency'])
def test_independent_version_index_checks_values_even_with_resealed_output_hashes(tmp_path,fault):
    args,root,work,_,_=fixture(tmp_path);refresh(**args)
    source=work/'refresh';proof=json.loads((source/'refresh.json').read_text())
    file=source/'history/combined/financial_panel.csv'
    if fault is not None:
        panel=pd.read_csv(file)
        if fault=='factor':panel.loc[0,'roe']+=.2
        else:panel.loc[0,'balance_available_date']='2099-01-01'
        panel.to_csv(file,index=False)
        summary_file=source/'history/combined/summary.json';summary=json.loads(summary_file.read_text())
        summary['panel_sha256']=sha256(file);summary_file.write_text(json.dumps(summary))
        proof['combined']['panel_sha256']=sha256(file)
        for p in [file,summary_file]:proof['output_hashes'][p.relative_to(source).as_posix()]=sha256(p)
        (source/'refresh.json').write_text(json.dumps(proof))
    spec=importlib.util.spec_from_file_location('independent_financial_refresh',Path(__file__).resolve().parents[1]/'scripts/audit_financial_refresh.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    check=dict(project=str(root),work_root=str(work),refresh='@work/refresh',inputs='inputs.json',out='@work/audited.json')
    if fault is None:assert module.audit(**check)['records']==2
    else:
        with pytest.raises(AssertionError):module.audit(**check)
        assert not (work/'audited.json').exists()

import json
import pandas as pd
import pytest
from ashare_mvp.financial_batch import download_financials
from ashare_mvp.observed_financials import normalize_observed_financials
from ashare_mvp.balance_batch import download_balance_batch, normalize_balance_batch
from ashare_mvp.financial_dependencies import combine_financial_files


def raw_capture(tmp_path, observation):
    source=tmp_path/'raw'
    def fetch(date,page):
        row=dict(SECUCODE='600000.SH',REPORTDATE='2026-06-30',NOTICE_DATE='2026-08-20',UPDATE_DATE='2026-08-20',EITIME='2026-08-20',WEIGHTAVG_ROE=10,YSTZ=20,SJLTZ=30)
        return dict(success=True,result=dict(count=1,pages=1,data=[row]))
    manifest=download_financials(source,'2026-06-30','2026-06-30',fetch=fetch)
    manifest['quarters']['2026-06-30']['retrieved_at']=observation
    (source/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')
    return source


def test_new_revision_never_appears_in_earlier_signal_dates(tmp_path):
    source=raw_capture(tmp_path,'2026-10-20T06:00:00Z')
    proof=normalize_observed_financials(source,pd.bdate_range('2026-08-01','2026-11-01'),tmp_path/'out')
    frame=pd.read_csv(tmp_path/'out/financial_panel.csv')
    assert frame.available_date.iloc[0]=='2026-10-20'
    assert frame.roe.iloc[0]==.1 and pd.isna(frame.debt_ratio.iloc[0])
    assert not proof['original_vintages_verified']
    with pytest.raises(ValueError,match='new observed'):
        normalize_observed_financials(source,pd.bdate_range('2026-08-01','2026-11-01'),tmp_path/'out')


def test_capture_after_calendar_is_unknown_not_backfilled(tmp_path):
    source=raw_capture(tmp_path,'2026-10-20T06:00:00Z')
    proof=normalize_observed_financials(source,pd.bdate_range('2026-08-01','2026-09-01'),tmp_path/'out')
    assert proof['visible_records']==0
    assert pd.read_csv(tmp_path/'out/financial_panel.csv').available_date.isna().all()


def test_observed_sources_join_through_existing_dependency_pipeline(tmp_path):
    source=raw_capture(tmp_path,'2026-10-20T06:00:00Z')
    sessions=pd.bdate_range('2026-08-01','2026-11-01')
    normalize_observed_financials(source,sessions,tmp_path/'earnings')
    balance_raw=tmp_path/'balance_raw'
    def fetch(date,page):
        return dict(success=True,result=dict(count=1,pages=1,data=[dict(SECUCODE='600000.SH',SECURITY_CODE='600000',REPORT_DATE=date,NOTICE_DATE='2026-08-20',TOTAL_ASSETS=100,TOTAL_LIABILITIES=50)]))
    manifest=download_balance_batch(balance_raw,'2026-06-30','2026-06-30',fetch=fetch,delay=0)
    manifest['quarters']['2026-06-30']['complete_observed_at_utc']='2026-10-20T06:00:00Z'
    manifest['updated_at_utc']='2026-10-20T06:00:00Z'
    (balance_raw/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')
    calendar=tmp_path/'calendar.csv'
    pd.DataFrame(dict(calendar_date=sessions,is_trading_day='1')).to_csv(calendar,index=False)
    normalize_balance_batch(balance_raw,calendar,tmp_path/'balances')
    proof=combine_financial_files(tmp_path/'earnings',tmp_path/'balances',tmp_path/'combined')
    assert proof['records']==1 and proof['debt_records']==1
    panel=pd.read_csv(tmp_path/'combined/financial_panel.csv')
    assert panel.available_date.iloc[0]=='2026-10-20' and panel.debt_ratio.iloc[0]==.5

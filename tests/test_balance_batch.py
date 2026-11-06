import copy
import json
import pandas as pd
import pytest
from ashare_mvp.balance_batch import download_balance_batch, gate_first_observed, normalize_balance_batch


def payload(count=501):
    rows=[dict(SECUCODE=f'{600000+index:06d}.SH',SECURITY_CODE=f'{600000+index:06d}',REPORT_DATE='2026-06-30',NOTICE_DATE='2026-08-20',TOTAL_ASSETS=100,TOTAL_LIABILITIES=50) for index in range(count)]
    def fetch(date,page):
        return dict(success=True,result=dict(count=count,pages=(count+499)//500,data=copy.deepcopy(rows[(page-1)*500:page*500])))
    return fetch


def test_pagination_and_first_observed_clocks_not_fake_update_date(tmp_path):
    out=tmp_path/'data'
    result=download_balance_batch(out,'2026-06-30','2026-06-30',fetch=payload(),delay=0)
    assert result['status']=='downloaded_raw'
    info=result['quarters']['2026-06-30']
    assert info['rows']==501 and info['pages']==2
    assert not info['provider_update_clock_supplied'] and not info['original_vintages_verified']
    assert info['first_observed_at_utc']<=info['complete_observed_at_utc']
    assert 'UPDATE_DATE' not in (out/'2026Q2/records.csv').read_text()
    assert download_balance_batch(out,'2026-06-30','2026-06-30',resume=True,fetch=lambda *_:pytest.fail('cached data fetched'),delay=0)['status']=='downloaded_raw'


@pytest.mark.parametrize('failure',['count','short_page','duplicate','report_period','publication','identity','schema'])
def test_invalid_source_is_retained_as_failure_not_completed(tmp_path,failure):
    original=payload()
    def fetch(date,page):
        value=original(date,page); result=value['result']
        if page==2:
            if failure=='count':result['count']=502
            if failure=='short_page':result['data']=[]
            if failure=='duplicate':result['data'][0].update(SECUCODE='600000.SH',SECURITY_CODE='600000')
            if failure=='report_period':result['data'][0]['REPORT_DATE']='2026-03-31'
            if failure=='publication':result['data'][0]['NOTICE_DATE']=None
            if failure=='identity':result['data'][0]['SECURITY_CODE']='600999'
            if failure=='schema':
                for row in result['data']:del row['NOTICE_DATE']
        return value
    result=download_balance_batch(tmp_path/'data','2026-06-30','2026-06-30',fetch=fetch,delay=0)
    assert result['status']=='partial' and result['completed_quarters']==0


def test_resume_refuses_changed_cached_file(tmp_path):
    out=tmp_path/'data'
    download_balance_batch(out,'2026-06-30','2026-06-30',fetch=payload(1),delay=0)
    (out/'2026Q2/records.csv').write_text('changed')
    with pytest.raises(ValueError,match='cached evidence'):
        download_balance_batch(out,'2026-06-30','2026-06-30',resume=True,fetch=payload(1),delay=0)


def test_observation_gate_uses_china_date_and_preserves_unknown():
    sessions=pd.to_datetime(['2026-10-20','2026-10-21','2026-10-22'])
    frame=pd.DataFrame(dict(available_date=pd.to_datetime(['2026-08-21',None,'2026-10-22'])))
    result=gate_first_observed(frame,'2026-10-20T18:00:00Z',sessions)
    assert result.available_date.iloc[0]==pd.Timestamp('2026-10-21')
    assert pd.isna(result.available_date.iloc[1])
    assert result.available_date.iloc[2]==pd.Timestamp('2026-10-22')
    assert not result.original_vintages_verified.any()
    later=gate_first_observed(frame,'2026-11-01T10:00:00Z',sessions)
    assert later.available_date.isna().all()


def test_normalized_batch_has_actual_debt_and_no_invented_update_clock(tmp_path):
    source=tmp_path/'raw'
    manifest=download_balance_batch(source,'2026-06-30','2026-06-30',fetch=payload(1),delay=0)
    manifest['quarters']['2026-06-30']['complete_observed_at_utc']='2026-10-20T06:00:00Z'
    (source/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')
    calendar=tmp_path/'calendar.csv'
    pd.DataFrame(dict(calendar_date=pd.bdate_range('2026-08-01','2026-11-01'),is_trading_day='1')).to_csv(calendar,index=False)
    proof=normalize_balance_batch(source,calendar,tmp_path/'normalized')
    panel=pd.read_csv(tmp_path/'normalized/balance_panel.csv')
    assert proof['rows']==1 and panel.debt_ratio.iloc[0]==.5
    assert panel.available_date.iloc[0]=='2026-10-20' and panel.provider_update_date.isna().all()
    assert panel.symbol.iloc[0]=='sh.600000'


def test_non_ashare_receipt_is_explicitly_audited_not_added_to_factor_pool(tmp_path):
    source=tmp_path/'raw'
    original=payload(2)
    def fetch(date,page):
        value=original(date,page)
        value['result']['data'][1].update(SECUCODE='689009.SH',SECURITY_CODE='689009')
        return value
    manifest=download_balance_batch(source,'2026-06-30','2026-06-30',fetch=fetch,delay=0)
    manifest['quarters']['2026-06-30']['complete_observed_at_utc']='2026-10-20T06:00:00Z'
    (source/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')
    calendar=tmp_path/'calendar.csv'
    pd.DataFrame(dict(calendar_date=pd.bdate_range('2026-08-01','2026-11-01'),is_trading_day='1')).to_csv(calendar,index=False)
    proof=normalize_balance_batch(source,calendar,tmp_path/'normalized')
    assert proof['rows']==1 and proof['excluded_rows']==1
    assert pd.read_csv(tmp_path/'normalized/excluded_securities.csv').SECUCODE.tolist()==['689009.SH']

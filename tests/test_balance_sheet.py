import pandas as pd
import pytest
from ashare_mvp.balance_sheet import normalize_balance_records


def record():
    return dict(SECURITY_CODE="600519", REPORT_DATE="2019-12-31", NOTICE_DATE="2020-04-22", UPDATE_DATE="2021-03-31", TOTAL_ASSETS=100, TOTAL_LIABILITIES=140)


def test_restatement_not_backfilled_and_insolvent_ratio_preserved():
    result=normalize_balance_records([record()], "sh.600519", pd.bdate_range("2020-01-01", "2021-04-02"))
    assert result.available_date.iloc[0] == pd.Timestamp("2021-04-01")
    assert result.debt_ratio.iloc[0] == 1.4
    early=normalize_balance_records([record()], "sh.600519", pd.bdate_range("2020-01-01", "2020-12-31"))
    assert pd.isna(early.available_date.iloc[0])


def test_missing_debt_not_zero_and_wrong_identity_rejected():
    row=record(); row['TOTAL_LIABILITIES']=None
    result=normalize_balance_records([row], "sh.600519", pd.bdate_range("2020-01-01", "2021-04-02"))
    assert pd.isna(result.debt_ratio.iloc[0])
    with pytest.raises(ValueError,match="identity"):
        normalize_balance_records([row], "sz.000001", pd.bdate_range("2020-01-01", "2021-04-02"))


def test_download_panel_verifies_all_raw_cache_and_preserves_clock(tmp_path):
    import json
    from ashare_mvp.balance_download import download_balance_sheets
    from ashare_mvp.balance_sheet import normalize_balance_download
    row=record()
    def fetch(endpoint,params):
        if endpoint=='Index': return b'<input id="hidctype" value="4">'
        if endpoint=='zcfzbDateAjaxNew': return json.dumps({'data':[{'REPORT_DATE':row['REPORT_DATE']}]}).encode()
        return json.dumps({'data':[row]}).encode()
    source=tmp_path/'raw'
    download_balance_sheets(['sh.600519'],'2019-01-01','2020-12-31',source,fetch=fetch,delay=0)
    calendar=tmp_path/'calendar.csv'
    pd.DataFrame({'calendar_date':pd.bdate_range('2020-01-01','2021-04-02'), 'is_trading_day':1}).to_csv(calendar,index=False)
    summary=normalize_balance_download(source,calendar,tmp_path/'panel')
    assert summary['records']==1 and summary['visible_records']==1 and not summary['model_ready']
    frame=pd.read_csv(tmp_path/'panel/balance_panel.csv')
    assert frame.available_date.iloc[0]=='2021-04-01'
    meta=json.loads((source/'manifest.json').read_text())
    index_request=next(q for q in meta['queries'].values() if q['endpoint']=='Index')
    (source/index_request['file']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='raw hash mismatch'):
        normalize_balance_download(source,calendar,tmp_path/'tampered')

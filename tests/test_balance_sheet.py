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


def test_wide_provider_rows_reduce_memory_without_losing_visibility_clocks():
    from ashare_mvp.balance_sheet import _retain_balance_fields
    row=record(); row["EITIME"]="2021-04-01"
    row.update({f"UNUSED_{i}":i for i in range(500)})
    retained=_retain_balance_fields([row])
    assert len(retained[0])==7 and len(row)==507
    calendar=pd.bdate_range("2020-01-01","2021-04-05")
    pd.testing.assert_frame_equal(normalize_balance_records([row],"sh.600519",calendar),normalize_balance_records(retained,"sh.600519",calendar))
    assert normalize_balance_records(retained,"sh.600519",calendar).available_date.iloc[0]==pd.Timestamp("2021-04-02")


def legacy_snapshot(tmp_path):
    import json,hashlib
    source=tmp_path/'legacy';source.mkdir()
    meta=dict(schema='issuer_balance_raw_v1',status='partial',request={'start':'2019-01-01','end':'2020-12-31'},queries={},securities={})
    for symbol,status in [('sh.600519','ok'),('sz.000004','failed')]:
        code=symbol.split('.')[1];exchange=symbol[:2].upper()
        row={**record(),'SECURITY_CODE':code,'SECUCODE':code+'.'+exchange}
        raw=json.dumps({'success':True,'result':{'count':1,'pages':1,'data':[row]}}).encode()
        filename=code+'.raw';(source/filename).write_bytes(raw)
        meta['queries'][symbol]={'status':'ok','endpoint':'legacyBalances','file':filename,'sha256':hashlib.sha256(raw).hexdigest(),'params':{'filter':'(SECUCODE="'+code+'.'+exchange+'")','p':'1'}}
        meta['securities'][symbol]={'status':status,'records':1} if status=='ok' else {'status':'failed','error':'later request timed out'}
    (source/'manifest.json').write_text(json.dumps(meta))
    calendar=tmp_path/'calendar.csv';pd.DataFrame({'calendar_date':pd.bdate_range('2020-01-01','2021-04-02'),'is_trading_day':1}).to_csv(calendar,index=False)
    return source,calendar,meta


def test_failed_legacy_issuer_keeps_raw_cache_but_does_not_abort_complete_peers(tmp_path):
    from ashare_mvp.balance_sheet import normalize_balance_download
    source,calendar,_=legacy_snapshot(tmp_path)
    before=(source/'000004.raw').read_bytes()
    result=normalize_balance_download(source,calendar,tmp_path/'out')
    panel=pd.read_csv(tmp_path/'out/balance_panel.csv')
    rejected=pd.read_csv(tmp_path/'out/rejected_securities.csv').set_index('symbol')
    assert panel.symbol.tolist()==['sh.600519'] and result['partial_source'] and not result['model_ready']
    assert rejected.loc['sz.000004','reason']=='unfinished raw acquisition'
    assert (source/'000004.raw').read_bytes()==before


def test_failed_legacy_raw_cache_still_requires_valid_hash(tmp_path):
    from ashare_mvp.balance_sheet import normalize_balance_download
    source,calendar,_=legacy_snapshot(tmp_path);(source/'000004.raw').write_bytes(b'changed')
    with pytest.raises(ValueError,match='raw hash mismatch'):normalize_balance_download(source,calendar,tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_completed_legacy_issuer_count_mismatch_still_fails(tmp_path):
    import json
    from ashare_mvp.balance_sheet import normalize_balance_download
    source,calendar,meta=legacy_snapshot(tmp_path);meta['securities']['sh.600519']['records']=2
    (source/'manifest.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError,match='legacy balance total count mismatch'):
        normalize_balance_download(source,calendar,tmp_path/'out')
    assert not (tmp_path/'out').exists()

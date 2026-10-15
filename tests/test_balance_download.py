import json
from ashare_mvp.balance_download import download_balance_sheets


def fetch(endpoint, params):
    if endpoint == 'Index':
        return b'<input id="hidctype" value="4">'
    if endpoint == 'zcfzbDateAjaxNew':
        return json.dumps({'data':[{'REPORT_DATE':'2020-03-31','NOTICE_DATE':'2020-04-30','UPDATE_DATE':'2020-04-30','TOTAL_ASSETS':100,'TOTAL_LIABILITIES':20}]}).encode()
    return json.dumps({'data':[{'SECURITY_CODE':'600519','REPORT_DATE':'2020-03-31','TOTAL_ASSETS':100,'TOTAL_LIABILITIES':20}]}).encode()


def test_resume_verifies_cached_reports_without_network(tmp_path):
    args=(['sh.600519'],'2020-01-01','2020-12-31',tmp_path)
    result=download_balance_sheets(*args,fetch=fetch,delay=0)
    assert result['status']=='downloaded_raw' and result['securities']['sh.600519']['records']==1
    def no_network(*args):
        raise AssertionError('unexpected network')
    assert download_balance_sheets(*args,resume=True,fetch=no_network,delay=0)['status']=='downloaded_raw'
    cache=next(tmp_path.glob('*.raw')); cache.write_bytes(b'changed')
    result=download_balance_sheets(*args,resume=True,fetch=no_network,delay=0)
    assert result['status']=='partial' and 'hash mismatch' in result['securities']['sh.600519']['error']


def test_missing_report_not_silently_accepted(tmp_path):
    def missing(endpoint,params):
        return b'{"data":[]}' if endpoint=='zcfzbAjaxNew' else fetch(endpoint,params)
    result=download_balance_sheets(['sh.600519'],'2020-01-01','2020-12-31',tmp_path,fetch=missing,delay=0)
    assert result['status']=='partial' and result['completed_securities']==0


def test_gzip_response_and_cached_resume(tmp_path):
    import gzip
    def compressed(endpoint,params):
        return gzip.compress(fetch(endpoint,params))
    args=(['sh.600519'],'2020-01-01','2020-12-31',tmp_path)
    assert download_balance_sheets(*args,fetch=compressed,delay=0)['status']=='downloaded_raw'
    assert download_balance_sheets(*args,resume=True,fetch=compressed,delay=0)['status']=='downloaded_raw'


def test_cached_unusable_response_is_refreshed_and_original_retained(tmp_path):
    def invalid(endpoint,params):
        return b'{"$type":"1"}' if endpoint=='zcfzbDateAjaxNew' else fetch(endpoint,params)
    args=(['sh.600519'],'2020-01-01','2020-12-31',tmp_path)
    assert download_balance_sheets(*args,fetch=invalid,delay=0)['status']=='partial'
    meta=json.loads((tmp_path/'manifest.json').read_text())
    original=meta['queries']['sh.600519:dates']['file']
    assert download_balance_sheets(*args,resume=True,fetch=fetch,delay=0)['status']=='downloaded_raw'
    assert (tmp_path/original).read_bytes()==b'{"$type":"1"}'


def test_fallback_count_identity_and_truncation(tmp_path):
    def fallback(endpoint,params):
        if endpoint=='zcfzbDateAjaxNew': return b'{"$type":"1"}'
        if endpoint=='legacyBalances':
            return json.dumps({'success':True,'result':{'count':1,'pages':1,'data':[{'SECURITY_CODE':'600519','SECUCODE':'600519.SH','REPORT_DATE':'2020-03-31','NOTICE_DATE':'2020-04-30','UPDATE_DATE':'2020-04-30','TOTAL_ASSETS':100,'TOTAL_LIABILITIES':20}]}}).encode()
        return fetch(endpoint,params)
    args=(['sh.600519'],'2020-01-01','2020-12-31',tmp_path/'good')
    result=download_balance_sheets(*args,fetch=fallback,delay=0)
    assert result['status']=='downloaded_raw' and result['securities']['sh.600519']['source_endpoint']=='legacyBalances'
    import pandas as pd
    from ashare_mvp.balance_sheet import normalize_balance_download
    calendar=tmp_path/'calendar.csv'
    pd.DataFrame({'calendar_date':pd.bdate_range('2020-01-01','2020-12-31'),'is_trading_day':1}).to_csv(calendar,index=False)
    assert normalize_balance_download(tmp_path/'good',calendar,tmp_path/'panel')['records']==1
    def truncated(endpoint,params):
        raw=fallback(endpoint,params)
        if endpoint=='legacyBalances':
            payload=json.loads(raw); payload['result']['count']=2; return json.dumps(payload).encode()
        return raw
    result=download_balance_sheets(['sh.600519'],'2020-01-01','2020-12-31',tmp_path/'bad',fetch=truncated,delay=0)
    assert result['status']=='partial' and 'truncated' in result['securities']['sh.600519']['error']


def test_unchanged_cached_issuers_do_not_rewrite_full_manifest(tmp_path,monkeypatch):
    from ashare_mvp import balance_download
    args=(["sh.600519"],"2020-01-01","2020-12-31",tmp_path)
    download_balance_sheets(*args,fetch=fetch,delay=0)
    original=balance_download.replace_checkpoint; writes=[]
    def track(source,target):
        writes.append(1); return original(source,target)
    monkeypatch.setattr(balance_download,"replace_checkpoint",track)
    def no_network(*args): raise AssertionError("cached request used network")
    result=download_balance_sheets(*args,resume=True,fetch=no_network,delay=0)
    assert result["status"]=="downloaded_raw"
    assert len(writes)==2  # running and final state; no unchanged issuer write


def test_transient_report_transport_error_does_not_drop_issuer(tmp_path,monkeypatch):
    from ashare_mvp import network_retry
    monkeypatch.setattr(network_retry.time,"sleep",lambda _:None)
    calls=[]
    def flaky(endpoint,params):
        if endpoint=="zcfzbAjaxNew":
            calls.append(1)
            if len(calls)==1: raise TimeoutError("temporary transport failure")
        return fetch(endpoint,params)
    result=download_balance_sheets(["sh.600519"],"2020-01-01","2020-12-31",tmp_path,fetch=flaky,delay=0)
    assert result["status"]=="downloaded_raw" and result["completed_securities"]==1
    assert len(calls)==2

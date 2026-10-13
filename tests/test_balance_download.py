import json
from ashare_mvp.balance_download import download_balance_sheets


def fetch(endpoint, params):
    if endpoint == 'Index':
        return b'<input id="hidctype" value="4">'
    if endpoint == 'zcfzbDateAjaxNew':
        return json.dumps({'data':[{'REPORT_DATE':'2020-03-31'}]}).encode()
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

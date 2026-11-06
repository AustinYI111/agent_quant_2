from copy import deepcopy
from decimal import Decimal
import json
from urllib.parse import urlencode

import pytest

from ashare_mvp.exchange_rights_quote import load_exchange_quote,reconcile_exchange_quote
from ashare_mvp.rights_terms import rights_reference
from ashare_mvp.phase2_protocol import sha256
from test_rights_wallet import event


FIELDS=['CODE','ABBR','RECDATE','BONRATE','RIGRATE','RIGPRICE','TRANRATE','PERFEE','EXDATE','PREPRICE','EXPRICE','FIRDATE']
HEADINGS=['股票代码','股票简称','股权登记日','送股比例','配股比例','配股价格','转配比例','每股转让费','除权基准日','除权前日收盘价','除权报价','股份上市日']
SQL='COMMON_SSE_SSGS_M_LC_RIGHTS_C'
SCRIPT_URL='https://www.sse.com.cn/xhtml/home/2021public/querySearch/search_publication_branch_2021.js'


def fixtures(root):
    e=event(symbol='sh.600001');e['exchange_ratio']=Decimal('.095')
    row=dict(CODE='600001',ABBR='示例',RECDATE='20240103',BONRATE='0.0000',RIGRATE='0.1000',
        RIGPRICE='10.00',TRANRATE='0.0000',PERFEE='0.00',EXDATE='20240112',PREPRICE='20.00',EXPRICE='19.09',FIRDATE='20240118')
    body=dict(actionErrors=[],fieldErrors={},sqlId=SQL,isPagination='true',result=[row],
        pageHelp=dict(pageNo=1,pageSize=25,pageCount=1,total=1,data=[deepcopy(row)]))
    params={'sqlId':SQL,'isPagination':'true','pageHelp.pageSize':25,'pageHelp.pageNo':1,
        'pageHelp.beginPage':1,'pageHelp.cacheSize':1,'pageHelp.endPage':1,'RECDATE':'202401'}
    url='https://query.sse.com.cn/commonQuery.do?'+urlencode(params)
    capture=dict(schema='sse_monthly_rights_json_capture_v1',source_url=url,final_url=url,params=params,
        started_at_utc='2026-01-01T00:00:00Z',observed_at_utc='2026-01-01T00:01:00Z',
        historical_original_vintage_verified=False,events_applied=False)
    script=('initTableSeven: function () {var theadList = '+json.dumps(HEADINGS,ensure_ascii=False)+';}\n'
        'detailTableSeven: function (pageIndex) {var rowData = '+json.dumps(FIELDS)+';}\n'+SQL)
    (root/'definition.js').write_text(script,encoding='utf8')
    definition_receipt=dict(source_url=SCRIPT_URL,sha256=sha256(root/'definition.js'),observed_at_utc='2026-01-01T00:00:00Z')
    (root/'definition_capture.json').write_text(json.dumps(definition_receipt),encoding='utf8')
    item=dict(schema='reviewed_sse_monthly_rights_json_quote_v1',reviewed=True,
        retrospective_reference_only=True,historical_availability_asserted=False,
        source_file='response.json',source_url=url,capture_file='capture.json',
        observed_at_utc='2026-01-01T00:01:00Z',reviewed_at_utc='2026-01-01T00:02:00Z',
        field_definition=dict(script_file='definition.js',script_sha256=sha256(root/'definition.js'),
            capture_file='definition_capture.json',capture_sha256=sha256(root/'definition_capture.json')))
    save(root,body,capture,item)
    return e,body,capture,item


def save(root,body,capture,item):
    (root/'response.json').write_text(json.dumps(body,ensure_ascii=False),encoding='utf8')
    capture.update(sha256=sha256(root/'response.json'),bytes=(root/'response.json').stat().st_size)
    item['source_sha256']=capture['sha256']
    (root/'capture.json').write_text(json.dumps(capture),encoding='utf8')
    item['capture_sha256']=sha256(root/'capture.json')


def test_explicit_json_quote_is_bound_and_formula_difference_preserved(tmp_path):
    e,b,c,item=fixtures(tmp_path)
    proof,sources=load_exchange_quote(item,e,tmp_path)
    assert proof['source_format']=='sse_monthly_json' and len(sources)==4
    e['reviewed_exchange_quote']=proof
    formula=rights_reference(20,19.09,e)
    assert not formula['matches']
    match=reconcile_exchange_quote(20,19.09,e,formula)
    assert match['matches'] and match['capital_count_formula_diagnostic']==formula
    assert not reconcile_exchange_quote(20.01,19.09,e,formula)['matches']
    assert not reconcile_exchange_quote(20,19.10,e,formula)['matches']
    assert e['offer_ratio']==Decimal('.1') and e['subscription_price']==Decimal('10')


@pytest.mark.parametrize('fault',['raw_hash','capture_hash','host','query_month','duplicate_query','query_sql',
    'clock','future_review','backwards','vintage','applied','sql_body','server_error','pagination_bool',
    'partial_page','page_number','page_data','wrong_period','wrong_code','duplicate_code','record_date',
    'ex_date','listing_date','offer_ratio','offer_price','bonus','transfer','fee','zero_price','nan_price',
    'subpenny','unreviewed','definition_hash','definition_heading','definition_order','definition_host','definition_future'])
def test_wrong_source_scope_field_mapping_or_quote_is_rejected(tmp_path,fault):
    e,b,c,item=fixtures(tmp_path)
    if fault=='raw_hash':(tmp_path/'response.json').write_text('{}')
    elif fault=='capture_hash':(tmp_path/'capture.json').write_text('{}')
    else:
        if fault=='host':item['source_url']=item['source_url'].replace('query.sse.com.cn','example.com');c['source_url']=c['final_url']=item['source_url']
        if fault=='query_month':c['params']['RECDATE']='202402';item['source_url']='https://query.sse.com.cn/commonQuery.do?'+urlencode(c['params']);c['source_url']=c['final_url']=item['source_url']
        if fault=='duplicate_query':item['source_url']+='&RECDATE=202401';c['source_url']=c['final_url']=item['source_url']
        if fault=='query_sql':item['source_url']=item['source_url'].replace(SQL,'OTHER');c['source_url']=c['final_url']=item['source_url'];c['params']['sqlId']='OTHER'
        if fault=='clock':item['observed_at_utc']='2026-01-01T00:00:59Z'
        if fault=='future_review':item['reviewed_at_utc']='2099-01-01T00:00:00Z'
        if fault=='backwards':c['started_at_utc']='2026-01-01T00:01:01Z'
        if fault=='vintage':c['historical_original_vintage_verified']=True
        if fault=='applied':c['events_applied']=True
        if fault=='sql_body':b['sqlId']='OTHER'
        if fault=='server_error':b['actionErrors']=['missing']
        if fault=='pagination_bool':b['pageHelp']['total']=True
        if fault=='partial_page':b['pageHelp'].update(total=26,pageCount=2)
        if fault=='page_number':b['pageHelp']['pageNo']=2
        if fault=='page_data':b['pageHelp']['data']=[]
        changes={'wrong_period':('RECDATE','20240203'),'wrong_code':('CODE','600002'),
            'record_date':('RECDATE','20240104'),'ex_date':('EXDATE','20240115'),'listing_date':('FIRDATE','20240119'),
            'offer_ratio':('RIGRATE','0.0950'),'offer_price':('RIGPRICE','11.00'),
            'bonus':('BONRATE','1.00'),'transfer':('TRANRATE','0.01'),'fee':('PERFEE','0.01'),
            'zero_price':('EXPRICE','0.00'),'nan_price':('PREPRICE','NaN'),'subpenny':('EXPRICE','19.091')}
        if fault in changes:
            k,v=changes[fault];b['result'][0][k]=v;b['pageHelp']['data']=deepcopy(b['result'])
        if fault=='duplicate_code':b['result']*=2;b['pageHelp'].update(total=2,data=deepcopy(b['result']))
        if fault=='unreviewed':item['reviewed']=False
        if fault.startswith('definition_'):
            if fault=='definition_hash':(tmp_path/'definition.js').write_text('changed')
            else:
                d=json.loads((tmp_path/'definition_capture.json').read_text())
                if fault=='definition_heading':s=(tmp_path/'definition.js').read_text(encoding='utf8').replace('除权报价','最新价');(tmp_path/'definition.js').write_text(s,encoding='utf8')
                if fault=='definition_order':s=(tmp_path/'definition.js').read_text(encoding='utf8').replace('"PREPRICE", "EXPRICE"','"EXPRICE", "PREPRICE"');(tmp_path/'definition.js').write_text(s,encoding='utf8')
                if fault=='definition_host':d['source_url']='https://example.com/definition.js'
                if fault=='definition_future':d['observed_at_utc']='2099-01-01T00:00:00Z'
                d['sha256']=sha256(tmp_path/'definition.js');item['field_definition']['script_sha256']=d['sha256']
                (tmp_path/'definition_capture.json').write_text(json.dumps(d),encoding='utf8')
                item['field_definition']['capture_sha256']=sha256(tmp_path/'definition_capture.json')
        save(tmp_path,b,c,item)
    with pytest.raises(ValueError):load_exchange_quote(item,e,tmp_path)


def test_later_complete_page_has_bound_global_row_number(tmp_path):
    e,b,c,item=fixtures(tmp_path)
    c['params'].update({'pageHelp.pageSize':1,'pageHelp.pageNo':2,'pageHelp.beginPage':2,'pageHelp.endPage':2})
    item['source_url']='https://query.sse.com.cn/commonQuery.do?'+urlencode(c['params'])
    c['source_url']=c['final_url']=item['source_url']
    b['pageHelp'].update(pageNo=2,pageSize=1,pageCount=2,total=2)
    save(tmp_path,b,c,item)
    proof,_=load_exchange_quote(item,e,tmp_path)
    assert proof['row_number']==2 and proof['ex_reference']=='19.09'

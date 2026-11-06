from copy import deepcopy
from decimal import Decimal
from datetime import datetime,timezone
import json
import pandas as pd
import pytest
from ashare_mvp.quote_basis_audit import FIELDS,parse_raw_history,compare_change_bases,audit_captured_history
from ashare_mvp.disk_market import build_quote_store
from ashare_mvp.phase2_protocol import sha256


def inputs():
    params=dict(secid='1.601555',fields2=FIELDS,fqt='0',klt='101',beg='20211214',end='20211224')
    body=dict(rc=0,data=dict(code='601555',market=1,decimal=2,preKPrice=8.86,klines=[
        '2021-12-14,8.76,8.87,8.93,8.73,1024367,905003568,2.26,0.11,0.01,2.64',
        '2021-12-23,8.55,8.68,8.93,8.44,1201788,1033315872,5.52,-2.14,-0.19,2.40',
        '2021-12-24,8.62,8.72,8.85,8.59,717253,625519504,3.00,0.46,0.04,1.43']))
    original={d:dict(open=op,close=cl,preclose=pre) for d,op,cl,pre in [
        ('2021-12-14',8.76,8.87,8.86),('2021-12-23',8.55,8.68,8.48),('2021-12-24',8.62,8.72,8.68)]}
    return params,body,original


def test_unadjusted_change_does_not_independently_establish_ex_rights_reference():
    params,body,original=inputs();symbol,anchor,rows=parse_raw_history(body,params)
    records=compare_change_bases(rows,anchor,original)
    assert symbol=='sh.601555' and all(r['open_matches'] and r['close_matches'] for r in records)
    ex=records[1]
    assert ex['raw_change_matches'] and ex['raw_pct_matches'] and not ex['reference_pct_matches']
    assert ex['close_minus_change']=='8.87' and ex['original_reference_preclose']=='8.48'
    assert ex['recomputed_reference_pct']=='2.36' and ex['reported_pct']=='-2.14'
    assert not ex['independent_ex_reference_supplied']


@pytest.mark.parametrize('fault',['adjusted','intraday','wrong_fields','wrong_code','missing_anchor','duplicate','nan','bad_ohlc','negative_volume'])
def test_malformed_or_noncomparable_contracts_rejected(fault):
    p,b,_=inputs()
    if fault=='adjusted':p['fqt']='1'
    if fault=='intraday':p['klt']='5'
    if fault=='wrong_fields':p['fields2']='f51,f52'
    if fault=='wrong_code':b['data']['code']='000001'
    if fault=='missing_anchor':b['data']['preKPrice']=None
    if fault=='duplicate':b['data']['klines'].append(b['data']['klines'][0])
    if fault in ['nan','bad_ohlc','negative_volume']:
        fields=b['data']['klines'][0].split(',')
        if fault=='nan':fields[2]='NaN'
        if fault=='bad_ohlc':fields[3]='1'
        if fault=='negative_volume':fields[5]='-1'
        b['data']['klines'][0]=','.join(fields)
    with pytest.raises(ValueError):parse_raw_history(b,p)


def test_actual_price_disagreement_is_not_hidden_by_change_basis_classification():
    p,b,o=inputs();_,anchor,rows=parse_raw_history(b,p);o['2021-12-23']['close']=8.69
    result=compare_change_bases(rows,anchor,o)
    assert result[1]['close_matches'] is False and result[1]['raw_pct_matches'] is True


def test_absent_original_quote_cannot_be_filled_or_assumed_zero():
    p,b,o=inputs();_,anchor,rows=parse_raw_history(b,p);del o['2021-12-23']
    with pytest.raises(ValueError,match='missing original'):compare_change_bases(rows,anchor,o)


def test_readonly_real_sqlite_audit_retains_suspension_coverage_and_original_hash(tmp_path):
    params,body,original=inputs();cal=pd.bdate_range('2021-12-13','2021-12-24')
    records=[]
    for date in cal:
        key=str(date.date());active=key in original or key=='2021-12-13'
        bar=original.get(key,dict(open=0.,close=8.87,preclose=8.87))
        if key=='2021-12-13':bar=dict(open=8.8,close=8.86,preclose=8.8)
        records.append(dict(date=date,symbol='sh.601555',**bar,tradestatus='1' if active else '0',terminal_date=None))
    pd.DataFrame(records).to_csv(tmp_path/'raw.csv',index=False)
    build_quote_store(tmp_path/'raw.csv',cal,tmp_path/'quotes',chunksize=3)
    source=tmp_path/'capture';source.mkdir();(source/'response.json').write_text(json.dumps(body))
    m=dict(status='raw_captured',response_sha256=sha256(source/'response.json'),observed_at_utc=datetime.now(timezone.utc).isoformat(),parameters=params)
    (source/'capture.json').write_text(json.dumps(m))
    before=sha256(tmp_path/'quotes/quotes.sqlite')
    proof=audit_captured_history(tmp_path/'quotes',source,tmp_path/'audit')
    assert proof['rows']==3 and proof['exact_active_date_coverage'] and proof['all_open_close_prices_match']
    assert proof['all_reported_changes_match_raw_preceding_close'] and not proof['reference_fault_waived']
    assert sha256(tmp_path/'quotes/quotes.sqlite')==before

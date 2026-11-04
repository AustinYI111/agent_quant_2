from copy import deepcopy
import json

import pytest

from test_rights_wallet import event, CAL
from ashare_mvp.rights_terms import normalize_rights_case,serialize_case,load_rights_evidence
from ashare_mvp.phase2_protocol import sha256


def scoped_spec():
    s=serialize_case(event())
    s.update(ordinary_share_scope='a_shares_only',holder_scope='ordinary_non_locked_a_shareholder',
        other_ordinary_shares=200,issuer_ordinary_shares_before=1200,issuer_ordinary_shares_after=1300)
    return s


def test_a_only_counts_reconcile_to_total_without_diluting_a_reference_ratio():
    e=normalize_rights_case(scoped_spec(),CAL)
    assert str(e['exchange_ratio'])=='0.1'
    assert e['registered_ordinary_shares']==1000 and e['post_issue_ordinary_shares']==1100
    assert e['issuer_ordinary_shares_after']==1300 and e['offer_ratio']==e['exchange_ratio']


@pytest.mark.parametrize('key,value',[
    ('ordinary_share_scope','all_share_classes'),('ordinary_share_scope',None),
    ('holder_scope','controlling_shareholder'),('holder_scope',None),
    ('other_ordinary_shares',201),('other_ordinary_shares',-1),('other_ordinary_shares',True),
    ('issuer_ordinary_shares_before',1201),('issuer_ordinary_shares_after',1301),
    ('issuer_ordinary_shares_before',1200.0),('issuer_ordinary_shares_after',None),
    ('registered_ordinary_shares',1200),('post_issue_ordinary_shares',1300)])
def test_ambiguous_class_or_inconsistent_issuer_capital_is_rejected(key,value):
    s=scoped_spec();s[key]=value
    with pytest.raises(ValueError):normalize_rights_case(s,CAL)


def primary_sources(root):
    names={'offer':['record_date','subscription_start','subscription_end','offer_ratio','subscription_price','expected_result_date'],
        'result':['outcome','ex_date','registered_ordinary_shares','issued_ordinary_shares','aggregate_subscription_cash','ordinary_share_scope','quoted_exchange_ratio_per_10'],
        'listing':['tradable_from','post_issue_ordinary_shares','holder_scope','ordinary_share_scope','other_ordinary_shares','issuer_ordinary_shares_before','issuer_ordinary_shares_after']}
    titles={'offer':'A股配股发行公告','result':'A股配股发行结果公告','listing':'A股配股股份变动及获配股票上市公告书'}
    dates={'offer':'2024-01-02','result':'2024-01-12','listing':'2024-01-16'}
    records={};notices=[]
    for i,(role,fields) in enumerate(names.items(),1):
        file=root/(role+'.PDF');file.write_bytes(b'%PDF-scoped-test-only')
        url=f'https://static.cninfo.com.cn/finalpage/{dates[role]}/{i}.PDF'
        observed='2024-02-01T00:00:00Z'
        notices.append(dict(url=url,sha256=sha256(file),observed_at_utc=observed,title=titles[role]))
        records[role]=dict(source_file=file.name,source_sha256=sha256(file),source_url=url,
            capture_file='capture.json',observed_at_utc=observed,reviewed_at_utc='2024-02-01T01:00:00Z',
            reviewed=True,reviewed_pages=[1],parameter_pages={f:1 for f in fields})
    capture=dict(code='002142',notices=notices)
    return records,capture


@pytest.mark.parametrize('fault',[None,'H_title','missing_scope_page','missing_lock_page','missing_other_page'])
def test_a_scope_requires_a_offering_identity_and_page_evidence(tmp_path,fault):
    project=tmp_path/'project';project.mkdir();raw=tmp_path/'raw';raw.mkdir()
    records,capture=primary_sources(raw)
    if fault=='H_title':capture['notices'][1]['title']='H股配股发行结果公告'
    if fault=='missing_scope_page':del records['result']['parameter_pages']['ordinary_share_scope']
    if fault=='missing_lock_page':del records['listing']['parameter_pages']['holder_scope']
    if fault=='missing_other_page':del records['listing']['parameter_pages']['other_ordinary_shares']
    (raw/'capture.json').write_text(json.dumps(capture),encoding='utf8')
    for r in records.values():r['capture_sha256']=sha256(raw/'capture.json')
    config=dict(schema='ordinary_rights_evidence_v1',original_vintages_verified=False,
        result_preopen_timestamp_verified=False,events=[dict(terms=scoped_spec(),sources=records)])
    (project/'terms.json').write_text(json.dumps(config),encoding='utf8')
    if fault:
        with pytest.raises(ValueError):load_rights_evidence('terms.json',project,raw,CAL)
    else:
        events,proof=load_rights_evidence('terms.json',project,raw,CAL)
        assert len(events)==1 and len(proof['evidence_source_hashes'])==4

import json
from decimal import Decimal

import pytest

from ashare_mvp.exchange_rights_quote import load_exchange_quote, reconcile_exchange_quote
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.rights_terms import rights_reference, normalize_rights_case
from test_rights_wallet import event, CAL


def source(tmp_path):
    e = event(symbol='sh.600001')
    (tmp_path/'original.pdf').write_bytes(b'%PDF-1.4\nfixture source')
    url = 'https://www.sse.com.cn/aboutus/publication/monthly/documents/c/123/files/'+'a'*32+'.pdf'
    c = dict(schema='primary_exchange_monthly_capture_v1',source_url=url,final_url=url,
        sha256=sha256(tmp_path/'original.pdf'),bytes=(tmp_path/'original.pdf').stat().st_size,
        started_at_utc='2026-01-01T00:00:00Z',completed_at_utc='2026-01-01T00:01:00Z',
        historical_availability_asserted=False,used_for_historical_selection=False,page_count=3,
        extracted_pages={'2':'股票代码 股权登记日 配股比例 配股价格\n600001 示例 2024.01.03 0.0000 0.1000 10.00',
                         '3':'除权基准日 除权前日收盘价 除权报价 股份上市日\n0.0000 0.00 2024.01.12 20.00 19.09 2024.01.18'})
    item=dict(schema='reviewed_sse_monthly_rights_quote_v1',reviewed=True,
        retrospective_reference_only=True,historical_availability_asserted=False,
        source_file='original.pdf',source_sha256=c['sha256'],source_url=url,
        capture_file='capture.json',observed_at_utc=c['completed_at_utc'],
        reviewed_at_utc='2026-01-01T00:02:00Z',reviewed_pages=[2,3])
    save(tmp_path,c,item)
    return e,c,item


def save(root,c,item):
    (root/'capture.json').write_text(json.dumps(c),encoding='utf8')
    item['capture_sha256']=sha256(root/'capture.json')


def test_source_bound_quotation_does_not_change_offer_economics(tmp_path):
    e,c,item=source(tmp_path)
    e['exchange_ratio']=Decimal('.095')
    q,hashes=load_exchange_quote(item,e,tmp_path)
    e['reviewed_exchange_quote']=q
    diagnostic=rights_reference(20,19.09,e)
    assert not diagnostic['matches']
    proof=reconcile_exchange_quote(20,19.09,e,diagnostic)
    assert proof['matches'] and proof['capital_count_formula_diagnostic']==diagnostic
    assert e['offer_ratio']==Decimal('.1') and e['issued_ordinary_shares']==100
    assert len(hashes)==2 and q['row_number']==1
    assert not reconcile_exchange_quote(20.01,19.09,e,diagnostic)['matches']
    assert not reconcile_exchange_quote(20,19.10,e,diagnostic)['matches']


@pytest.mark.parametrize('kind',['pdf','capture','url','clock','future','headings','issuer','date',
                                'listing','ratio','rows','pages','availability','row_order'])
def test_changed_or_ambiguous_primary_quote_refused(tmp_path,kind):
    e,c,item=source(tmp_path)
    if kind=='pdf':(tmp_path/'original.pdf').write_bytes(b'%PDF-changed')
    elif kind=='capture':(tmp_path/'capture.json').write_text('{}')
    elif kind=='url':item['source_url']='https://example.com/fake.pdf'
    elif kind=='clock':item['observed_at_utc']='2026-01-01T00:00:59Z'
    elif kind=='future':item['reviewed_at_utc']='2099-01-01T00:00:00Z'
    elif kind=='pages':item['reviewed_pages']=[True,3]
    elif kind=='availability':item['historical_availability_asserted']=True
    else:
        if kind=='headings':c['extracted_pages']['3']=c['extracted_pages']['3'].replace('除权报价','价格')
        if kind=='issuer':c['extracted_pages']['2']=c['extracted_pages']['2'].replace('600001','600002')
        if kind=='date':c['extracted_pages']['3']=c['extracted_pages']['3'].replace('2024.01.12','2024.01.15')
        if kind=='listing':c['extracted_pages']['3']=c['extracted_pages']['3'].replace('2024.01.18','2024.01.19')
        if kind=='ratio':c['extracted_pages']['2']=c['extracted_pages']['2'].replace('0.1000','0.0950')
        if kind=='rows':c['extracted_pages']['3']+='\n0.0000 0.00 2024.01.12 20.00 19.09 2024.01.18'
        if kind=='row_order':
            c['extracted_pages']['2']+='\n600002 其他 2024.01.03 0.0000 0.1000 10.00'
            c['extracted_pages']['3']=c['extracted_pages']['3'].replace('\n','\n0.0000 0.00 2024.01.15 20.00 19.09 2024.01.18\n',1)
        save(tmp_path,c,item)
    with pytest.raises(ValueError):load_exchange_quote(item,e,tmp_path)


def test_terms_cannot_self_assert_reviewed_quote():
    from ashare_mvp.rights_terms import serialize_case
    spec=serialize_case(event())
    spec['reviewed_exchange_quote']={'ex_reference':'19.09'}
    with pytest.raises(ValueError,match='separately verified'):normalize_rights_case(spec,CAL)


@pytest.mark.parametrize('policy',['cash_limited_subscribe','decline'])
def test_primary_quote_only_waives_typed_fault_and_independent_money_still_reconciles(tmp_path,monkeypatch,policy):
    from test_rights_account import fixture
    from ashare_mvp.rights_reference_audit import build_rights_reference, RightsReferenceAudit
    from ashare_mvp.rights_disk_account import simulate_rights_account
    from ashare_mvp.rights_account_audit import reconstruct_rights_account
    from ashare_mvp.model import Config
    work,cal,store,ref,e=fixture(tmp_path,monkeypatch)
    ref.close()
    e['exchange_ratio']=Decimal('.095')
    e['issued_ordinary_shares']=95
    e['aggregate_subscription_cash']='950'
    e['post_issue_ordinary_shares']=1095
    e['reviewed_exchange_quote']=dict(symbol=e['symbol'],event_id=e['event_id'],
        ex_date=str(e['ex_date'].date()),previous_close='20.0',ex_reference='19.09',
        retrospective_reference_only=True)
    build_rights_reference(store,work/'ordinary','rights.json',tmp_path/'project',work,work/'quoted')
    ref=RightsReferenceAudit(work/'quoted',store,tmp_path/'project',work)
    try:
        assert not hasattr(ref,'day_faults')
        assert ref.parent.day_faults(e['ex_date'])=={e['symbol']}
        assert ref.day_faults_for_rights(e['ex_date'])==set()
        proof=ref.manifest['reconciliations'][0]
        assert proof['matches'] and not proof['capital_count_formula_diagnostic']['matches']
        assert 'exchange_rights_quote.py' in ref.manifest['code_hashes']
        simulate_rights_account(store,ref,work/'schedule.csv',cal,Config(),work/'account',policy)
        result=reconstruct_rights_account(store,ref,work/'account',cal[1],cal[-1],work/'independent',policy)
        assert result['max_absolute_reconciliation_error']<1e-10
    finally:
        ref.close();store.close()

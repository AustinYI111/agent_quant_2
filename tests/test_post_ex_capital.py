from copy import deepcopy
import json
import pandas as pd
import pytest

from ashare_mvp.rights_terms import normalize_rights_case,serialize_case,rights_reference,POST_EX_CAPITAL_FIELDS,load_rights_evidence
from ashare_mvp.rights_wallet import RightsWallet
from ashare_mvp.phase2_protocol import sha256
from test_rights_wallet import event,CAL
from test_rights_share_scope import scoped_spec,primary_sources


def bridge_spec():
    s=serialize_case(event())
    s.update(post_issue_ordinary_shares=1270,post_ex_other_ordinary_shares=170,
        post_ex_other_ordinary_kind='convertible_conversion',post_ex_other_ordinary_scope='same_share_class',
        post_ex_other_ordinary_start='2024-01-15',post_ex_other_ordinary_end='2024-01-16')
    return s


def test_bridge_reconciles_quoted_total_without_changing_rights_ratio():
    original=event();e=normalize_rights_case(bridge_spec(),CAL)
    assert e['post_issue_ordinary_shares']==1270
    assert e['registered_ordinary_shares']+e['issued_ordinary_shares']==1100
    assert e['exchange_ratio']==original['exchange_ratio']
    assert e['offer_ratio']==original['offer_ratio']
    assert rights_reference(20,19.09,e)==rights_reference(20,19.09,original)
    assert serialize_case(e)['post_ex_other_ordinary_start']=='2024-01-15'


@pytest.mark.parametrize('key,value',[
    ('post_ex_other_ordinary_shares',0),('post_ex_other_ordinary_shares',-170),
    ('post_ex_other_ordinary_shares',True),('post_ex_other_ordinary_shares',170.0),
    ('post_ex_other_ordinary_shares','170'),('post_ex_other_ordinary_kind','unknown'),
    ('post_ex_other_ordinary_scope','H_share_class'),('post_ex_other_ordinary_start','2024-01-12'),
    ('post_ex_other_ordinary_start','2024-01-13'),('post_ex_other_ordinary_start','2024-01-15T00:00:00Z'),
    ('post_ex_other_ordinary_end','2024-01-11'),('post_ex_other_ordinary_end','2024-01-17'),
    ('post_ex_other_ordinary_end',None),('post_issue_ordinary_shares',1100),
    ('post_issue_ordinary_shares',1270.0),('post_issue_ordinary_shares',None),
    ('listing_publication_date',None),('tradable_from',None),('ex_date',None),('outcome','failed')])
def test_unverified_or_misdated_bridge_is_rejected(key,value):
    s=bridge_spec();s[key]=value
    with pytest.raises(ValueError):normalize_rights_case(s,CAL)


@pytest.mark.parametrize('missing',POST_EX_CAPITAL_FIELDS)
def test_partial_bridge_is_rejected(missing):
    s=bridge_spec();s.pop(missing)
    with pytest.raises(ValueError):normalize_rights_case(s,CAL)


def test_bridge_also_reconciles_scoped_a_and_h_capital():
    s=scoped_spec();s.update({k:v for k,v in bridge_spec().items() if k.startswith('post_ex_')})
    s.update(post_issue_ordinary_shares=1270,issuer_ordinary_shares_after=1470)
    e=normalize_rights_case(s,CAL)
    assert e['other_ordinary_shares']==200 and str(e['exchange_ratio'])=='0.1'
    s['issuer_ordinary_shares_after']=1300
    with pytest.raises(ValueError,match='capital must reconcile'):normalize_rights_case(s,CAL)


@pytest.mark.parametrize('policy',['cash_limited_subscribe','decline'])
@pytest.mark.parametrize('cash',[0.,500.,2000.])
def test_third_party_conversion_never_gives_holder_free_shares_or_changes_cash(policy,cash):
    original=event();bridged=normalize_rights_case(bridge_spec(),CAL)
    wallets=[RightsWallet([e],policy) for e in [original,bridged]]
    holdings=[{'sz.002142':1000.},{'sz.002142':1000.}]
    balances=[cash,cash]
    for date in CAL[(CAL>='2024-01-03') & (CAL<='2024-01-22')]:
        prices={'sz.002142':19.09 if date>=pd.Timestamp('2024-01-12') else 20.}
        outputs=[]
        for i,wallet in enumerate(wallets):
            result=wallet.open(date,balances[i],holdings[i],prices);balances[i]=result[0]
            outputs.append((result,wallet.close(date,holdings[i])))
        assert outputs[0]==outputs[1]
        assert holdings[0]==holdings[1] and balances[0]==balances[1]
        assert wallets[0].snapshot()==wallets[1].snapshot()
    assert holdings[1]['sz.002142']<=1100.
    if policy=='decline':assert holdings[1]['sz.002142']==1000.


@pytest.mark.parametrize('missing',[None,*POST_EX_CAPITAL_FIELDS])
def test_listing_source_must_bind_every_bridge_parameter(tmp_path,missing):
    project=tmp_path/'project';project.mkdir();raw=tmp_path/'raw';raw.mkdir()
    records,capture=primary_sources(raw)
    for key in POST_EX_CAPITAL_FIELDS:records['listing']['parameter_pages'][key]=1
    if missing:records['listing']['parameter_pages'].pop(missing)
    (raw/'capture.json').write_text(json.dumps(capture),encoding='utf8')
    for r in records.values():r['capture_sha256']=sha256(raw/'capture.json')
    config=dict(schema='ordinary_rights_evidence_v1',original_vintages_verified=False,
        result_preopen_timestamp_verified=False,events=[dict(terms=bridge_spec(),sources=records)])
    (project/'case.json').write_text(json.dumps(config),encoding='utf8')
    if missing:
        with pytest.raises(ValueError,match='page mapping'):load_rights_evidence('case.json',project,raw,CAL)
    else:
        events,_=load_rights_evidence('case.json',project,raw,CAL)
        assert events[0]['post_ex_other_ordinary_shares']==170


@pytest.mark.parametrize('policy',['cash_limited_subscribe','decline'])
def test_sqlite_account_and_independent_audit_preserve_original_money_and_shares(tmp_path,monkeypatch,policy):
    from test_rights_account import fixture
    from ashare_mvp.rights_reference_audit import build_rights_reference,RightsReferenceAudit
    from ashare_mvp.rights_disk_account import simulate_rights_account
    from ashare_mvp.rights_account_audit import reconstruct_rights_account
    from ashare_mvp.model import Config
    work,cal,store,old,e=fixture(tmp_path,monkeypatch)
    try:
        simulate_rights_account(store,old,work/'schedule.csv',cal,Config(),work/'baseline',policy)
    finally:old.close()
    s=serialize_case(e);s.update(post_issue_ordinary_shares=1270,post_ex_other_ordinary_shares=170,
        post_ex_other_ordinary_kind='convertible_conversion',post_ex_other_ordinary_scope='same_share_class',
        post_ex_other_ordinary_start=str(cal[7].date()),post_ex_other_ordinary_end=str(cal[8].date()))
    normalized=normalize_rights_case(s,cal);e.clear();e.update(normalized)
    build_rights_reference(store,work/'ordinary','rights.json',tmp_path/'project',work,work/'bridge_ref')
    ref=RightsReferenceAudit(work/'bridge_ref',store,tmp_path/'project',work)
    try:
        result=simulate_rights_account(store,ref,work/'schedule.csv',cal,Config(),work/'bridged',policy)
        for name in ['daily_nav.csv','orders.csv','rights_ledger.csv','rights_daily.csv','daily_execution_totals.csv']:
            assert (work/'baseline'/name).read_bytes()==(work/'bridged'/name).read_bytes()
        check=reconstruct_rights_account(store,ref,work/'bridged',cal[1],cal[-1],work/'audit',policy,
            expected_holdings=result['holdings'],expected_rights_state=result['rights_state'])
        assert check['max_absolute_reconciliation_error']<1e-12
    finally:ref.close();store.close()

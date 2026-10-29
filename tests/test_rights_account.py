import json
from pathlib import Path

import pandas as pd
import pytest

from ashare_mvp.disk_market import build_quote_store, build_reference_audit, QuoteStore, ReferenceAudit
from ashare_mvp.disk_account import simulate_disk_account
from ashare_mvp.rights_disk_account import simulate_rights_account, RightsAccountStop
from ashare_mvp.rights_reference_audit import build_rights_reference, RightsReferenceAudit
from ashare_mvp.rights_terms import normalize_rights_case
from ashare_mvp.model import Config


def fixture(tmp_path, monkeypatch, outcome='success', fault=False, extra_action=False):
    work = tmp_path/'work'
    work.mkdir()
    project = tmp_path/'project'
    project.mkdir()
    cal = pd.bdate_range('2024-01-02', periods=20)
    e = normalize_rights_case(dict(event_id='a',symbol='sh.600001',
        offer_publication_date=str(cal[0].date()),record_date=str(cal[2].date()),
        subscription_start=str(cal[3].date()),subscription_end=str(cal[5].date()),
        expected_result_date=str(cal[6].date()),offer_ratio='.1',subscription_price='10',
        outcome=outcome,result_publication_date=None if outcome is None else str(cal[6].date()),
        ex_date=str(cal[6].date()) if outcome=='success' else None,
        registered_ordinary_shares=1000,issued_ordinary_shares=100,
        quoted_exchange_ratio_per_10='1',aggregate_subscription_cash='1000',post_issue_ordinary_shares=1100,
        listing_publication_date=str(cal[8].date()) if outcome=='success' else None,
        tradable_from=str(cal[11].date()) if outcome=='success' else None,
        refund_date=str(cal[9].date()) if outcome in ['failed','canceled'] else None),cal)
    records=[]
    for i,d in enumerate(cal):
        price=19.09 if outcome=='success' and i>=6 else 20.
        pre=price+(1. if fault and i==9 else 0.)
        records.append(dict(date=d,symbol=e['symbol'],open=price,close=price,preclose=pre,
                            tradestatus='1',terminal_date=None,can_buy=True,can_sell=True))
    pd.DataFrame(records).to_csv(work/'raw.csv',index=False)
    columns=['symbol','ex_date','available_date','pay_date','cash_per_share','share_multiplier']
    actions=pd.DataFrame(columns=columns)
    if extra_action:
        actions=pd.DataFrame([dict(symbol=e['symbol'],ex_date=cal[9],available_date=cal[0],pay_date=cal[9],cash_per_share=0.,share_multiplier=1.)])
    actions.to_csv(work/'actions.csv',index=False)
    pd.DataFrame([dict(date=cal[i],symbol=e['symbol'],target_weight=.5) for i in [0,8,12]]).to_csv(work/'schedule.csv',index=False)
    # These tests exercise the real SQLite pipeline and typed audit; primary
    # capture identity/tampering is tested separately in test_rights_wallet.
    monkeypatch.setattr('ashare_mvp.rights_reference_audit.load_rights_evidence',
                        lambda *args: ([e],dict(project_source_hashes={},evidence_source_hashes={},original_vintages_verified=False,reference_only_not_ranking_or_training=True)))
    build_quote_store(work/'raw.csv',cal,work/'quotes',chunksize=3)
    build_reference_audit(work/'quotes',work/'raw.csv',work/'actions.csv',work/'ordinary')
    store=QuoteStore(work/'quotes')
    build_rights_reference(store,work/'ordinary','rights.json',project,work,work/'rights')
    ref=RightsReferenceAudit(work/'rights',store,project,work)
    return work,cal,store,ref,e


@pytest.mark.parametrize('kind',['normal','dividend','split','suspension','blocked_sale','missing_buy'])
def test_no_rights_preserves_original_nav_and_orders_exactly(kind,tmp_path,monkeypatch):
    from test_disk_account import inputs,build
    data,picks,raw,actions,c=inputs(tmp_path,kind)
    store,parent,_,_=build(tmp_path,data)
    ref=object.__new__(RightsReferenceAudit)
    ref.path=parent.path
    ref.manifest=parent.manifest
    ref.events=[]
    ref.actions=parent.actions
    ref.day_faults_for_rights=parent.day_faults
    try:
        simulate_disk_account(store,parent,tmp_path/'picks.csv',data.calendar,c,tmp_path/'old')
        simulate_rights_account(store,ref,tmp_path/'picks.csv',data.calendar,c,tmp_path/'new','cash_limited_subscribe')
        for name in ['daily_nav.csv','orders.csv','daily_execution_totals.csv']:
            assert (tmp_path/'old'/name).read_bytes()==(tmp_path/'new'/name).read_bytes()
    finally:
        parent.close();store.close()


def test_typed_reference_does_not_silently_enter_original_account(tmp_path,monkeypatch):
    work,cal,store,ref,e=fixture(tmp_path,monkeypatch)
    try:
        assert not hasattr(ref,'day_faults')
        with pytest.raises(AttributeError):
            simulate_disk_account(store,ref,work/'schedule.csv',cal,Config(),work/'wrong')
        assert ref.parent.day_faults(e['ex_date'])=={e['symbol']}
        assert ref.day_faults_for_rights(e['ex_date'])==set()
    finally:
        ref.close();store.close()


@pytest.mark.parametrize('policy',['cash_limited_subscribe','decline'])
def test_paid_rights_and_decline_complete_full_calendar(tmp_path,monkeypatch,policy):
    work,cal,store,ref,e=fixture(tmp_path,monkeypatch)
    try:
        result=simulate_rights_account(store,ref,work/'schedule.csv',cal,Config(),work/'account',policy)
        assert result['sessions']==19
        assert result['requested_end']==str(cal[-1].date())
        assert result['rights_state']['locked_claims']=={}
        ledger=pd.read_csv(work/'account/rights_ledger.csv')
        daily=pd.read_csv(work/'account/rights_daily.csv')
        if policy=='decline':
            assert 'subscribe' not in set(ledger.kind)
            assert daily.rights_value.eq(0).all()
        else:
            assert len(ledger[ledger.kind.eq('subscribe')])==1
            assert ledger.loc[ledger.kind.eq('shares_unlocked'),'date'].iloc[0]==str(cal[11].date())
            assert daily.loc[daily.date.lt(str(cal[11].date())),'rights_value'].max()>0
            # Initial equity is one; the account pays, rather than being given,
            # the 0.1 x registered share entitlement at the fixed offer price.
            paid=ledger[ledger.kind.eq('subscribe')].iloc[0]
            assert paid.principal==pytest.approx(paid.quantity*10.)
            assert paid.cash_delta==pytest.approx(-paid.principal-paid.fee)
    finally:
        ref.close();store.close()


@pytest.mark.parametrize('outcome',['failed','canceled'])
def test_failed_offer_refunds_in_account_without_free_stock(tmp_path,monkeypatch,outcome):
    work,cal,store,ref,e=fixture(tmp_path,monkeypatch,outcome=outcome)
    try:
        result=simulate_rights_account(store,ref,work/'schedule.csv',cal,Config(),work/'account','cash_limited_subscribe')
        ledger=pd.read_csv(work/'account/rights_ledger.csv')
        assert result['sessions']==19 and not result['rights_state']['locked_claims']
        assert set(ledger.kind)=={'registry_entitlement','subscribe','refund'}
        assert ledger.loc[ledger.kind.eq('refund'),'date'].iloc[0]==str(cal[10].date())
        assert ledger.loc[ledger.kind.eq('refund'),'principal'].sum()==pytest.approx(ledger.loc[ledger.kind.eq('subscribe'),'principal'].sum())
    finally:
        ref.close();store.close()


@pytest.mark.parametrize('kind',['unknown','reference','overlap'])
def test_locked_positive_claim_stops_with_state_and_all_valid_prefix_ledgers(tmp_path,monkeypatch,kind):
    work,cal,store,ref,e=fixture(tmp_path,monkeypatch,outcome=None if kind=='unknown' else 'success',fault=kind=='reference',extra_action=kind=='overlap')
    try:
        with pytest.raises(RightsAccountStop) as error:
            simulate_rights_account(store,ref,work/'schedule.csv',cal,Config(),work/'account','cash_limited_subscribe')
        x=error.value
        assert x.reason==dict(unknown='rights_outcome',reference='reference',overlap='rights_corporate_action_overlap')[kind]
        assert x.rights_state['locked_claims']['a']['quantity']>0
        nav=pd.read_csv(work/'account/daily_nav.csv')
        daily=pd.read_csv(work/'account/rights_daily.csv')
        assert len(nav)==len(daily)==x.nav_rows
        assert nav.date.max()<str(x.date.date())
        assert daily.date.tolist()==nav.date.tolist()
    finally:
        ref.close();store.close()


def test_tampered_typed_audit_rejected(tmp_path,monkeypatch):
    work,cal,store,ref,e=fixture(tmp_path,monkeypatch)
    ref.close()
    m=json.loads((work/'rights/manifest.json').read_text())
    m['reconciliations'][0]['observed_preclose']='99'
    (work/'rights/manifest.json').write_text(json.dumps(m))
    try:
        with pytest.raises(ValueError,match='changed'):
            RightsReferenceAudit(work/'rights',store,tmp_path/'project',work)
    finally:store.close()


@pytest.mark.parametrize('policy,outcome', [('cash_limited_subscribe','success'),('decline','success'),('cash_limited_subscribe','failed'),('cash_limited_subscribe','canceled')])
def test_independent_reconstruction_of_complete_rights_accounts(tmp_path,monkeypatch,policy,outcome):
    from ashare_mvp.rights_account_audit import reconstruct_rights_account
    work,cal,store,ref,e=fixture(tmp_path,monkeypatch,outcome=outcome)
    try:
        result=simulate_rights_account(store,ref,work/'schedule.csv',cal,Config(),work/'account',policy)
        proof=reconstruct_rights_account(store,ref,work/'account',cal[1],cal[-1],work/'audit',policy,
                                         expected_holdings=result['holdings'],expected_rights_state=result['rights_state'])
        assert proof['sessions']==19 and proof['max_absolute_reconciliation_error']<1e-12
    finally:ref.close();store.close()


def test_independent_unknown_claim_prefix_preserves_all_stopped_equity(tmp_path,monkeypatch):
    from ashare_mvp.rights_account_audit import reconstruct_rights_account
    work,cal,store,ref,e=fixture(tmp_path,monkeypatch,outcome=None)
    try:
        with pytest.raises(RightsAccountStop) as error:
            simulate_rights_account(store,ref,work/'schedule.csv',cal,Config(),work/'account','cash_limited_subscribe')
        x=error.value
        proof=reconstruct_rights_account(store,ref,work/'account',cal[1],cal[-1],work/'audit','cash_limited_subscribe',
                                         stop_date=x.date,expected_holdings=x.holdings,expected_rights_state=x.rights_state)
        assert proof['stopped_state_checked'] and not proof['full_period_performance_verified']
    finally:ref.close();store.close()


@pytest.mark.parametrize('filename,field,value', [('rights_ledger.csv','quantity',9.),('rights_ledger.csv','cash_delta',9.),
    ('rights_daily.csv','rights_value',9.),('daily_nav.csv','cash',9.),('daily_execution_totals.csv','cost',9.)])
def test_independent_rights_audit_rejects_tampering(tmp_path,monkeypatch,filename,field,value):
    from ashare_mvp.rights_account_audit import reconstruct_rights_account
    work,cal,store,ref,e=fixture(tmp_path,monkeypatch)
    try:
        simulate_rights_account(store,ref,work/'schedule.csv',cal,Config(),work/'account','cash_limited_subscribe')
        frame=pd.read_csv(work/'account'/filename);frame.loc[0,field]=value;frame.to_csv(work/'account'/filename,index=False)
        with pytest.raises(ValueError):
            reconstruct_rights_account(store,ref,work/'account',cal[1],cal[-1],work/'audit','cash_limited_subscribe')
    finally:ref.close();store.close()

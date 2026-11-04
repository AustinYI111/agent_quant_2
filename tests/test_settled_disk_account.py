import pandas as pd
import pytest
from test_ordinary_share_settlement import fixture,load
from ashare_mvp.disk_market import build_quote_store,build_reference_audit,QuoteStore,ReferenceAudit
from ashare_mvp.rights_reference_audit import RightsReferenceAudit
from ashare_mvp.rights_disk_account import simulate_rights_account
from ashare_mvp.settled_disk_account import simulate_settled_account,SettledAccountStop
from ashare_mvp.model import Config


def market(args):
    project,work,cal,*_=args
    raw=pd.DataFrame([dict(date=d,symbol='sh.600001',open=13.2 if i<2 else 10.,close=13.2 if i<2 else 10.,preclose=13.2 if i<2 else 10.,tradestatus='1',can_buy=True,can_sell=True,terminal_date=None) for i,d in enumerate(cal)])
    raw.to_csv(work/'raw.csv',index=False)
    pd.DataFrame([dict(symbol='sh.600001',ex_date=cal[2],available_date=cal[0],pay_date=cal[2],cash_per_share=.2,share_multiplier=1.3)]).to_csv(work/'actions.csv',index=False)
    pd.DataFrame([dict(date=cal[i],symbol='sh.600001',target_weight=weight) for i,weight in [(0,1.),(1,0.),(2,0.)]]).to_csv(work/'schedule.csv',index=False)
    build_quote_store(work/'raw.csv',cal,work/'quotes',chunksize=2)
    build_reference_audit(work/'quotes',work/'raw.csv',work/'actions.csv',work/'ordinary')
    store=QuoteStore(work/'quotes');parent=ReferenceAudit(work/'ordinary',store)
    ref=object.__new__(RightsReferenceAudit);ref.path=parent.path;ref.manifest=parent.manifest;ref.actions=parent.actions;ref.events=[];ref.day_faults_for_rights=parent.day_faults
    return store,parent,ref


def test_actual_sqlite_account_sells_only_old_shares_then_unlocked_bonus(tmp_path):
    args=fixture(tmp_path);book=load(args);project,work,cal,*_=args;store,parent,ref=market(args)
    try:
        config=Config(commission_bps=0,slippage_bps=0,sell_tax_bps=0)
        result=simulate_settled_account(store,ref,book,work/'schedule.csv',cal,config,work/'settled','decline')
        orders=pd.read_csv(work/'settled/orders.csv');sales=orders[orders.side.eq('sell')]
        assert list(sales.date)==['2024-01-04','2024-01-05']
        assert sales.notional.tolist()==pytest.approx([10/13.2,3/13.2])
        nav=pd.read_csv(work/'settled/daily_nav.csv');assert nav.nav.to_numpy()==pytest.approx([1.]*len(nav),abs=1e-12)
        assert result['ordinary_state']['locked_claims']=={} and result['holdings']=={}
        ledger=pd.read_csv(work/'settled/ordinary_ledger.csv')
        assert list(ledger.kind)==['bonus_claim_recognized','bonus_shares_unlocked']
        assert list(ledger.date)==['2024-01-04','2024-01-05']
    finally:parent.close();store.close()


def test_missing_source_stops_before_ex_date_cash_or_share_mutation(tmp_path):
    args=fixture(tmp_path);project,work,cal,*_=args;(project/'listing.json').write_text('[]');book=load(args)
    store,parent,ref=market(args)
    try:
        with pytest.raises(SettledAccountStop) as caught:
            simulate_settled_account(store,ref,book,work/'schedule.csv',cal,Config(commission_bps=0,slippage_bps=0,sell_tax_bps=0),work/'missing','decline')
        error=caught.value
        assert error.reason=='ordinary_listing_unknown' and error.holdings==pytest.approx({'sh.600001':1/13.2})
        assert error.ordinary_state['locked_claims']=={}
        nav=pd.read_csv(work/'missing/daily_nav.csv');assert nav.date.tolist()==['2024-01-03']
        orders=pd.read_csv(work/'missing/orders.csv');assert orders.side.tolist()==['buy']
    finally:parent.close();store.close()


def test_locked_bonus_value_reduces_rebalance_buy_target(tmp_path):
    args=fixture(tmp_path);book=load(args);project,work,cal,*_=args;store,parent,ref=market(args)
    pd.DataFrame([dict(date=cal[i],symbol='sh.600001',target_weight=1.) for i in [0,1]]).to_csv(work/'schedule.csv',index=False)
    try:
        simulate_settled_account(store,ref,book,work/'schedule.csv',cal,Config(commission_bps=0,slippage_bps=0,sell_tax_bps=0),work/'same_target','decline')
        orders=pd.read_csv(work/'same_target/orders.csv');later=orders[orders.date.eq('2024-01-04')]
        assert later.notional.sum()==pytest.approx(0.)
    finally:parent.close();store.close()


@pytest.mark.parametrize('policy',['decline','cash_limited_subscribe'])
def test_paid_rights_integration_matches_legacy_when_no_ordinary_bonus(tmp_path,monkeypatch,policy):
    from test_rights_account import fixture as rights_fixture
    from ashare_mvp.ordinary_share_settlement import OrdinaryListingBook
    work,cal,store,ref,e=rights_fixture(tmp_path,monkeypatch)
    project=tmp_path/'project';(project/'listing.json').write_text('[]')
    book=OrdinaryListingBook('listing.json',project,work,cal)
    try:
        simulate_rights_account(store,ref,work/'schedule.csv',cal,Config(),work/'old',policy)
        simulate_settled_account(store,ref,book,work/'schedule.csv',cal,Config(),work/'new',policy)
        for name in ['daily_nav.csv','orders.csv','daily_execution_totals.csv','rights_ledger.csv','rights_daily.csv']:
            assert (work/'old'/name).read_bytes()==(work/'new'/name).read_bytes()
    finally:ref.close();store.close()


def test_ordinary_registry_while_paid_rights_locked_stops_before_mutation(tmp_path,monkeypatch):
    from test_rights_account import fixture as rights_fixture
    from ashare_mvp.ordinary_share_settlement import OrdinaryListingBook
    import json,hashlib
    work,cal,store,ref,event=rights_fixture(tmp_path,monkeypatch)
    source_root=tmp_path/'sources';source_root.mkdir()
    def change(row,raw,source,capture):
        row.update(record_date='2024-01-11',ex_date='2024-01-12',tradable_from='2024-01-15')
        raw['ex_date']='2024-01-12';capture.write_text(json.dumps(raw))
        row['source_capture_sha256']=hashlib.sha256(capture.read_bytes()).hexdigest()
    args=fixture(source_root,change)
    book=OrdinaryListingBook('listing.json',args[0],args[1],cal)
    try:
        with pytest.raises(SettledAccountStop) as caught:
            simulate_settled_account(store,ref,book,work/'schedule.csv',cal,Config(),work/'overlap','cash_limited_subscribe')
        error=caught.value
        assert error.reason=='ordinary_rights_event_overlap' and error.date==pd.Timestamp('2024-01-11')
        assert error.rights_state['locked_claims'] and error.ordinary_state['locked_claims']=={}
        assert pd.read_csv(work/'overlap/daily_nav.csv').date.iloc[-1]=='2024-01-10'
    finally:ref.close();store.close()


@pytest.mark.parametrize('kind',['normal','dividend','suspension','blocked_sale','missing_buy'])
def test_no_bonus_matches_original_rights_engine_exactly(tmp_path,monkeypatch,kind):
    from test_disk_account import inputs,build
    data,picks,raw,actions,config=inputs(tmp_path,kind)
    store,parent,_,_=build(tmp_path,data)
    project=tmp_path/'project';work=tmp_path/'work';project.mkdir();work.mkdir();(project/'listing.json').write_text('[]')
    from ashare_mvp.ordinary_share_settlement import OrdinaryListingBook
    book=OrdinaryListingBook('listing.json',project,work,data.calendar)
    ref=object.__new__(RightsReferenceAudit);ref.path=parent.path;ref.manifest=parent.manifest;ref.events=[];ref.actions=parent.actions;ref.day_faults_for_rights=parent.day_faults
    try:
        simulate_rights_account(store,ref,tmp_path/'picks.csv',data.calendar,config,tmp_path/'old','decline')
        simulate_settled_account(store,ref,book,tmp_path/'picks.csv',data.calendar,config,tmp_path/'new','decline')
        for name in ['daily_nav.csv','orders.csv','daily_execution_totals.csv','rights_ledger.csv','rights_daily.csv']:
            assert (tmp_path/'old'/name).read_bytes()==(tmp_path/'new'/name).read_bytes()
    finally:parent.close();store.close()

import json
import pandas as pd
import pytest
from test_ordinary_share_settlement import fixture,load,registered,action,empty,SYMBOL
from test_settled_disk_account import market
from ashare_mvp.ordinary_share_settlement import OrdinaryListingBook,OrdinaryShareWallet,OrdinarySettlementStop
from ashare_mvp.pending_listing_account import simulate_pending_listing_account,PendingListingAccountStop
from ashare_mvp.pending_listing_audit import reconstruct_pending_listing_account
from ashare_mvp.settled_disk_account import simulate_settled_account
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.model import Config


def unresolved(row,*_):
    row.update(tradable_from=None,listing_status='unresolved_original_conflict',source_declared_tradable_from='2023-01-04',unresolved_reason='Original primary notice explicitly names a listing date before the current record date; no valid replacement date is known.')


def pending_book(args):
    return OrdinaryListingBook('listing.json',args[0],args[1],args[2],allow_unresolved_listing=True)


def schedule(work,cal,weights):
    pd.DataFrame([dict(date=cal[i],symbol=SYMBOL,target_weight=v) for i,v in weights]).to_csv(work/'schedule.csv',index=False)


def audit(store,ref,book,work,cal,result,stop=None,schedule_file=None):
    file=schedule_file or work/'schedule.csv'
    return reconstruct_pending_listing_account(store,ref,book,work/'account',cal[1],cal[-1],work/'audit','decline',stop_date=stop,expected_holdings=result['holdings'],expected_rights_state=result['rights_state'],expected_ordinary_state=result['ordinary_state'],schedule_path=file,expected_schedule_sha256=sha256(file))


@pytest.mark.parametrize('fault',['default','no_status','no_literal','no_reason','plausible_literal','guessed_date'])
def test_unknown_date_requires_explicit_conflict_profile(tmp_path,fault):
    def change(row,*rest):
        unresolved(row)
        if fault=='no_status':row.pop('listing_status')
        if fault=='no_literal':row.pop('source_declared_tradable_from')
        if fault=='no_reason':row['unresolved_reason']='unknown'
        if fault=='plausible_literal':row['source_declared_tradable_from']='2024-01-05'
        if fault=='guessed_date':row['tradable_from']='2024-01-04'
    args=fixture(tmp_path,change)
    with pytest.raises((ValueError,KeyError)):
        if fault=='default':load(args)
        else:pending_book(args)


def test_unknown_bonus_never_unlocks_and_fork_does_not_mutate_parent(tmp_path):
    book=pending_book(fixture(tmp_path,unresolved))
    wallet,holdings=registered(book)
    before=wallet.snapshot()
    with pytest.raises(OrdinarySettlementStop):wallet.open('2024-01-04',holdings,{SYMBOL:10.},action())
    assert before==wallet.snapshot() and holdings=={SYMBOL:100.}
    wallet.allow_unresolved_listing=True
    child=wallet.fork();copy_holdings=dict(holdings)
    child.open('2024-01-04',copy_holdings,{SYMBOL:10.},action());child.close('2024-01-04',copy_holdings)
    child.open('2024-01-05',copy_holdings,{SYMBOL:10.},empty())
    assert copy_holdings==holdings and wallet.snapshot()==before
    assert child.unresolved_exposure({SYMBOL:10.})==pytest.approx({SYMBOL:300.})
    assert list(child.snapshot()['locked_claims'].values())[0]['tradable_from'] is None


@pytest.mark.parametrize('target',[1.,.5])
def test_account_continues_only_when_existing_shares_cover_intended_sales(tmp_path,target):
    args=fixture(tmp_path,unresolved);book=pending_book(args);_,work,cal,*_=args;store,parent,ref=market(args)
    schedule(work,cal,[(0,1.),(1,target)])
    try:
        result=simulate_pending_listing_account(store,ref,book,work/'schedule.csv',cal,Config(),work/'account','decline')
        assert result['ordinary_state']['locked_claims'] and not result['listing_dates_certified']
        ledger=pd.read_csv(work/'account/ordinary_ledger.csv');assert ledger.kind.tolist()==['bonus_claim_recognized']
        report=audit(store,ref,book,work,cal,result)
        assert report['max_absolute_reconciliation_error']<1e-12 and not report['full_period_performance_verified']
        assert report['status']=='pending_listing_requested_interval_reconciled'
    finally:parent.close();store.close()


@pytest.mark.parametrize('later',[False,True])
def test_binding_sale_stops_atomically_with_previous_completed_state(tmp_path,later):
    args=fixture(tmp_path,unresolved);book=pending_book(args);_,work,cal,*_=args;store,parent,ref=market(args)
    schedule(work,cal,[(0,1.),(1,.5),(2,0.)] if later else [(0,1.),(1,0.)])
    try:
        with pytest.raises(PendingListingAccountStop) as caught:
            simulate_pending_listing_account(store,ref,book,work/'schedule.csv',cal,Config(),work/'account','decline')
        error=caught.value;date=cal[3 if later else 2]
        assert error.date==date and error.reason=='ordinary_sellability_needed'
        nav=pd.read_csv(work/'account/daily_nav.csv');assert nav.date.iloc[-1]==str(cal[2 if later else 1].date())
        assert bool(error.ordinary_state['locked_claims']) is later
        result=dict(holdings=error.holdings,rights_state=error.rights_state,ordinary_state=error.ordinary_state)
        report=audit(store,ref,book,work,cal,result,stop=date)
        assert report['stopped_state_checked'] and report['max_absolute_reconciliation_error']<1e-12
        assert all(pd.Timestamp(d)<date for d in pd.read_csv(work/'account/orders.csv').date)
    finally:parent.close();store.close()


def test_independent_audit_rejects_arithmetically_consistent_omitted_target_sale(tmp_path):
    args=fixture(tmp_path,unresolved);book=pending_book(args);_,work,cal,*_=args;store,parent,ref=market(args)
    schedule(work,cal,[(0,1.),(1,.5)])
    try:
        result=simulate_pending_listing_account(store,ref,book,work/'schedule.csv',cal,Config(),work/'account','decline')
        original=work/'original.csv'
        pd.DataFrame([dict(date=cal[i],symbol=SYMBOL,target_weight=v) for i,v in [(0,1.),(1,0.)]]).to_csv(original,index=False)
        with pytest.raises(ValueError,match='original target needs unresolved bonus sale'):
            audit(store,ref,book,work,cal,result,schedule_file=original)
    finally:parent.close();store.close()


def test_independent_audit_does_not_call_producer_wallet_or_order_helpers(tmp_path,monkeypatch):
    import ashare_mvp.pending_listing_account as producer
    import ashare_mvp.disk_account as targets
    args=fixture(tmp_path,unresolved);book=pending_book(args);_,work,cal,*_=args;store,parent,ref=market(args)
    schedule(work,cal,[(0,1.),(1,.5)])
    try:
        result=simulate_pending_listing_account(store,ref,book,work/'schedule.csv',cal,Config(),work/'account','decline')
        def forbidden(*a,**k):raise AssertionError('producer called by audit')
        monkeypatch.setattr(producer,'simulate_pending_listing_account',forbidden)
        monkeypatch.setattr(OrdinaryShareWallet,'open',forbidden)
        monkeypatch.setattr(targets,'iter_schedule',forbidden)
        assert audit(store,ref,book,work,cal,result)['max_absolute_reconciliation_error']<1e-12
    finally:parent.close();store.close()


def test_confirmed_listing_profile_is_byte_identical_to_strict_account(tmp_path):
    args=fixture(tmp_path);book=load(args);_,work,cal,*_=args;store,parent,ref=market(args)
    try:
        simulate_settled_account(store,ref,book,work/'schedule.csv',cal,Config(),work/'strict','decline')
        result=simulate_pending_listing_account(store,ref,book,work/'schedule.csv',cal,Config(),work/'account','decline')
        for name in ['daily_nav.csv','orders.csv','ordinary_ledger.csv','rights_ledger.csv','rights_daily.csv','daily_execution_totals.csv']:
            assert (work/'strict'/name).read_bytes()==(work/'account'/name).read_bytes()
        assert audit(store,ref,book,work,cal,result)['max_absolute_reconciliation_error']<1e-12
    finally:parent.close();store.close()


def test_audit_rejects_wrong_original_schedule_hash_before_creating_output(tmp_path):
    args=fixture(tmp_path,unresolved);book=pending_book(args);_,work,cal,*_=args;store,parent,ref=market(args)
    try:
        with pytest.raises(ValueError,match='schedule hash differs'):
            reconstruct_pending_listing_account(store,ref,book,work/'account',cal[1],cal[-1],work/'audit','decline',schedule_path=work/'schedule.csv',expected_schedule_sha256='0'*64)
        assert not (work/'audit').exists()
    finally:parent.close();store.close()


@pytest.mark.parametrize('policy',['decline','cash_limited_subscribe'])
def test_paid_rights_remain_identical_and_independently_reconciled(tmp_path,monkeypatch,policy):
    from test_rights_account import fixture as rights_fixture
    work,cal,store,ref,_=rights_fixture(tmp_path,monkeypatch)
    project=tmp_path/'project';(project/'listing.json').write_text('[]')
    book=OrdinaryListingBook('listing.json',project,work,cal)
    try:
        simulate_settled_account(store,ref,book,work/'schedule.csv',cal,Config(),work/'strict',policy)
        result=simulate_pending_listing_account(store,ref,book,work/'schedule.csv',cal,Config(),work/'account',policy)
        for name in ['daily_nav.csv','orders.csv','ordinary_ledger.csv','rights_ledger.csv','rights_daily.csv','daily_execution_totals.csv']:
            assert (work/'strict'/name).read_bytes()==(work/'account'/name).read_bytes()
        report=reconstruct_pending_listing_account(store,ref,book,work/'account',cal[1],cal[-1],work/'audit',policy,expected_holdings=result['holdings'],expected_rights_state=result['rights_state'],expected_ordinary_state=result['ordinary_state'],schedule_path=work/'schedule.csv',expected_schedule_sha256=sha256(work/'schedule.csv'))
        assert report['max_absolute_reconciliation_error']<1e-12
    finally:ref.close();store.close()


@pytest.mark.parametrize('listing',['2024-01-04','2024-01-05'])
def test_trades_and_nav_invariant_under_either_possible_listing_date(tmp_path,listing):
    args=fixture(tmp_path,unresolved);book=pending_book(args);_,work,cal,*_=args;store,parent,ref=market(args)
    schedule(work,cal,[(0,1.),(1,.5)])
    try:
        simulate_pending_listing_account(store,ref,book,work/'schedule.csv',cal,Config(),work/'account','decline')
        rows=json.loads((args[0]/'listing.json').read_text(encoding='utf8'))
        rows[0]['tradable_from']=listing;rows[0].pop('listing_status')
        (args[0]/'confirmed.json').write_text(json.dumps(rows),encoding='utf8')
        resolved=OrdinaryListingBook('confirmed.json',args[0],work,cal)
        simulate_settled_account(store,ref,resolved,work/'schedule.csv',cal,Config(),work/'confirmed','decline')
        for name,fields in [('orders.csv',['notional','cost','stamp_duty']),('daily_nav.csv',['nav','cash','dividend_receivable'])]:
            actual=pd.read_csv(work/'account'/name);other=pd.read_csv(work/'confirmed'/name)
            assert actual.date.tolist()==other.date.tolist()
            for field in fields:assert actual[field].to_numpy()==pytest.approx(other[field].to_numpy(),abs=1e-12)
    finally:parent.close();store.close()

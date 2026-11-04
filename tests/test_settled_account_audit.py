import csv
import pandas as pd
import pytest
from test_ordinary_share_settlement import fixture,load
from test_settled_disk_account import market
from ashare_mvp.settled_disk_account import simulate_settled_account,SettledAccountStop
from ashare_mvp.settled_account_audit import reconstruct_settled_account
from ashare_mvp.model import Config


@pytest.mark.parametrize('tamper',[None,'quantity','early_unlock','remove_flow','nav','sale','stop_holdings'])
def test_independent_bonus_reconstruction_and_tamper_rejection(tmp_path,tamper):
    args=fixture(tmp_path);book=load(args);project,work,cal,*_=args;store,parent,ref=market(args)
    try:
        result=simulate_settled_account(store,ref,book,work/'schedule.csv',cal,Config(),work/'account','decline')
        if tamper in ['quantity','early_unlock','remove_flow']:
            p=work/'account/ordinary_ledger.csv';frame=pd.read_csv(p)
            if tamper=='quantity':frame.loc[0,'quantity']*=2
            if tamper=='early_unlock':frame.loc[1,'date']='2024-01-04'
            if tamper=='remove_flow':frame=frame.iloc[:1]
            frame.to_csv(p,index=False)
        if tamper=='nav':
            p=work/'account/daily_nav.csv';frame=pd.read_csv(p);frame.loc[1,'nav']+=.1;frame.to_csv(p,index=False)
        if tamper=='sale':
            p=work/'account/orders.csv';frame=pd.read_csv(p);i=frame[frame.side.eq('sell')].index[0];frame.loc[i,'notional']*=1.3;frame.to_csv(p,index=False)
        expected=dict(result['holdings'])
        if tamper=='stop_holdings':expected['sh.600001']=10.
        def audit():return reconstruct_settled_account(store,ref,book,work/'account',cal[1],cal[-1],work/'audit','decline',expected_holdings=expected,expected_rights_state=result['rights_state'],expected_ordinary_state=result['ordinary_state'])
        if tamper:
            with pytest.raises(ValueError):audit()
        else:
            report=audit();assert report['status']=='complete_settled_account_reconciled' and report['max_absolute_reconciliation_error']<1e-12
            data=pd.read_csv(work/'audit/daily_reconstruction.csv')
            assert data.loc[data.date.eq('2024-01-04'),'ordinary_locked_value'].iloc[0]>0
            assert data.loc[data.date.eq('2024-01-05'),'ordinary_locked_value'].iloc[0]==0
    finally:parent.close();store.close()


def test_auditor_rejects_legacy_instant_bonus_sale(tmp_path):
    from ashare_mvp.rights_disk_account import simulate_rights_account
    args=fixture(tmp_path);book=load(args);project,work,cal,*_=args;store,parent,ref=market(args)
    try:
        simulate_rights_account(store,ref,work/'schedule.csv',cal,Config(),work/'legacy','decline')
        (work/'legacy/ordinary_ledger.csv').write_text('date,symbol,ex_date,kind,quantity\n')
        with pytest.raises(ValueError):reconstruct_settled_account(store,ref,book,work/'legacy',cal[1],cal[-1],work/'audit','decline')
    finally:parent.close();store.close()


@pytest.mark.parametrize('policy',['decline','cash_limited_subscribe'])
def test_paid_rights_still_reconstruct_without_importing_wallet(tmp_path,monkeypatch,policy):
    from test_rights_account import fixture as rights_fixture
    from ashare_mvp.ordinary_share_settlement import OrdinaryListingBook
    work,cal,store,ref,event=rights_fixture(tmp_path,monkeypatch)
    project=tmp_path/'project';(project/'listing.json').write_text('[]');book=OrdinaryListingBook('listing.json',project,work,cal)
    try:
        result=simulate_settled_account(store,ref,book,work/'schedule.csv',cal,Config(),work/'account',policy)
        report=reconstruct_settled_account(store,ref,book,work/'account',cal[1],cal[-1],work/'audit',policy,expected_holdings=result['holdings'],expected_rights_state=result['rights_state'],expected_ordinary_state=result['ordinary_state'])
        assert report['max_absolute_reconciliation_error']<1e-12
    finally:ref.close();store.close()


@pytest.mark.parametrize('same_day',[False,True])
def test_terminal_locked_claim_or_same_day_release_reconciles(tmp_path,same_day):
    args=fixture(tmp_path,lambda r,*_:r.update(tradable_from='2024-01-04') if same_day else None)
    book=load(args);project,work,cal,*_=args;store,parent,ref=market(args)
    try:
        prefix=cal[:3]
        result=simulate_settled_account(store,ref,book,work/'schedule.csv',prefix,Config(),work/'account','decline')
        report=reconstruct_settled_account(store,ref,book,work/'account',cal[1],prefix[-1],work/'audit','decline',expected_holdings=result['holdings'],expected_rights_state=result['rights_state'],expected_ordinary_state=result['ordinary_state'])
        assert report['max_absolute_reconciliation_error']<1e-12
        assert bool(result['ordinary_state']['locked_claims']) is not same_day
    finally:parent.close();store.close()

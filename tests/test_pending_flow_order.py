import copy,json
import pandas as pd
import pytest
from test_ordinary_share_settlement import fixture,load,SYMBOL
from ashare_mvp.disk_market import build_quote_store,build_reference_audit,QuoteStore,ReferenceAudit
from ashare_mvp.rights_reference_audit import RightsReferenceAudit
from ashare_mvp.pending_listing_account import simulate_pending_listing_account
from ashare_mvp.pending_listing_audit import reconstruct_pending_listing_account
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.model import Config


@pytest.mark.parametrize('variant',['original','cross_issuer_order','missing','duplicate','quantity','event_date','unlock_before_recognition'])
def test_independent_audit_matches_cross_issuer_flows_without_losing_claim_identity(tmp_path,variant):
    args=fixture(tmp_path);root,work,cal,row,raw=args;second='sh.600002'
    other=copy.deepcopy(row);capture=copy.deepcopy(raw);capture['code']='600002';url=row['source_url'].replace('fixture.PDF','fixture2.PDF');capture['notices'][0]['url']=url
    source=work/'capture2.json';source.write_text(json.dumps(capture))
    other.update(symbol=second,tradable_from=str(cal[2].date()),source_url=url,source_capture_file='@work/capture2.json',source_capture_sha256=sha256(source))
    (root/'listing.json').write_text(json.dumps([row,other]));book=load(args)
    prices=pd.DataFrame([dict(date=d,symbol=s,open=13.2 if i<2 else 10.,close=13.2 if i<2 else 10.,preclose=13.2 if i<2 else 10.,tradestatus='1',can_buy=True,can_sell=True,terminal_date=None) for s in [SYMBOL,second] for i,d in enumerate(cal)])
    prices.to_csv(work/'raw.csv',index=False)
    pd.DataFrame([dict(symbol=s,ex_date=cal[2],available_date=cal[0],pay_date=cal[2],cash_per_share=.2,share_multiplier=1.3) for s in [SYMBOL,second]]).to_csv(work/'actions.csv',index=False)
    # Buy insertion order deliberately differs from the action-table order.
    pd.DataFrame([dict(date=cal[0],symbol=s,target_weight=.5) for s in [second,SYMBOL]]).to_csv(work/'schedule.csv',index=False)
    build_quote_store(work/'raw.csv',cal,work/'quotes',chunksize=4)
    build_reference_audit(work/'quotes',work/'raw.csv',work/'actions.csv',work/'reference')
    store=QuoteStore(work/'quotes');parent=ReferenceAudit(work/'reference',store)
    ref=object.__new__(RightsReferenceAudit);ref.path=parent.path;ref.manifest=parent.manifest;ref.actions=parent.actions;ref.events=[];ref.day_faults_for_rights=parent.day_faults
    try:
        result=simulate_pending_listing_account(store,ref,book,work/'schedule.csv',cal,Config(),work/'account','decline')
        path=work/'account/ordinary_ledger.csv';ledger=pd.read_csv(path,float_precision='round_trip')
        day=ledger[ledger.date.eq(str(cal[2].date()))].copy();rest=ledger[~ledger.date.eq(str(cal[2].date()))].copy()
        assert day.symbol.tolist()==[SYMBOL,second,second] and day.kind.tolist()==['bonus_claim_recognized','bonus_claim_recognized','bonus_shares_unlocked']
        if variant=='cross_issuer_order':day=day.iloc[[1,2,0]]
        elif variant=='missing':day=day.iloc[1:]
        elif variant=='duplicate':day=pd.concat([day,day.iloc[:1]],ignore_index=True)
        elif variant=='quantity':day.iloc[0,day.columns.get_loc('quantity')]+=.01
        elif variant=='event_date':day.iloc[0,day.columns.get_loc('ex_date')]=str(cal[1].date())
        elif variant=='unlock_before_recognition':day=day.iloc[[2,0,1]]
        pd.concat([day,rest],ignore_index=True).to_csv(path,index=False)
        def verify():return reconstruct_pending_listing_account(store,ref,book,work/'account',cal[1],cal[-1],work/'audit','decline',expected_holdings=result['holdings'],expected_rights_state=result['rights_state'],expected_ordinary_state=result['ordinary_state'],schedule_path=work/'schedule.csv',expected_schedule_sha256=sha256(work/'schedule.csv'))
        if variant in ['original','cross_issuer_order']:assert verify()['max_absolute_reconciliation_error']<1e-12
        else:
            with pytest.raises(ValueError):verify()
    finally:parent.close();store.close()

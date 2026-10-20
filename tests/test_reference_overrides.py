import pandas as pd
import pytest
from ashare_mvp.reference_overrides import apply_reference_overrides
from ashare_mvp.price_action_audit import audit_price_actions


def inputs():
    dates=pd.bdate_range("2020-07-27","2020-08-03")
    actions=pd.DataFrame([dict(symbol="sh.600052",ex_date="2020-07-31",available_date="2020-07-28",pay_date="2020-07-31",cash_per_share=.09727,share_multiplier=1)])
    evidence=pd.DataFrame([dict(symbol="sh.600052",ex_date="2020-07-31",reference_cash_per_share=.09483,expected_cash_per_share=.09727,announcement_date="2020-07-27",available_date="2020-07-28",source_url="https://static.cninfo.com.cn/test.pdf",source_sha256="1"*64,source_page=2,reviewed=True)])
    return actions,evidence,dates


def test_reviewed_reference_preserves_actual_cash_and_explains_quote():
    actions,evidence,dates=inputs()
    corrected=apply_reference_overrides(actions,evidence,dates)
    assert corrected.cash_per_share.iloc[0]==.09727 and corrected.reference_cash_per_share.iloc[0]==.09483
    assert actions.columns.tolist()==["symbol","ex_date","available_date","pay_date","cash_per_share","share_multiplier"]
    prices=pd.DataFrame(dict(date=dates,symbol="sh.600052",close=[3.73]*4+[3.64]*2,preclose=[3.73]*4+[3.64]*2))
    gaps,_=audit_price_actions(prices,actions,dates,reference_tick_size=.01)
    assert len(gaps)==1
    gaps,_=audit_price_actions(prices,corrected,dates,reference_tick_size=.01)
    assert gaps.empty


@pytest.mark.parametrize("field,value",[("expected_cash_per_share",.1),("available_date","2020-08-03"),("reviewed",False),("source_sha256","bad")])
def test_conflict_late_or_unreviewed_reference_rejected(field,value):
    actions,evidence,dates=inputs();evidence.loc[0,field]=value
    with pytest.raises(ValueError): apply_reference_overrides(actions,evidence,dates)


def share_inputs():
    actions,evidence,dates=inputs()
    actions['cash_per_share']=0.;actions['share_multiplier']=2.;actions['pay_date']=pd.NaT
    evidence['reference_cash_per_share']=0.;evidence['expected_cash_per_share']=0.
    evidence['reference_share_multiplier']=1.9;evidence['expected_share_multiplier']=2.
    return actions,evidence,dates


def test_reviewed_reference_shares_validate_quote_without_changing_entitlement():
    from ashare_mvp.corporate_actions import economic_return_prices
    actions,evidence,dates=share_inputs();corrected=apply_reference_overrides(actions,evidence,dates)
    assert corrected.share_multiplier.iloc[0]==2. and corrected.reference_share_multiplier.iloc[0]==1.9
    prices=pd.DataFrame(dict(date=dates,symbol='sh.600052',open=[10.]*4+[5.26]*2,close=[10.]*4+[5.26]*2,preclose=[10.]*4+[5.26]*2,tradestatus='1'))
    gaps,_=audit_price_actions(prices,corrected,dates,reference_tick_size=.01);assert gaps.empty
    with pytest.raises(ValueError,match='unreconciled'):
        economic_return_prices(prices,actions,dates,reference_tick_size=.01)
    result=economic_return_prices(prices,corrected,dates,reference_tick_size=.01)
    assert result.total_return_open.tolist()==pytest.approx([10.]*4+[10.52]*2)
    assert result.economic_shares.tolist()==[1.]*4+[2.]*2 and result.dividend_receivable.eq(0).all()
    assert 'reference_share_multiplier' not in actions


@pytest.mark.parametrize('field,value', [('expected_share_multiplier',1.8),('reference_share_multiplier',0),('reference_share_multiplier',float('inf')),('expected_share_multiplier',float('nan'))])
def test_conflicting_invalid_or_half_known_reference_share_terms_are_rejected(field,value):
    actions,evidence,dates=share_inputs();evidence[field]=value
    with pytest.raises(ValueError):apply_reference_overrides(actions,evidence,dates)


def test_existing_reference_share_conflict_is_rejected():
    actions,evidence,dates=share_inputs();actions['reference_share_multiplier']=1.8
    with pytest.raises(ValueError,match='conflicting reviewed reference shares'):
        apply_reference_overrides(actions,evidence,dates)


def test_mixed_cash_only_and_share_reference_evidence_preserves_cash_only_defaults():
    actions,evidence,dates=inputs();a,e,_=share_inputs();a['symbol']='sh.600053';e['symbol']='sh.600053'
    corrected=apply_reference_overrides(pd.concat([actions,a],ignore_index=True),pd.concat([evidence,e],ignore_index=True),dates).set_index('symbol')
    assert pd.isna(corrected.loc['sh.600052','reference_share_multiplier'])
    assert corrected.loc['sh.600052','reference_cash_per_share']==.09483
    assert corrected.loc['sh.600053','share_multiplier']==2. and corrected.loc['sh.600053','reference_share_multiplier']==1.9


def test_reference_json_decimal_literals_preserve_half_tick_decision(tmp_path):
    import json
    from ashare_mvp.reference_overrides import load_reference_evidence
    from ashare_mvp.price_reference import reference_matches
    path=tmp_path/'reference.json';path.write_text(json.dumps([dict(reference_cash_per_share=.075,reference_share_multiplier=1.39)]))
    result=load_reference_evidence(path).iloc[0]
    assert result.reference_cash_per_share==.075 and result.reference_share_multiplier==1.39
    assert reference_matches(9.93,10.,result.reference_cash_per_share,1.,.01)

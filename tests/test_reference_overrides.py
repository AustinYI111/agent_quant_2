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

from types import SimpleNamespace
import pandas as pd
import pytest
from ashare_mvp.pipeline import simulate_monthly
from ashare_mvp.model import Config


def inputs():
    dates=pd.bdate_range("2024-01-02",periods=5)
    raw=pd.DataFrame(dict(date=dates,symbol="sh.600000",open=[10,10,9,9,9],close=[10,10,9,9,9],preclose=[10,10,9,9,9],tradestatus="1"))
    prices=raw[["date","symbol"]].copy(); prices["total_return_open"]=10.; prices["can_buy"]=True; prices["can_sell"]=True
    actions=pd.DataFrame(dict(symbol=["sh.600000"],ex_date=[dates[2]],available_date=[dates[0]],pay_date=[dates[3]],cash_per_share=[1.],share_multiplier=[1.]))
    return SimpleNamespace(prices=prices,calendar=dates),raw,actions,dates


def test_sale_after_ex_date_retains_receivable_until_payment():
    data,raw,actions,dates=inputs()
    picks=pd.DataFrame(dict(date=[dates[0],dates[1]],symbol="sh.600000",target_weight=[1.,0.]))
    nav,trades=simulate_monthly(data,picks,Config(commission_bps=0,slippage_bps=0,sell_tax_bps=0),raw,actions)
    ex=nav[nav.date==dates[2]].iloc[0]
    assert ex.cash==pytest.approx(.9) and ex.dividend_receivable==pytest.approx(.1) and ex.positions==0
    assert nav.nav.tolist()==pytest.approx([1]*4)
    assert nav.iloc[-1].cash==pytest.approx(1) and nav.iloc[-1].dividend_receivable==0


def test_ex_date_buyer_receives_no_old_entitlement():
    data,raw,actions,dates=inputs()
    picks=pd.DataFrame(dict(date=[dates[1]],symbol="sh.600000",target_weight=[1.]))
    nav,_=simulate_monthly(data,picks,Config(commission_bps=0,slippage_bps=0,sell_tax_bps=0),raw,actions)
    assert nav.dividend_receivable.eq(0).all() and nav.nav.tolist()==pytest.approx([1]*3)


def test_raw_suspension_blocks_sale_even_with_bad_execution_flag():
    data,raw,actions,dates=inputs()
    raw.loc[2,"tradestatus"]="0"
    picks=pd.DataFrame(dict(date=[dates[0],dates[1]],symbol="sh.600000",target_weight=[1.,0.]))
    nav,trades=simulate_monthly(data,picks,Config(commission_bps=0,slippage_bps=0,sell_tax_bps=0),raw,actions)
    assert trades[trades.side=="sell"].iloc[0].status=="blocked"
    assert nav[nav.date==dates[2]].iloc[0].positions==1

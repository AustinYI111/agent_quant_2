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


def test_full_event_run_seals_inputs_and_records_model_return_contract(tmp_path):
    import json
    from ashare_mvp.demo import make_demo
    from ashare_mvp.pipeline import run
    data=make_demo(stocks=20,periods=420)
    p=data.prices
    raw=pd.DataFrame(dict(date=p.date,symbol=p.symbol,open=p.total_return_open,close=p.total_return_open,tradestatus="1"))
    raw["preclose"]=raw.groupby("symbol").close.shift().fillna(raw.close)
    actions=pd.DataFrame(columns=["symbol","ex_date","available_date","pay_date","cash_per_share","share_multiplier"])
    symbol=p.symbol.iloc[0];ex=data.calendar[400]
    previous=raw.loc[raw.symbol.eq(symbol)&raw.date.eq(data.calendar[399]),"close"].iloc[0]
    raw.loc[raw.symbol.eq(symbol)&raw.date.eq(ex),"preclose"]=previous-.09
    actions=pd.DataFrame([dict(symbol=symbol,ex_date=ex,available_date=ex,pay_date=ex,cash_per_share=.1,share_multiplier=1)])
    evidence=pd.DataFrame([dict(symbol=symbol,ex_date=ex,expected_cash_per_share=.1,reference_cash_per_share=.09,
        announcement_date=data.calendar[399],available_date=ex,source_url="https://static.cninfo.com.cn/synthetic-fixture.pdf",source_sha256="1"*64,source_page=1,reviewed=True)])
    with pytest.raises(ValueError,match="unreconciled"):
        run(data,tmp_path/'no_evidence',Config(n_estimators=2,min_train_groups=2,train_horizon=3),raw_prices=raw,corporate_actions=actions,reference_tick_size=.01)
    assert not (tmp_path/'no_evidence').exists()
    summary=run(data,tmp_path/'run',Config(n_estimators=2,min_train_groups=2,train_horizon=3),raw_prices=raw,corporate_actions=actions,reference_tick_size=.01,reference_overrides=evidence)
    assert summary["monthly_predictions"]>0
    assert summary["account_nav_basis"]=="raw_share_account_gross_dividends"
    assert summary["label_return_basis"]=="gross_new_entry_entitlements"
    assert {"raw_prices","corporate_actions"}.issubset(summary["input_hashes"])
    assert (tmp_path/'run/input_raw_prices.csv').exists()
    assert "reference_overrides" in summary["input_hashes"]
    assert (tmp_path/'run/input_reference_overrides.json').exists()
    manifest=json.loads(next((tmp_path/'run/models').glob('*.json')).read_text(encoding='utf-8'))
    assert manifest["account_nav_basis"]==summary["account_nav_basis"]
    assert manifest["label_return_basis"]==summary["label_return_basis"]
    assert manifest["price_reconciliation"]["reference_tick_size"]==.01


@pytest.mark.parametrize('signal_index',[0,1])
def test_stock_only_account_without_payment_date_for_old_and_new_buyers(signal_index):
    data,raw,actions,dates=inputs()
    raw.loc[raw.date>=dates[2],['open','close','preclose']]=5.
    actions['cash_per_share']=0.;actions['share_multiplier']=2.;actions['pay_date']=pd.NaT
    picks=pd.DataFrame(dict(date=[dates[signal_index],dates[3]],symbol='sh.600000',target_weight=[1.,0.]))
    nav,trades=simulate_monthly(data,picks,Config(commission_bps=0,slippage_bps=0,sell_tax_bps=0),raw,actions)
    assert nav.nav.tolist()==pytest.approx([1.]*len(nav))
    assert nav.dividend_receivable.eq(0).all() and nav.iloc[-1].positions==0
    assert nav.iloc[-1].cash==pytest.approx(1.)

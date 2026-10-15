import pandas as pd
import pytest
from ashare_mvp.security_identity import stitch_code_prices,canonical_event_symbols,code_change_maps


def changes():
    return pd.DataFrame([dict(old_symbol="sz.000043",new_symbol="sz.001914",announcement_date="2019-12-06",effective_date="2019-12-16",share_ratio=1,source_url="https://static.cninfo.com.cn/evidence.pdf")])


def test_dated_code_change_preserves_prices_and_quarantines_zero_old_code():
    p=pd.DataFrame(dict(date=["2019-12-13","2019-12-16","2019-12-16"],symbol=["sz.000043","sz.000043","sz.001914"],close=[10,0,11],preclose=[10,0,10]))
    result,rejected=stitch_code_prices(p,changes())
    assert result.close.tolist()==[10,11] and result.symbol.eq("sz.000043").all()
    assert result.trading_symbol.tolist()==["sz.000043","sz.001914"]
    assert len(rejected)==1 and rejected.close.iloc[0]==0
    prefix,_=stitch_code_prices(p.iloc[:1],changes())
    pd.testing.assert_frame_equal(prefix,result.iloc[:1])


def test_event_collisions_are_not_silently_deduplicated():
    e=pd.DataFrame(dict(symbol=["sz.000043","sz.001914"],ex_date=["2020-01-02"]*2))
    with pytest.raises(ValueError,match="ambiguous same-issuer"): canonical_event_symbols(e,changes())


@pytest.mark.parametrize("field,value",[("share_ratio",2),("announcement_date","2019-12-17"),("new_symbol","sz.000043")])
def test_unproven_exchange_or_bad_clock_rejected(field,value):
    c=changes();c.loc[0,field]=value
    with pytest.raises(ValueError): code_change_maps(c)


def test_complete_identity_training_and_reload_preserve_live_trade_code(tmp_path):
    import numpy as np
    from dataclasses import replace
    from ashare_mvp.demo import make_demo,save
    from ashare_mvp.data import load
    from ashare_mvp.pipeline import run
    from ashare_mvp.model import Config
    from ashare_mvp.model_registry import export_model,predict_model
    data=make_demo(stocks=20,periods=420)
    prices=data.prices.copy(); funds=data.fundamentals.copy()
    original=prices.symbol.iloc[0]; effective=data.calendar[350]
    selected=prices.symbol.eq(original)
    prices.loc[selected,"symbol"]="sz.000043"
    prices.loc[selected & prices.date.ge(effective),"symbol"]="sz.001914"
    prices.loc[prices.symbol.eq("sz.001914"),"listing_date"]=effective
    selected=funds.symbol.eq(original)
    funds.loc[selected,"symbol"]="sz.000043"
    funds.loc[selected & funds.available_date.ge(effective),"symbol"]="sz.001914"
    raw=replace(data,prices=prices,fundamentals=funds)
    evidence=changes();evidence["announcement_date"]=str(data.calendar[340].date());evidence["effective_date"]=str(effective.date())
    save(raw,tmp_path/'input')
    summary=run(raw,tmp_path/'run',Config(n_estimators=2,min_train_groups=2,train_horizon=3),code_changes=evidence)
    assert "trading_symbol" not in summary["features"]
    latest=pd.read_csv(tmp_path/'run/latest_top20.csv')
    assert latest.loc[latest.symbol.eq("sz.000043"),"trading_symbol"].iloc[0]=="sz.001914"
    orders=pd.read_csv(tmp_path/'run/orders.csv',parse_dates=["date"])
    issuer_orders=orders[orders.symbol.eq("sz.000043")]
    assert len(issuer_orders)>0
    assert issuer_orders.loc[issuer_orders.date.ge(effective),"trading_symbol"].eq("sz.001914").all()
    restored=load(tmp_path/'input',code_changes=evidence)
    export_model(tmp_path/'run',tmp_path/'bundle')
    recovered=predict_model(tmp_path/'bundle',restored,tmp_path/'prediction.csv')
    assert recovered.symbol.tolist()==latest.symbol.tolist()
    assert recovered.trading_symbol.tolist()==latest.trading_symbol.tolist()
    np.testing.assert_allclose(recovered.score,latest.score,rtol=0,atol=1e-12)


def test_fundamental_alias_conflict_is_not_silently_resolved():
    from ashare_mvp.security_identity import apply_identity_dataset
    from ashare_mvp.data import Dataset
    p=pd.DataFrame(dict(date=[pd.Timestamp("2019-12-13")],symbol=["sz.000043"],close=[10]))
    f=pd.DataFrame(dict(symbol=["sz.000043","sz.001914"],report_date=["2019-09-30"]*2,available_date=["2019-12-06"]*2))
    data=Dataset(p,f,pd.DatetimeIndex(p.date),{})
    with pytest.raises(ValueError,match="fundamental versions"): apply_identity_dataset(data,changes())

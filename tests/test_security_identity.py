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

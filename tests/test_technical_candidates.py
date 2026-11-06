import numpy as np
import pandas as pd
import pytest
from ashare_mvp.technical_candidates import market_state_features,technical_candidate_rows,normalize_candidate_snapshot


@pytest.fixture
def inputs():
    calendar=pd.bdate_range('2023-01-02',periods=280)
    index=pd.DataFrame(dict(date=calendar,code='sh.000300',close=np.linspace(100,400,len(calendar))))
    prices=pd.DataFrame(dict(date=calendar,symbol='sh.600000',quality_segment=1,close=10.,volume=1000.))
    prices.loc[len(prices)-1,['close','volume']]=[11.,3000.]
    return calendar,index,prices


def test_prior_box_bounds_and_prior_volume_exclude_current_signal(inputs):
    calendar,index,prices=inputs
    states=market_state_features(index,calendar)
    row=technical_candidate_rows(prices,states,calendar).iloc[-1]
    assert row.box_width_20==0 and row.box_width_60==0
    assert row.close_to_prior_box_high20==1.1 and row.breakout_volume_ratio50==3
    assert row.box_entry and row.market_entry and row.combined_entry
    assert row.momentum_63_times_market_on==pytest.approx(.1)


def test_future_prices_do_not_change_past_features(inputs):
    calendar,index,prices=inputs
    full=technical_candidate_rows(prices,market_state_features(index,calendar),calendar)
    prefix=technical_candidate_rows(prices.iloc[:-20],market_state_features(index.iloc[:-20],calendar),calendar)
    pd.testing.assert_frame_equal(full.iloc[:-20],prefix)


def test_unknown_market_state_is_not_forward_filled_or_forced_into_exit(inputs):
    calendar,index,prices=inputs
    states=market_state_features(index.iloc[:-1],calendar)
    row=technical_candidate_rows(prices,states,calendar).iloc[-1]
    assert pd.isna(row.index_above_ma200) and not row.market_entry
    assert pd.isna(row.market_exit) and not row.combined_entry
    assert pd.isna(row.combined_exit)


def test_new_quality_segment_does_not_inherit_old_box(inputs):
    calendar,index,prices=inputs
    prices.loc[260:,'quality_segment']=2
    frame=technical_candidate_rows(prices,market_state_features(index,calendar),calendar)
    assert frame.loc[260:279,'box_entry'].eq(False).all()
    assert frame.loc[279,'box_width_20']!=frame.loc[279,'box_width_20']
    assert pd.isna(frame.loc[279,'box_exit'])


def test_price_gap_without_quality_break_is_rejected(inputs):
    calendar,index,prices=inputs
    with pytest.raises(ValueError,match='missing price session'):
        technical_candidate_rows(prices.drop(150),market_state_features(index,calendar),calendar)


def test_market_state_is_not_demeaned_to_zero(inputs):
    calendar,index,prices=inputs
    frame=technical_candidate_rows(prices,market_state_features(index,calendar),calendar).tail(1)
    second=frame.copy();second['symbol']='sz.000001';second['box_width_20']=.2
    cross=pd.concat([frame,second],ignore_index=True)
    result=normalize_candidate_snapshot(cross)
    assert result.index_above_ma200.tolist()==[1.,1.]
    assert result.index_volatility_63.tolist()==cross.index_volatility_63.tolist()
    assert result.box_width_20.iloc[0]<0<result.box_width_20.iloc[1]


def test_cross_date_normalization_cannot_leak_future_cross_section(inputs):
    calendar,index,prices=inputs
    frame=technical_candidate_rows(prices,market_state_features(index,calendar),calendar).tail(2)
    with pytest.raises(ValueError,match='one signal date'):
        normalize_candidate_snapshot(frame)


def test_zero_historical_volume_does_not_create_infinite_breakout(inputs):
    calendar,index,prices=inputs;prices['volume']=0.
    row=technical_candidate_rows(prices,market_state_features(index,calendar),calendar).iloc[-1]
    assert pd.isna(row.breakout_volume_ratio50) and not row.box_entry


def test_index_cannot_be_replaced_with_unrelated_benchmark(inputs):
    calendar,index,_=inputs;index['code']='sh.000001'
    with pytest.raises(ValueError,match='CSI300'):
        market_state_features(index,calendar)

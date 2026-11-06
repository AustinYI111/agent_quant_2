import numpy as np
import pandas as pd
import pytest
from ashare_mvp.industry_candidate_dataset import research_industry_symbol_rows, normalize_industry_snapshots
from ashare_mvp.archived_industry_research import FEATURES


@pytest.fixture
def history():
    calendar=pd.bdate_range('2020-01-01',periods=160)
    prices=pd.DataFrame(dict(date=calendar,symbol='sh.600000',quality_segment=0,close=np.arange(1.,161.),volume=100.))
    states=pd.DataFrame(dict(date=calendar,index_above_ma200=1.,index_volatility_63=.1))
    return prices,states,calendar


def test_raw_context_uses_declared_causal_windows(history):
    prices,states,calendar=history
    result=research_industry_symbol_rows(prices,states,calendar,calendar[-1:],calendar[-1])
    assert result.raw_momentum_63.iloc[0]==pytest.approx(160/97-1)
    assert result.raw_momentum_126.iloc[0]==pytest.approx(160/34-1)
    assert result.close_above_ma50.iloc[0]==1.


def test_later_prices_cannot_change_a_prior_industry_signal(history):
    prices,states,calendar=history;signal=calendar[-10:-9]
    original=research_industry_symbol_rows(prices,states,calendar,signal,calendar[-1])
    changed=prices.copy();changed.loc[changed.date.gt(signal[0]),'close']=99999.
    pd.testing.assert_frame_equal(original,research_industry_symbol_rows(changed,states,calendar,signal,calendar[-1]))


def test_quality_break_keeps_insufficient_context_unknown(history):
    prices,states,calendar=history;prices=prices.copy();prices.loc[100:,'quality_segment']=1
    result=research_industry_symbol_rows(prices,states,calendar,calendar[-1:],calendar[-1])
    assert pd.isna(result.raw_momentum_63.iloc[0]) and pd.isna(result.raw_momentum_126.iloc[0])
    assert result.close_above_ma50.iloc[0]==1.


def test_independent_bars_or_missing_original_key_rejected(history):
    prices,states,calendar=history
    with pytest.raises(ValueError,match='independent'):
        research_industry_symbol_rows(prices,states,calendar,calendar[-1:],calendar[-2])
    with pytest.raises(ValueError,match='preserve original'):
        research_industry_symbol_rows(prices,states,calendar,pd.DatetimeIndex([calendar[0]-pd.Timedelta(days=1)]),calendar[-1])


def test_interleaved_dates_normalize_without_mixing_cross_sections():
    raw=pd.DataFrame(dict(date=pd.to_datetime(['2020-01-02','2020-01-03','2020-01-02','2020-01-03']),symbol=['a','a','b','b']))
    for name in FEATURES:raw[name]=[0.,1000.,10.,1100.]
    result=normalize_industry_snapshots(raw)
    for _,frame in result.groupby('date'):
        np.testing.assert_allclose(frame[FEATURES].to_numpy(),[[-1.]*3,[1.]*3],rtol=0,atol=1e-12)


def test_missing_and_constant_values_preserve_eligibility_and_unknowns():
    raw=pd.DataFrame(dict(date=pd.to_datetime(['2020-01-02']*3),symbol=pd.Categorical(['b','a','c'],categories=['c','b','a'])))
    for name in FEATURES:raw[name]=[2.,2.,np.nan]
    result=normalize_industry_snapshots(raw)
    assert result.symbol.tolist()==['a','b','c'] and len(result)==3
    assert result[FEATURES].iloc[:2].eq(0).all().all() and result[FEATURES].iloc[2].isna().all()


def test_normalization_rejects_outcome_columns_and_duplicate_keys():
    raw=pd.DataFrame(dict(date=pd.to_datetime(['2020-01-02']),symbol=['a']))
    for name in FEATURES:raw[name]=[0.]
    with pytest.raises(ValueError,match='no outcomes'):normalize_industry_snapshots(raw.assign(target=999.))
    with pytest.raises(ValueError,match='unique'):normalize_industry_snapshots(pd.concat([raw,raw],ignore_index=True))

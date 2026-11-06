import numpy as np
import pandas as pd
import pytest
from ashare_mvp.candidate_dataset import normalize_candidate_snapshots, research_symbol_rows, FEATURES
from ashare_mvp.technical_candidates import normalize_candidate_snapshot, market_state_features


def sample():
    rows=pd.DataFrame({'date':pd.to_datetime(['2020-01-02']*3+['2020-01-03']*3),'symbol':['a','b','c']*2})
    for column in FEATURES:rows[column]=[1.,2.,np.nan,100.,200.,np.nan]
    rows['index_above_ma200']=1.
    rows['index_volatility_63']=.02
    return rows


def test_cross_section_matches_original_single_date_rule_without_future_contamination():
    raw=sample();result=normalize_candidate_snapshots(raw)
    for date,group in raw.groupby('date'):
        expected=normalize_candidate_snapshot(group).reset_index(drop=True)
        pd.testing.assert_frame_equal(result.loc[result.date.eq(date),FEATURES].reset_index(drop=True),expected)
    pd.testing.assert_frame_equal(result.iloc[:3].reset_index(drop=True),normalize_candidate_snapshots(raw.iloc[:3]))
    assert result.index_above_ma200.eq(1).all()
    assert result.index_volatility_63.eq(.02).all()
    assert result.box_width_20.isna().sum()==2


def test_issuer_major_interleaved_dates_and_nonconsecutive_index_match_sorted_result():
    raw=sample();expected=normalize_candidate_snapshots(raw)
    interleaved=raw.sort_values(['symbol','date']).copy()
    interleaved.index=np.arange(len(interleaved))*3+5
    pd.testing.assert_frame_equal(normalize_candidate_snapshots(interleaved),expected)


@pytest.mark.parametrize('fault',['target','duplicate','infinite','market','date'])
def test_invalid_or_outcome_contaminated_snapshots_rejected(fault):
    raw=sample()
    if fault=='target':raw['target']=.1
    elif fault=='duplicate':raw=pd.concat([raw,raw.iloc[:1]],ignore_index=True)
    elif fault=='infinite':raw.loc[0,'box_width_20']=np.inf
    elif fault=='market':raw.loc[0,'index_above_ma200']=0.
    elif fault=='date':raw.loc[0,'date']+=pd.Timedelta(hours=1)
    with pytest.raises(ValueError):normalize_candidate_snapshots(raw)


def test_only_original_eligible_keys_selected_but_daily_exit_signals_retained():
    calendar=pd.bdate_range('2020-01-01',periods=280)
    index=pd.DataFrame(dict(date=calendar,close=np.arange(280)+100.,code='sh.000300'))
    history=pd.DataFrame(dict(date=calendar,symbol='sh.600000',close=10.,volume=100.,quality_segment=1))
    history.loc[278,'close']=8.
    states=market_state_features(index,calendar)
    daily,selected=research_symbol_rows(history,states,calendar,pd.DatetimeIndex([calendar[260]]),calendar[-1])
    assert selected.date.tolist()==[calendar[260]]
    assert len(daily)==280 and daily.loc[278,'box_exit']
    with pytest.raises(ValueError,match='independent/future'):
        research_symbol_rows(history,states,calendar,pd.DatetimeIndex([]),calendar[-2])
    with pytest.raises(ValueError,match='eligible signal keys'):
        research_symbol_rows(history,states,calendar,pd.DatetimeIndex(['2019-12-31']),calendar[-1])

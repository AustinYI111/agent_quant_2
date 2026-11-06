import numpy as np
import pandas as pd
import pytest
from ashare_mvp.financial_snapshot_history import merge_observed_financial_history,merge_dependency_observations,FUND_COLUMNS
from ashare_mvp.financial_dependencies import combine_financial_dependencies
from ashare_mvp.data import Dataset,fundamentals_asof


def frame(roe=.1,available='2026-10-20',observed='2026-10-20T01:00:00Z',period='2026-06-30'):
    return pd.DataFrame([dict(symbol='sh.600000',report_date=period,available_date=available,roe=roe,revenue_growth=.1,earnings_growth=.2,debt_ratio=np.nan,first_observed_at_utc=observed)])


def baseline():return frame(.05,'2025-11-01','2025-11-01T00:00:00Z','2025-09-30')[FUND_COLUMNS]


def visible(funds,date):return fundamentals_asof(Dataset(pd.DataFrame(),funds,pd.DatetimeIndex([date]),{}),pd.Timestamp(date)).iloc[0]


def test_refresh_not_yet_visible_keeps_prior_known_version_and_native_unknown():
    previous=frame();latest=frame(.2,'2026-10-22','2026-10-21T09:30:00Z').assign(debt_ratio=.4)
    funds=merge_observed_financial_history(baseline(),[previous,latest],now='2026-10-21T10:00:00Z')
    assert visible(funds,'2026-10-21').roe==.1 and pd.isna(visible(funds,'2026-10-21').debt_ratio)
    assert visible(funds,'2026-10-22').roe==.2 and visible(funds,'2026-10-22').debt_ratio==.4


def test_same_day_versions_choose_latest_actual_observation_independent_of_order():
    early=frame(.1,'2026-10-21','2026-10-21T01:00:00Z');late=frame(.3,'2026-10-21','2026-10-21T09:30:00Z')
    one=merge_observed_financial_history(baseline(),[early,late],now='2026-10-21T10:00:00Z')
    two=merge_observed_financial_history(baseline(),[late,early],now='2026-10-21T10:00:00Z')
    pd.testing.assert_frame_equal(one,two)
    assert visible(one,'2026-10-21').roe==.3


def test_new_revision_of_old_quarter_does_not_replace_newer_known_period():
    latest_old=frame(.9,'2026-10-21','2026-10-21T09:00:00Z','2026-03-31')
    funds=merge_observed_financial_history(baseline(),[frame(),latest_old],now='2026-10-21T10:00:00Z')
    assert visible(funds,'2026-10-21').report_date==pd.Timestamp('2026-06-30')
    assert visible(funds,'2026-10-21').roe==.1


def test_identical_observation_is_idempotent_but_conflicting_same_clock_rejected():
    old=frame();one=merge_observed_financial_history(baseline(),[old,old],now='2026-10-21T10:00:00Z')
    assert len(one)==2
    with pytest.raises(ValueError,match='conflicting'):
        merge_observed_financial_history(baseline(),[old,frame(.3)],now='2026-10-21T10:00:00Z')


@pytest.mark.parametrize('fault',['future','backdated','infinite','missing_clock'])
def test_unknown_or_future_observation_is_not_usable(fault):
    observed=frame()
    if fault=='future':observed['first_observed_at_utc']='2026-10-22T00:00:00Z'
    if fault=='backdated':observed['first_observed_at_utc']='2026-10-21T09:00:00Z'
    if fault=='infinite':observed['roe']=float('inf')
    if fault=='missing_clock':observed['first_observed_at_utc']=None
    with pytest.raises(ValueError):merge_observed_financial_history(baseline(),[observed],now='2026-10-21T10:00:00Z')


def test_combined_dependency_clock_is_maximum_of_income_and_balance():
    income=frame(.1,'2026-10-21','2026-10-21T01:00:00Z').drop(columns='debt_ratio')
    balance=income[['symbol','report_date','available_date']].assign(debt_ratio=.5,first_observed_at_utc='2026-10-21T09:30:00Z')
    for data in [income,balance]:
        for c in ['report_date','available_date']:data[c]=pd.to_datetime(data[c])
    result=combine_financial_dependencies(income,balance)
    assert result.first_observed_at_utc.iloc[0]==pd.Timestamp('2026-10-21T09:30:00Z')
    assert result.earnings_first_observed_at_utc.iloc[0]==pd.Timestamp('2026-10-21T01:00:00Z')
    assert result.balance_first_observed_at_utc.iloc[0]==pd.Timestamp('2026-10-21T09:30:00Z')


def test_missing_clock_of_used_balance_does_not_invent_combined_first_observation():
    income=frame(.1,'2026-10-21','2026-10-21T01:00:00Z').drop(columns='debt_ratio')
    balance=income[['symbol','report_date','available_date']].assign(debt_ratio=.5)
    for data in [income,balance]:
        for c in ['report_date','available_date']:data[c]=pd.to_datetime(data[c])
    result=combine_financial_dependencies(income,balance)
    assert pd.isna(result.first_observed_at_utc.iloc[0])
    with pytest.raises(ValueError,match='observation'):merge_observed_financial_history(baseline(),[result],now='2026-10-21T10:00:00Z')


def test_dependency_history_keeps_old_balance_until_new_version_is_visible():
    old_income=frame(.1).drop(columns='debt_ratio');new_income=frame(.2,'2026-10-21','2026-10-21T09:00:00Z').drop(columns='debt_ratio')
    old_balance=frame()[['symbol','report_date','available_date','first_observed_at_utc']].assign(debt_ratio=.5)
    new_balance=frame(.1,'2026-10-22','2026-10-21T09:30:00Z')[['symbol','report_date','available_date','first_observed_at_utc']].assign(debt_ratio=.6)
    income=merge_dependency_observations([old_income,new_income]);balances=merge_dependency_observations([old_balance,new_balance])
    result=combine_financial_dependencies(income,balances)
    assert visible(result,'2026-10-21').roe==.2 and visible(result,'2026-10-21').debt_ratio==.5
    assert visible(result,'2026-10-22').debt_ratio==.6


def test_explicit_new_unknown_balance_is_not_filled_from_an_older_version():
    income=merge_dependency_observations([frame().drop(columns='debt_ratio')])
    old=frame()[['symbol','report_date','available_date','first_observed_at_utc']].assign(debt_ratio=.5)
    new=frame(.1,'2026-10-21','2026-10-21T09:00:00Z')[['symbol','report_date','available_date','first_observed_at_utc']].assign(debt_ratio=np.nan)
    result=combine_financial_dependencies(income,merge_dependency_observations([old,new]))
    assert pd.isna(visible(result,'2026-10-21').debt_ratio)


def test_dependency_same_clock_conflict_is_rejected_before_join():
    old=frame().drop(columns='debt_ratio');changed=old.assign(roe=.9)
    with pytest.raises(ValueError,match='conflicting dependency'):merge_dependency_observations([old,changed])

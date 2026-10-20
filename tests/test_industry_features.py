import pandas as pd
import pytest
from ashare_mvp.industry_features import industry_snapshot_features


@pytest.fixture
def inputs():
    symbols=['issuer_'+str(index) for index in range(21)]
    snapshot=pd.DataFrame(dict(symbol=symbols,raw_momentum_63=[.2]*10+[0.]*10+[.1],raw_momentum_126=[.4]*10+[0.]*10+[.2],close_above_ma50=[1.]*10+[0.]*10+[1.]))
    membership=pd.DataFrame(dict(symbol=symbols,industry=['A']*10+['B']*10+[None],effective_date='2026-10-19',observed_at_utc='2026-10-20T06:00:00Z'))
    return snapshot,membership


def test_equal_weight_sector_relative_momentum_and_known_breadth(inputs):
    snapshot,membership=inputs
    result=industry_snapshot_features(snapshot,membership,'2026-10-20','2026-10-20T10:00:00Z')
    assert result.industry_relative_momentum_63.iloc[0]==pytest.approx(.1)
    assert result.industry_relative_momentum_63.iloc[10]==pytest.approx(-.1)
    assert result.industry_breadth_50.iloc[0]==1 and result.industry_breadth_50.iloc[10]==0
    assert pd.isna(result.industry_relative_momentum_63.iloc[20]) and not result.industry_known.iloc[20]


def test_current_membership_never_backfills_prior_signal_day(inputs):
    snapshot,membership=inputs
    result=industry_snapshot_features(snapshot,membership,'2026-10-19','2026-10-20T10:00:00Z')
    assert not result.industry_known.any()
    assert result.industry_breadth_50.isna().all()


def test_future_effective_or_future_observation_is_unavailable(inputs):
    snapshot,membership=inputs
    membership.loc[:9,'observed_at_utc']='2026-10-20T11:00:00Z'
    membership.loc[10:19,'effective_date']='2026-10-21'
    result=industry_snapshot_features(snapshot,membership,'2026-10-20','2026-10-20T10:00:00Z')
    assert not result.industry_known.any()


def test_ten_valid_members_required_for_each_metric_unknowns_are_not_zero(inputs):
    snapshot,membership=inputs
    snapshot.loc[0,'raw_momentum_63']=None
    result=industry_snapshot_features(snapshot,membership,'2026-10-20','2026-10-20T10:00:00Z')
    assert result.industry_relative_momentum_63.iloc[:10].isna().all()
    assert result.industry_relative_momentum_126.iloc[:10].notna().all()


def test_ambiguous_membership_and_missing_observation_clock_are_rejected(inputs):
    snapshot,membership=inputs
    with pytest.raises(ValueError,match='ambiguous'):
        industry_snapshot_features(snapshot,pd.concat([membership,membership.iloc[:1]]),'2026-10-20','2026-10-20T10:00:00Z')
    with pytest.raises(ValueError,match='evidence'):
        industry_snapshot_features(snapshot,membership.drop(columns='observed_at_utc'),'2026-10-20','2026-10-20T10:00:00Z')


def test_absent_membership_stays_unknown_instead_of_creating_a_fake_sector(inputs):
    snapshot,membership=inputs
    result=industry_snapshot_features(snapshot,membership.iloc[:0],'2026-10-20','2026-10-20T10:00:00Z')
    assert not result.industry_known.any() and result.industry_breadth_50.isna().all()

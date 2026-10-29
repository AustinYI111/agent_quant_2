import numpy as np
import pandas as pd
import pytest
from ashare_mvp.archived_industry_research import select_archived_research_memberships as select, archived_research_industry_features as features


@pytest.fixture
def inputs():
    symbols=['sz.'+str(n).zfill(6) for n in range(1,22)]
    snapshot=pd.DataFrame(dict(symbol=symbols,raw_momentum_63=[.2]*10+[0.]*10+[.1],raw_momentum_126=[.4]*10+[0.]*10+[.2],close_above_ma50=[1.]*10+[0.]*10+[np.nan]))
    rows=pd.DataFrame(dict(symbol=symbols[:20],industry=['A01']*10+['C39']*10,release_id='old'))
    changed=rows.iloc[1:].copy();changed['release_id']='new'
    memberships=pd.concat([rows,changed],ignore_index=True)
    releases=pd.DataFrame([
        dict(release_id='old',period_end='2019-12-31',published_date='2020-01-03',first_observed_at_utc='2026-10-22T10:00:00Z',source_sha256='a'*64,snapshot_kind='official_archived_industry_release',members_count=20),
        dict(release_id='new',period_end='2020-03-31',published_date='2020-04-04',first_observed_at_utc='2026-10-22T10:01:00Z',source_sha256='b'*64,snapshot_kind='official_archived_industry_release',members_count=19)])
    calendar=pd.DatetimeIndex(['2020-01-03','2020-01-06','2020-04-03','2020-04-06','2020-04-07','2020-12-31'])
    return snapshot,memberships,releases,calendar


def run(inputs,signal='2020-01-06'):
    s,m,r,c=inputs
    return features(s,m,r,c,signal,'2020-12-31','2026-10-23T02:00:00Z')


def test_original_observation_clock_is_retained_not_backdated(inputs):
    result=run(inputs)
    assert result.first_observed_at_utc.dropna().eq(pd.Timestamp('2026-10-22T10:00:00Z')).all()
    assert result.visible_date.dropna().eq(pd.Timestamp('2020-01-06')).all()
    assert result.attrs['genuine_historical_observation_claimed'] is False
    assert result.attrs['source_hashes_must_be_verified_by_caller'] is True


def test_reporting_period_and_publication_day_are_not_availability(inputs):
    result=run(inputs,'2020-01-03')
    assert not result.industry_known.any()
    assert result.industry_breadth_50.isna().all()


def test_equal_weight_raw_means_match_original_industry_definition(inputs):
    result=run(inputs)
    assert result.industry_relative_momentum_63.iloc[0]==pytest.approx(.1)
    assert result.industry_relative_momentum_126.iloc[10]==pytest.approx(-.2)
    assert result.industry_breadth_50.iloc[0]==1.
    assert result.industry_breadth_50.iloc[10]==0.
    assert pd.isna(result.industry_breadth_50.iloc[-1])


def test_latest_complete_snapshot_does_not_carry_removed_issuer(inputs):
    result=run(inputs,'2020-04-06')
    assert not result.industry_known.iloc[0]
    assert pd.isna(result.industry.iloc[0])
    assert result.release_id.dropna().eq('new').all()
    assert result.industry_relative_momentum_63.iloc[1:10].isna().all()


def test_future_release_does_not_affect_prior_signal(inputs):
    result=run(inputs,'2020-04-03')
    assert result.release_id.dropna().eq('old').all()


def test_truncated_release_cannot_look_like_complete_snapshot(inputs):
    s,m,r,c=inputs
    with pytest.raises(ValueError,match='truncated'):
        run((s,m.iloc[:-1],r,c))


def test_current_membership_schema_is_not_accepted(inputs):
    s,m,r,c=inputs;r=r.copy();r.loc[0,'snapshot_kind']='latest_current_membership'
    with pytest.raises(ValueError,match='current membership'):
        run((s,m,r,c))


@pytest.mark.parametrize('field,value',[
    ('first_observed_at_utc','2026-10-24T00:00:00Z'),
    ('period_end','2020-02-01'),
    ('published_date','2027-01-01'),
    ('source_sha256','invalid')])
def test_bad_source_identity_or_clocks_rejected(inputs,field,value):
    s,m,r,c=inputs;r=r.copy();r.loc[0,field]=value
    with pytest.raises(ValueError):run((s,m,r,c))


def test_release_order_does_not_change_shared_next_session_selection(inputs):
    s,m,r,c=inputs
    r=r.copy();r.loc[0,'published_date']='2020-04-03'
    first=run((s,m,r,c),'2020-04-06');second=run((s,m,r.iloc[::-1],c),'2020-04-06')
    pd.testing.assert_frame_equal(first,second)
    assert first.release_id.dropna().eq('new').all()


def test_duplicate_membership_and_unknown_release_rejected(inputs):
    s,m,r,c=inputs
    with pytest.raises(ValueError,match='membership keys'):run((s,pd.concat([m,m.iloc[:1]]),r,c))
    m=m.copy();m.loc[0,'release_id']='unbound'
    with pytest.raises(ValueError,match='membership keys'):run((s,m,r,c))


def test_independent_signal_and_calendar_rejected(inputs):
    s,m,r,c=inputs
    with pytest.raises(ValueError,match='independent'):
        select(m,r,c,'2026-01-01','2020-12-31','2026-10-23T02:00:00Z')
    with pytest.raises(ValueError,match='research-only'):
        run((s,m,r,c.append(pd.DatetimeIndex(['2026-01-01']))))


def test_ten_valid_members_required_separately_for_each_feature(inputs):
    s,m,r,c=inputs;s=s.copy();s.loc[0,'raw_momentum_63']=np.nan
    result=run((s,m,r,c))
    assert result.industry_relative_momentum_63.iloc[:10].isna().all()
    assert result.industry_relative_momentum_126.iloc[:10].notna().all()


def test_future_return_columns_have_no_effect_on_industry_features(inputs):
    s,m,r,c=inputs;original=run(inputs);s=s.assign(future_return=np.arange(len(s))*999.)
    pd.testing.assert_frame_equal(original,run((s,m,r,c)))

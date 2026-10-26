from copy import deepcopy
import numpy as np
import pandas as pd
import pytest
from ashare_mvp.industry_attribution import historical_labels,attribute_industries


def classification():
    cal=pd.bdate_range('2023-01-02','2023-01-10')
    releases=pd.DataFrame([dict(release_id='old',period_end='2022-12-31',published_date='2023-01-02',first_observed_at_utc='2026-10-20T00:00:00+00:00',source_sha256='a'*64,snapshot_kind='official_archived_industry_release',members_count=1),dict(release_id='new',period_end='2022-12-31',published_date='2023-01-04',first_observed_at_utc='2026-10-20T00:00:00+00:00',source_sha256='b'*64,snapshot_kind='official_archived_industry_release',members_count=1)])
    members=pd.DataFrame([dict(symbol='sh.600001',industry='C13',release_id='old'),dict(symbol='sh.600001',industry='C14',release_id='new')])
    return members,releases,cal


def ledgers():
    dates=pd.to_datetime(['2023-01-04','2023-01-05','2023-01-06'])
    a=pd.DataFrame(dict(date=dates,symbol=['sh.600001']*3,price_pnl=[0.,.05,-.01],gross_dividend_entitlement=[0.,0.,.02],commission_cost=[.00015,0.,.00003],slippage_cost=[.00025,0.,.00005],stamp_duty_cost=[0.,0.,.00002]))
    d=a.drop(columns='symbol').copy();d['opening_nav']=[1.,.9996,1.0496];d['nav_change']=[-.0004,.05,.0099];d['net_return']=d.nav_change/d.opening_nav;d['unit_beta_market_reference']=[0.,.005,-.011];d['price_residual_after_market']=d.price_pnl-d.unit_beta_market_reference
    p=pd.DataFrame(dict(date=dates,symbol=['sh.600001']*3,market_value=[.5,.55,.54]))
    return d,a,p


def run(d,a,p,members=None,releases=None):
    m,r,cal=classification()
    return attribute_industries(d,a,p,members if members is not None else m,releases if releases is not None else r,cal,'2023-01-10')


def test_open_interval_uses_membership_known_at_its_start_not_current_close():
    m,r,cal=classification();keys=ledgers()[1][['date','symbol']]
    labels=historical_labels(keys,m,r,cal,'2023-01-10')
    assert labels.industry.tolist()==['C13','C13','C14']
    assert labels.classification_date.tolist()==pd.to_datetime(['2023-01-03','2023-01-04','2023-01-05']).tolist()


def test_member_removed_from_latest_full_release_does_not_inherit_old_sector():
    m,r,cal=classification();m.loc[m.release_id.eq('new'),'symbol']='sh.600002'
    labels=historical_labels(ledgers()[1][['date','symbol']],m,r,cal,'2023-01-10')
    assert labels.industry.tolist()==['C13','C13','__UNKNOWN__']


def test_sector_price_dividend_cost_market_and_residual_sum_to_original_account():
    grouped,checks,labels=run(*ledgers())
    assert np.nanmax(np.abs(checks.drop(columns='date').to_numpy()))<1e-12
    assert grouped.net_pnl.sum()==pytest.approx(.0595)
    assert grouped.groupby('industry').net_pnl.sum().to_dict()==pytest.approx({'C13':.0496,'C14':.0099})
    assert grouped.allocated_market_reference.sum()==pytest.approx(-.006)
    bydate=grouped.groupby('date')
    assert (bydate.starting_equity_weight.sum()+bydate.cash_and_receivable_weight.first()).eq(1.).all()


def test_unknown_sector_keeps_actual_income_and_equity_exposure():
    m,r,cal=classification();m.loc[m.release_id.eq('new'),'symbol']='sh.600002'
    grouped,checks,labels=run(*ledgers(),members=m)
    unknown=grouped[grouped.industry.eq('__UNKNOWN__')].iloc[0]
    assert unknown.net_pnl==pytest.approx(.0099) and unknown.starting_equity_weight>0
    assert unknown.gross_dividend_entitlement==.02


@pytest.mark.parametrize('fault',['unknown_money','different_pnl','too_large_position','session_gap'])
def test_incomplete_or_inconsistent_ledgers_cannot_become_a_sector_explanation(fault):
    d,a,p=ledgers()
    if fault=='unknown_money':a.loc[1,'price_pnl']=np.nan
    elif fault=='different_pnl':a.loc[1,'price_pnl']=.06
    elif fault=='too_large_position':p.loc[0,'market_value']=2.
    elif fault=='session_gap':d=d.drop(index=1);a=a.drop(index=1);p=p.drop(index=1)
    with pytest.raises(ValueError):run(d,a,p)


def test_future_classification_does_not_change_earlier_contributions():
    d,a,p=ledgers();m,r,cal=classification()
    first,_,_=run(d,a,p)
    m.loc[m.release_id.eq('new'),'industry']='C15'
    changed,_,_=run(d,a,p,members=m)
    pd.testing.assert_frame_equal(first[first.date.lt('2023-01-06')],changed[changed.date.lt('2023-01-06')])


@pytest.mark.parametrize('calendar',[[],['2023-01-04','2023-01-04'],['2023-01-05','2023-01-04']])
def test_invalid_calendar_cannot_label_intervals(calendar):
    m,r,_=classification()
    with pytest.raises(ValueError,match='calendar'):
        historical_labels(ledgers()[1][['date','symbol']],m,r,calendar,'2023-01-10')

import numpy as np
import pandas as pd
import pytest
from ashare_mvp.candidate_portfolios import mature_schedule,paired_net_returns
from ashare_mvp.horizon_labels import label_interval,HORIZONS


def test_sparse_terminal_dates_read_without_inference_or_filtering_held_names(tmp_path):
    from ashare_mvp.candidate_portfolios import account_quotes
    dates=pd.bdate_range('2021-01-01',periods=5)
    raw=pd.DataFrame(dict(date=dates,symbol=['sh.600000']*5,open=10.,close=10.,preclose=10.,tradestatus=1,
        terminal_date=['','','2021-01-05','','']))
    raw.to_csv(tmp_path/'raw.csv',index=False)
    result=account_quotes(tmp_path/'raw.csv',{'sh.600000'},dates[0],dates[-1])
    assert len(result)==5 and result.terminal_date.notna().sum()==1
    assert result.loc[2,'terminal_date']==pd.Timestamp('2021-01-05')
    assert result.tradestatus.eq('1').all()


def test_annual_schedule_keeps_full_twelve_month_cycles_and_excludes_calendar_unmatured():
    cal=pd.bdate_range('2020-01-01','2025-12-31')
    signals=pd.DatetimeIndex(pd.Series(cal,index=cal).groupby(cal.to_period('M')).max())
    signals=signals[signals>='2021-07-30']
    scheduled,end=mature_schedule(signals,cal,'12m')
    for signal in scheduled:
        entry,exit=label_interval(cal,signal,HORIZONS['12m'])
        assert exit>=entry+pd.DateOffset(months=12)
    assert end==label_interval(cal,scheduled[-1],HORIZONS['12m'])[1]
    assert end<=cal[-1] and len(scheduled)==4
    assert all(label_interval(cal,a,HORIZONS['12m'])[1]<=label_interval(cal,b,HORIZONS['12m'])[0] for a,b in zip(scheduled,scheduled[1:]))


def test_short_grid_uses_full_mature_cycles_not_cropped_fold_exit():
    cal=pd.bdate_range('2021-01-01',periods=90)
    signals=cal[10::5]
    scheduled,end=mature_schedule(signals,cal,'5d')
    assert scheduled[-1]==cal[80] and end==cal[86]
    with pytest.raises(ValueError,match='no complete'):
        mature_schedule(cal[-2:],cal,'12m')


def test_paired_net_cost_diagnostic_includes_first_open_cost_and_requires_same_dates():
    dates=pd.bdate_range('2020-01-01',periods=2)
    a=pd.DataFrame(dict(date=dates,nav=[.9984,1.03]));b=pd.DataFrame(dict(date=dates,nav=[.9992,1.02]))
    excess,result=paired_net_returns(a,b)
    assert excess[0]==pytest.approx(-.0008)
    assert result['net_total_return_difference']==pytest.approx(.01)
    assert result['uncertainty_status']=='insufficient_calendar_history'
    b.loc[1,'date']+=pd.Timedelta(days=1)
    with pytest.raises(ValueError,match='identical sessions'):paired_net_returns(a,b)


def test_calendar_bootstrap_is_paired_seeded_and_does_not_resample_accounts_separately():
    dates=pd.bdate_range('2020-01-01',periods=260)
    b=pd.DataFrame(dict(date=dates,nav=np.cumprod(1+np.sin(np.arange(260))*.001)))
    a=b.copy()
    excess,result=paired_net_returns(a,b)
    assert np.array_equal(excess,np.zeros(260))
    assert result['family_adjusted_mean_daily_interval']==[0.,0.]
    a.nav*=np.exp(np.arange(260)*.0001)
    _,first=paired_net_returns(a,b);_,second=paired_net_returns(a,b)
    assert first==second and first['family_comparisons']==24

import json
import numpy as np
import pandas as pd
import pytest
from ashare_mvp.horizon_research import chronological_partitions,rebalance_signals,compare_index,train_horizon_models,HorizonResearchConfig,load_horizon_model
from ashare_mvp.horizon_labels import HORIZONS,label_interval


def sample_tables():
    cal=pd.bdate_range('2016-01-01','2025-12-31')
    signals=pd.Series(cal[cal.year<=2024]).groupby(cal[cal.year<=2024].to_period('M')).max().tolist()
    rng=np.random.default_rng(42);features=[];labels=[]
    for date in signals:
        for i in range(20):
            a,b=rng.normal(size=2);symbol=f'sh.{600000+i}'
            features.append(dict(date=date,symbol=symbol,available_date=date,roe=a,momentum_63=b))
            for key in HORIZONS:
                entry,end=label_interval(cal,date,HORIZONS[key]);value={'5d':a,'20d':-a,'12m':b}[key]/10
                labels.append(dict(date=date,symbol=symbol,horizon=key,entry_date=entry,label_end=end,target=value if pd.notna(end) else np.nan))
    return pd.DataFrame(features),pd.DataFrame(labels),cal


def test_partition_purges_actual_outcome_end_not_only_signal_date():
    x,y,cal=sample_tables();f=x.merge(y[y.horizon.eq('12m')],on=['date','symbol'])
    train,valid,test,cuts=chronological_partitions(f,cal,'2020-01-31','2022-01-31','2024-12-31')
    assert train.label_end.lt(cuts['validation_cutoff']).all()
    assert valid.label_end.lt(cuts['test_cutoff']).all()
    assert train.date.lt(pd.Timestamp('2020-01-31')).all() and test.date.ge(pd.Timestamp('2022-01-31')).all()
    assert not train.date.dt.year.eq(2019).all() # older observations are retained
    assert train.date.max()<pd.Timestamp('2019-03-01')
    assert len(test)>0


def test_future_features_and_mixed_horizons_rejected():
    x,y,cal=sample_tables();f=x.merge(y[y.horizon.eq('5d')],on=['date','symbol'])
    f.loc[0,'available_date']=f.date.iloc[0]+pd.Timedelta(days=1)
    with pytest.raises(ValueError,match='future feature'):chronological_partitions(f,cal,'2020-01-31','2022-01-31','2024-12-31')
    f=x.merge(y,on=['date','symbol'])
    with pytest.raises(ValueError,match='one horizon'):chronological_partitions(f,cal,'2020-01-31','2022-01-31','2024-12-31')


def test_rebalance_matches_trading_intervals_and_calendar_minimum_hold():
    cal=pd.bdate_range('2020-01-01','2023-12-31')
    for key in HORIZONS:
        selected=rebalance_signals(cal[:-1],cal,key)
        entries=[label_interval(cal,d,HORIZONS[key])[0] for d in selected]
        for a,b in zip(entries,entries[1:]):
            if key=='12m':assert b>=a+pd.DateOffset(months=12)
            else:assert cal.get_loc(b)-cal.get_loc(a)>=HORIZONS[key].length


def comparison_inputs():
    dates=pd.to_datetime(['2020-01-02','2021-01-04'])
    nav=pd.DataFrame({'date':dates,'nav':[.99,1.188]})
    trades=pd.DataFrame([dict(date=dates[0],side='buy',status='filled',notional=.99,cost=.01),
        dict(date=dates[1],side='sell',status='filled',notional=.2,cost=.002),
        dict(date=dates[1],side='buy',status='blocked',notional=.5,cost=0.)])
    bench=pd.DataFrame({'date':dates,'open':[100.,110.]})
    return nav,trades,bench


def test_comparison_uses_net_nav_costs_and_pre_cost_equity_turnover():
    nav,trades,bench=comparison_inputs();daily,m=compare_index(nav,trades,bench,'price_index')
    assert m['net_portfolio']['total_return']==pytest.approx(.188)
    assert m['benchmark']['total_return']==pytest.approx(.1)
    assert m['excess_total_return']==pytest.approx(.088)
    assert m['relative_wealth_return']==pytest.approx(.08)
    assert m['net_portfolio']['max_drawdown']==pytest.approx(-.01)
    assert m['paid_cost_initial_equity_units']==pytest.approx(.012)
    expected=.99+.2/1.19
    assert m['two_way_turnover_sum']==pytest.approx(expected)
    assert m['one_way_turnover_sum']==pytest.approx(expected/2)
    assert m['blocked_orders']==1 and m['filled_orders']==2
    assert m['one_way_turnover_ex_initial']==pytest.approx(.2/1.19/2)
    assert m['dividend_basis_warning'] is not None and daily.benchmark_nav.iloc[0]==1


def test_missing_benchmark_never_forward_filled_and_kind_mandatory():
    n,t,b=comparison_inputs()
    with pytest.raises(ValueError,match='missing portfolio'):compare_index(n,t,b.iloc[:1],'price_index')
    with pytest.raises(ValueError,match='explicit'):compare_index(n,t,b,'unknown')
    _,m=compare_index(n,t,b,'total_return_index');assert m['dividend_basis_warning'] is None


@pytest.mark.parametrize('column,value',[('cost',-.1),('cost',float('nan')),('notional',float('inf'))])
def test_invalid_cost_or_notional_rejected(column,value):
    n,t,b=comparison_inputs();t.at[0,column]=value
    with pytest.raises(ValueError,match='nonnegative'):compare_index(n,t,b,'price_index')


def test_blocked_order_cannot_have_paid_cost():
    n,t,b=comparison_inputs();t.at[2,'cost']=.001
    with pytest.raises(ValueError,match='status/cost'):compare_index(n,t,b,'price_index')


def test_bankruptcy_does_not_fabricate_information_ratio():
    dates=pd.bdate_range('2024-01-01',periods=3)
    n=pd.DataFrame({'date':dates,'nav':[1.,0.,0.]});b=pd.DataFrame({'date':dates,'open':[100.,100.,100.]})
    _,m=compare_index(n,pd.DataFrame(),b,'price_index')
    assert m['net_portfolio']['total_return']==-1 and m['information_ratio'] is None
    json.dumps(m,allow_nan=False)


def test_three_models_rolling_audit_reload_and_future_test_independence(tmp_path):
    x,y,cal=sample_tables();config=HorizonResearchConfig(top_k=5,min_train_dates=12,candidate_estimators=(8,12))
    ranks,audit,summary=train_horizon_models(x,y,['roe','momentum_63'],cal,'2020-01-31','2022-01-31','2024-12-31',tmp_path/'first',config)
    assert set(ranks.horizon)==set(HORIZONS) and summary['models']==9
    assert pd.to_datetime(audit.max_label_end).lt(pd.to_datetime(audit.cutoff)).all()
    assert pd.to_datetime(audit.max_feature_date).lt(pd.to_datetime(audit.date)).all()
    for key in HORIZONS:
        directory=tmp_path/'first'/key/'20220131'
        model=load_horizon_model(directory,key,['roe','momentum_63'])
        current=x[x.date.eq(pd.Timestamp('2022-01-31'))]
        expected=ranks[ranks.horizon.eq(key)&ranks.date.eq(pd.Timestamp('2022-01-31'))].set_index('symbol').reindex(current.symbol).score
        assert np.allclose(model.predict(current[['roe','momentum_63']]),expected,rtol=0,atol=1e-12)
    altered=y.copy();mask=altered.date.ge(pd.Timestamp('2024-01-01'));altered.loc[mask,'target']*= -100
    later,_,_=train_horizon_models(x,altered,['roe','momentum_63'],cal,'2020-01-31','2022-01-31','2024-12-31',tmp_path/'second',config)
    pd.testing.assert_frame_equal(ranks,later)
    with pytest.raises(ValueError,match='contract differs'):load_horizon_model(tmp_path/'first'/'5d'/'20220131','20d',['roe','momentum_63'])
    model_file=tmp_path/'first'/'5d'/'20220131'/'model.txt';model_file.write_text(model_file.read_text()+'changed')
    with pytest.raises(ValueError,match='hash mismatch'):load_horizon_model(model_file.parent,'5d',['roe','momentum_63'])


def test_missing_test_label_keeps_forecast_candidate_and_wrong_units_fail(tmp_path):
    x,y,cal=sample_tables();config=HorizonResearchConfig(top_k=5,candidate_estimators=(8,))
    drop=y.date.eq(pd.Timestamp('2022-01-31'))&y.symbol.eq('sh.600000')&y.horizon.eq('5d')
    trimmed=y[~drop]
    ranks,_,_=train_horizon_models(x,trimmed,['roe','momentum_63'],cal,'2020-01-31','2022-01-31','2024-12-31',tmp_path/'ok',config)
    assert len(ranks[ranks.date.eq(pd.Timestamp('2022-01-31'))&ranks.horizon.eq('5d')])==20
    wrong=y.copy();wrong.loc[wrong.horizon.eq('5d'),'label_end']=wrong.loc[wrong.horizon.eq('5d'),'entry_date']+pd.DateOffset(months=5)
    with pytest.raises(ValueError,match='declared trading/calendar'):train_horizon_models(x,wrong,['roe','momentum_63'],cal,'2020-01-31','2022-01-31','2024-12-31',tmp_path/'bad',config)



def test_portfolio_coupler_executes_all_three_schedules_and_net_costs(tmp_path):
    from ashare_mvp.data import Dataset
    from ashare_mvp.horizon_research import backtest_horizon_rankings
    cal=pd.bdate_range('2020-01-01','2022-12-30')
    prices=[];ranks=[]
    for i,date in enumerate(cal):
        for j in range(4):
            prices.append(dict(date=date,symbol=f'sh.{600000+j}',total_return_open=(1+j/10)*(1.0001**i),can_buy=True,can_sell=True))
            for key in HORIZONS:ranks.append(dict(date=date,symbol=f'sh.{600000+j}',horizon=key,score=(j if i//60%2 else -j),available_date=date))
    data=Dataset(pd.DataFrame(prices),pd.DataFrame(),cal,{'synthetic':True})
    bench=pd.DataFrame({'date':cal,'open':100.*(1.0001**np.arange(len(cal)))})
    result=backtest_horizon_rankings(data,pd.DataFrame(ranks),bench,'price_index',tmp_path,top_k=2)
    assert set(result)==set(HORIZONS)
    assert result['5d']['scheduled_rebalances']>result['20d']['scheduled_rebalances']>result['12m']['scheduled_rebalances']
    for key,m in result.items():
        assert m['paid_cost_initial_equity_units']>0 and m['filled_orders']>0
        daily=pd.read_csv(tmp_path/key/'index_comparison.csv')
        assert daily.nav.iloc[0]<daily.benchmark_nav.iloc[0]==1.
        assert (tmp_path/key/'orders.csv').exists()
    long=pd.read_csv(tmp_path/'12m'/'scheduled_top20.csv',parse_dates=['date'])
    dates=long.date.drop_duplicates().tolist();entries=[cal[cal.searchsorted(d,side='right')] for d in dates]
    assert all(b>=a+pd.DateOffset(months=12) for a,b in zip(entries,entries[1:]))


def test_short_horizon_cannot_claim_five_day_backtest_from_monthly_forecasts(tmp_path):
    from ashare_mvp.data import Dataset
    from ashare_mvp.horizon_research import backtest_horizon_rankings
    cal=pd.bdate_range('2024-01-01',periods=80)
    p=pd.DataFrame({'date':cal,'symbol':'sh.600000','total_return_open':10.,'can_buy':True,'can_sell':True})
    ranks=pd.DataFrame([dict(date=cal[i],symbol='sh.600000',horizon='5d',score=1.,available_date=cal[i]) for i in [0,20,40,60]])
    with pytest.raises(ValueError,match='prescribed short-horizon'):backtest_horizon_rankings(Dataset(p,pd.DataFrame(),cal,{}),ranks,pd.DataFrame({'date':cal,'open':100.}),'price_index',tmp_path,top_k=1)


def test_runner_requires_exploratory_and_sealed_benchmark_before_data_access(tmp_path):
    from ashare_mvp.horizon_research import run_three_horizon_research
    with pytest.raises(ValueError,match='exploratory'):run_three_horizon_research('missing','missing',tmp_path/'out','2020-01-31','2022-01-31','2024-12-31')
    bench=tmp_path/'bench';bench.mkdir();(bench/'raw_prices.csv').write_text('date,open\n2024-01-01,100\n')
    (bench/'summary.json').write_text(json.dumps(dict(schema='benchmark_raw_v1',raw_sha256='a'*64)))
    with pytest.raises(ValueError,match='benchmark hash'):run_three_horizon_research('missing',bench,tmp_path/'out','2020-01-31','2022-01-31','2024-12-31',exploratory=True)



def test_initial_allocation_exclusion_handles_cash_waiting_before_first_signal():
    n,t,b=comparison_inputs()
    day=pd.Timestamp('2020-01-01')
    n=pd.concat([pd.DataFrame({'date':[day],'nav':[1.]}),n],ignore_index=True)
    b=pd.concat([pd.DataFrame({'date':[day],'open':[95.]}),b],ignore_index=True)
    _,m=compare_index(n,t,b,'price_index')
    assert m['initial_allocation_date']=='2020-01-02'
    assert m['one_way_turnover_ex_initial']==pytest.approx(.2/1.19/2)

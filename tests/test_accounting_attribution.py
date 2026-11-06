import numpy as np
import pandas as pd
import pytest
from test_paper_tracking import book
from ashare_mvp.paper_tracking import replay_paper_books
from ashare_mvp.accounting_attribution import attribute_open_account


def outputs(args):
    folder=args['out']/'5d'
    return pd.read_csv(folder/'open_account_attribution.csv'),pd.read_csv(folder/'position_history.csv'),pd.read_csv(folder/'asset_attribution.csv')


def test_costs_and_daily_positions_sum_to_each_actual_account_change(book):
    args,_,_=book;result=replay_paper_books(**args)['books']['5d'];daily,positions,assets=outputs(args)
    assert result['accounting_identity_verified'] and result['accounting_max_absolute_error']<1e-10
    assert len(daily)==5 and len(positions)==100
    np.testing.assert_allclose(daily.price_pnl,0,atol=1e-14)
    np.testing.assert_allclose(daily.nav_change,-daily.total_execution_cost,atol=1e-14)
    np.testing.assert_allclose(daily.total_execution_cost,daily.commission_cost+daily.slippage_cost+daily.stamp_duty_cost,atol=1e-14)
    np.testing.assert_allclose(assets.groupby('date').commission_cost.sum(),daily.commission_cost,atol=1e-14)
    assert daily.total_execution_cost.iloc[0]>0 and daily.stamp_duty_cost.sum()==0


def test_dividend_entitlement_and_later_cash_payment_are_not_double_income(book):
    args,_,cal=book;raw=args['raw_prices'];raw.loc[raw.date.ge(cal[2]),['open','close','preclose']]=9.
    args['actions']=pd.DataFrame([dict(symbol=s,ex_date=cal[2],available_date=cal[1],pay_date=cal[3],cash_per_share=1.,share_multiplier=1.) for s in raw.symbol.unique()])
    replay_paper_books(**args);daily,_,_=outputs(args)
    assert daily.gross_dividend_entitlement.iloc[1]>0 and daily.dividends_paid_cash.iloc[3]>0
    assert daily.gross_dividend_entitlement.iloc[3]==0
    np.testing.assert_allclose(daily.price_pnl+daily.gross_dividend_entitlement,0,atol=1e-14)
    np.testing.assert_allclose(daily.receivable_change,daily.gross_dividend_entitlement-daily.dividends_paid_cash,atol=1e-14)


def test_ex_date_new_buyer_receives_no_previous_holders_dividend(book):
    args,_,cal=book;raw=args['raw_prices'];raw.loc[raw.date.ge(cal[1]),['open','close','preclose']]=9.
    args['actions']=pd.DataFrame([dict(symbol=s,ex_date=cal[1],available_date=cal[0],pay_date=cal[2],cash_per_share=1.,share_multiplier=1.) for s in raw.symbol.unique()])
    replay_paper_books(**args);daily,_,_=outputs(args)
    assert daily.gross_dividend_entitlement.sum()==daily.dividends_paid_cash.sum()==0


def test_split_doubles_shares_without_creating_price_profit(book):
    args,_,cal=book;raw=args['raw_prices'];raw.loc[raw.date.ge(cal[2]),['open','close','preclose']]=5.
    args['actions']=pd.DataFrame([dict(symbol=s,ex_date=cal[2],available_date=cal[1],pay_date=cal[2],cash_per_share=0.,share_multiplier=2.) for s in raw.symbol.unique()])
    replay_paper_books(**args);daily,positions,_=outputs(args)
    np.testing.assert_allclose(daily.price_pnl,0,atol=1e-14)
    shares=positions.groupby('date').shares.sum()
    assert np.isclose(shares.iloc[1],2*shares.iloc[0])


def test_market_reference_and_remaining_price_change_are_exact_but_not_alpha(book):
    args,_,cal=book;raw=args['raw_prices'];raw.loc[raw.date.ge(cal[2]),['open','close']]=10.5;raw.loc[raw.date.ge(cal[3]),'preclose']=10.5
    args['benchmark'].loc[args['benchmark'].date.ge(cal[2]),'open']=10.2
    result=replay_paper_books(**args)['books']['5d'];daily,_,_=outputs(args)
    assert 'not causal alpha' in result['market_reference_method']
    np.testing.assert_allclose(daily.unit_beta_market_reference+daily.price_residual_after_market,daily.price_pnl,atol=1e-14)
    assert np.isclose(daily.net_return.iloc[1],.05) and np.isclose(daily.unit_beta_market_reference.iloc[1]/daily.opening_nav.iloc[1],.02)
    assert result['industry_attribution_status'].startswith('pending')


def test_missing_benchmark_is_unknown_and_not_zero_market_exposure(book):
    args,_,_=book;args['benchmark']=None;replay_paper_books(**args);daily,_,_=outputs(args)
    assert daily.unit_beta_market_reference.isna().all() and daily.price_residual_after_market.isna().all()


@pytest.mark.parametrize('fault',['nav','cash','held_quote','cost'])
def test_inconsistent_assets_prices_or_costs_are_rejected(book,fault):
    args,_,cal=book;replay_paper_books(**args);folder=args['out']/'5d'
    nav=pd.read_csv(folder/'open_nav.csv');orders=pd.read_csv(folder/'orders.csv');quotes=args['raw_prices'].copy()
    if fault=='nav':nav.loc[1,'nav']+=.1
    if fault=='cash':nav.loc[1,'cash']+=.1
    if fault=='held_quote':quotes=quotes[~(quotes.date.eq(cal[2])&quotes.symbol.eq('sh.600000'))]
    if fault=='cost':orders.loc[0,'cost']+=.01
    with pytest.raises(ValueError,match='reconcile|held valuation|cost contract'):
        attribute_open_account(nav,orders,quotes,args['actions'],args['benchmark'])

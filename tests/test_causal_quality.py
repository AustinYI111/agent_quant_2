from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from ashare_mvp.causal_quality import causal_feature_index,quality_inputs
from ashare_mvp.horizon_labels import forward_horizon_labels
from ashare_mvp.factors import technical_panel
from ashare_mvp.pipeline import simulate_monthly
from ashare_mvp.model import Config


def empty_actions():return pd.DataFrame(columns=['symbol','ex_date','available_date','pay_date','cash_per_share','share_multiplier'])


def quotes(n=600,fault=300):
    cal=pd.bdate_range('2018-01-02',periods=n)
    values=np.where(np.arange(n)>=fault,9.,10.)
    raw=pd.DataFrame(dict(date=cal,symbol='sh.600000',open=values,close=values,preclose=values,tradestatus='1'))
    if fault>=n:raw['preclose']=10.
    return raw,empty_actions(),cal


def test_features_are_prefix_invariant_and_rolling_warmup_resets_at_observable_fault():
    raw,a,cal=quotes();prefix=280
    earlier,_=causal_feature_index(raw.iloc[:prefix],a,cal[:prefix],.01)
    full,summary=causal_feature_index(raw,a,cal,.01)
    pd.testing.assert_frame_equal(full.iloc[:prefix].reset_index(drop=True),earlier)
    assert summary['reference_faults']==1 and summary['segments']==2
    p=full.rename(columns={'feature_close':'close'});p['volume']=1.
    data=SimpleNamespace(prices=p,metadata={'quality_policy':{'schema':'causal_quality_research_v1'}})
    tech=technical_panel(data)
    assert tech.loc[299,'momentum_252_skip21']==0.
    assert pd.isna(tech.loc[300,'momentum_252_skip21']) and pd.isna(tech.loc[551,'momentum_252_skip21'])
    assert tech.loc[552,'momentum_252_skip21']==0.


def test_future_terminal_does_not_remove_historical_features_or_make_future_prices():
    raw,a,cal=quotes(n=40,fault=100)
    full,_=causal_feature_index(raw.iloc[:30],a,cal,.01)
    assert len(full)==30 and full.date.max()==cal[29]
    assert full.feature_close.eq(10).all()


def test_unknown_label_interval_retains_rows_and_later_fault_does_not_erase_earlier_label():
    raw,a,cal=quotes(n=45,fault=25)
    signals=cal[[0,20,24]]
    result=forward_horizon_labels(raw,a,cal,signals,['5d'],.01,unknown_intervals='record')
    assert len(result)==3
    assert result.status.tolist()==['valued','unresolved_interval','valued']
    assert result.target.iloc[0]==0. and pd.isna(result.target.iloc[1]) and result.target.iloc[2]==0.
    with pytest.raises(ValueError,match='unreconciled'):
        forward_horizon_labels(raw,a,cal,signals,['5d'],.01)


def test_terminal_label_is_unknown_at_mature_date_not_zero_or_truncated():
    raw,a,cal=quotes(n=45,fault=100);b=raw.copy();b['symbol']='sh.600001'
    ended=raw.iloc[:30].copy();ended['terminal_date']=cal[30]
    allrows=pd.concat([ended,b],ignore_index=True)
    result=forward_horizon_labels(allrows,a,cal,cal[[0,25]],['5d'],.01,unknown_intervals='record')
    item=result[result.symbol.eq('sh.600000')&result.date.eq(cal[25])].iloc[0]
    assert item.status=='unresolved_terminal_valuation' and item.label_end==cal[31] and pd.isna(item.target)
    assert result[result.symbol.eq('sh.600000')&result.date.eq(cal[0])].iloc[0].target==0.


def test_only_historical_feature_pairs_limit_requests_not_future_label_values():
    raw,a,cal=quotes(n=45,fault=25);pairs=pd.DataFrame({'date':cal[[0,20]],'symbol':'sh.600000'})
    result=forward_horizon_labels(raw,a,cal,cal[[0,20,24]],['5d'],.01,unknown_intervals='record',eligible_pairs=pairs)
    assert len(result)==2 and result.status.tolist()==['valued','unresolved_interval']


def account(fault_symbol='sh.600001',terminal=None,missing=False):
    cal=pd.bdate_range('2024-01-02',periods=8);parts=[]
    for symbol in ['sh.600000','sh.600001']:
        values=np.array([10.]*8)
        if symbol==fault_symbol:values[4:]=9.
        f=pd.DataFrame(dict(date=cal,symbol=symbol,open=values,close=values,preclose=values,tradestatus='1'))
        if symbol=='sh.600000' and terminal is not None:f['terminal_date']=cal[terminal]
        if symbol=='sh.600000' and missing:f=f.iloc[:4]
        parts.append(f)
    raw=pd.concat(parts,ignore_index=True);p=raw[['date','symbol']].copy()
    p['total_return_open']=999.;p['can_buy']=True;p['can_sell']=True
    return SimpleNamespace(prices=p,calendar=cal),raw,empty_actions(),cal


CFG=Config(commission_bps=0,slippage_bps=0,sell_tax_bps=0)


def test_unheld_fault_cannot_block_real_holdings_but_strict_default_still_rejects():
    data,raw,a,cal=account();picks=pd.DataFrame(dict(date=[cal[0]],symbol='sh.600000',target_weight=1.))
    nav,_=simulate_monthly(data,picks,CFG,raw,a,.01,causal_quality_research=True)
    assert nav.nav.tolist()==pytest.approx([1.]*7)
    with pytest.raises(ValueError):simulate_monthly(data,picks,CFG,raw,a,.01)


@pytest.mark.parametrize('kind',['reference','terminal','missing'])
def test_actual_unresolved_holdings_always_stop(kind):
    data,raw,a,cal=account(fault_symbol='sh.600000' if kind=='reference' else 'none',terminal=4 if kind=='terminal' else None,missing=kind=='missing')
    picks=pd.DataFrame(dict(date=[cal[0]],symbol='sh.600000',target_weight=1.))
    with pytest.raises(ValueError,match='held'):
        simulate_monthly(data,picks,CFG,raw,a,.01,causal_quality_research=True)


def test_already_exited_zero_position_is_not_valued_after_missing_future_quotes():
    data,raw,a,cal=account(fault_symbol='none',missing=True)
    picks=pd.DataFrame(dict(date=cal[[0,2]],symbol='sh.600000',target_weight=[1.,0.]))
    nav,_=simulate_monthly(data,picks,CFG,raw,a,.01,causal_quality_research=True)
    assert nav.nav.tolist()==pytest.approx([1.]*7) and nav.iloc[-1].positions==0


def test_new_buyer_at_reference_fault_does_not_inherit_old_holder_entitlements():
    data,raw,a,cal=account(fault_symbol='sh.600000')
    picks=pd.DataFrame(dict(date=[cal[3]],symbol='sh.600000',target_weight=1.))
    nav,_=simulate_monthly(data,picks,CFG,raw,a,.01,causal_quality_research=True)
    assert nav.nav.tolist()==pytest.approx([1.]*4)


def test_missing_new_buy_is_blocked_without_substitution_or_future_filtering():
    data,raw,a,cal=account(fault_symbol='none',missing=True)
    picks=pd.DataFrame(dict(date=[cal[3]],symbol='sh.600000',target_weight=1.))
    nav,trades=simulate_monthly(data,picks,CFG,raw,a,.01,causal_quality_research=True)
    assert nav.cash.eq(1).all() and trades.status.eq('blocked').all()


def test_bad_event_clocks_cannot_be_hidden_by_quality_mode():
    raw,a,cal=quotes(n=20,fault=100)
    a=pd.DataFrame([dict(symbol='sh.600000',ex_date=cal[5],available_date=cal[6],pay_date=cal[7],cash_per_share=1.,share_multiplier=1.)])
    with pytest.raises(ValueError,match='visible'):quality_inputs(raw,a,cal,.01)


def test_explicit_export_retains_terminal_history_and_seals_raw_inputs(tmp_path):
    from test_market_dataset import fixture,prepare,digest
    from ashare_mvp.public_conversion import load_public_data,_public_event_inputs
    paths,cal=fixture(tmp_path);events=paths[3]
    import json
    file=events/'actions.csv';f=pd.read_csv(file);f['cash_per_share']=.5;f.to_csv(file,index=False)
    meta=json.loads((events/'summary.json').read_text());meta['actions_sha256']=digest(file)
    (events/'summary.json').write_text(json.dumps(meta))
    result=prepare(tmp_path,paths,causal_quality_research=True)
    assert result['status']=='prepared_causal_quality_research' and result['issue_counts']['unreconciled_adjustment']==1
    data=load_public_data(tmp_path/'dataset');raw,_,_=_public_event_inputs(tmp_path/'dataset',data)
    assert len(data.prices)==8 and len(raw)==8 and data.prices.quality_segment.max()==2
    assert data.metadata['price_basis']=='causal_segment_feature_index' and not data.metadata['model_ready']
    assert data.metadata['converted_hashes']['event_raw_prices.csv']==digest(tmp_path/'dataset/event_raw_prices.csv')


def test_terminal_day_new_buy_is_blocked_even_if_execution_flags_are_optimistic():
    data,raw,a,cal=account(fault_symbol='none',terminal=4)
    picks=pd.DataFrame(dict(date=[cal[3]],symbol='sh.600000',target_weight=1.))
    nav,trades=simulate_monthly(data,picks,CFG,raw,a,.01,causal_quality_research=True)
    assert nav.cash.eq(1).all() and trades.status.eq('blocked').all()


def test_causal_account_cannot_use_feature_index_as_portfolio_wealth():
    data,raw,a,cal=account()
    picks=pd.DataFrame(dict(date=[cal[0]],symbol='sh.600000',target_weight=1.))
    with pytest.raises(ValueError,match='requires raw'):
        simulate_monthly(data,picks,CFG,causal_quality_research=True)


def test_forged_quality_flags_cannot_hide_held_reference_fault():
    data,raw,a,cal=account(fault_symbol='sh.600000')
    raw['reference_fault']=False;raw['quality_segment']=1;raw['quality_break']=False
    picks=pd.DataFrame(dict(date=[cal[0]],symbol='sh.600000',target_weight=1.))
    with pytest.raises(ValueError,match='unreconciled held'):
        simulate_monthly(data,picks,CFG,raw,a,.01,causal_quality_research=True)


def test_known_feature_uncertainty_does_not_delete_prediction_candidates():
    from ashare_mvp.public_conversion import public_snapshot
    date=pd.Timestamp('2024-01-04')
    panel=pd.DataFrame([dict(date=date,symbol='sh.600000',eligible=True,is_st=False,volume=100.,
        listing_date=pd.Timestamp('2010-01-01'),momentum_63=np.nan,momentum_126=np.nan,
        momentum_252_skip21=np.nan,volatility_63=np.nan)])
    financials=pd.DataFrame([dict(symbol='sh.600000',report_date=pd.Timestamp('2023-09-30'),
        available_date=pd.Timestamp('2023-11-01'),roe=.1)])
    data=SimpleNamespace(fundamentals=financials,metadata={'quality_policy':{'schema':'causal_quality_research_v1'}})
    assert len(public_snapshot(data,panel,date))==1
    data.metadata={}
    assert public_snapshot(data,panel,date).empty


def test_each_horizon_is_attempted_and_unresolved_hold_keeps_valid_prefix_only(tmp_path):
    import json
    from ashare_mvp.horizon_research import backtest_horizon_rankings
    from ashare_mvp.causal_quality import IncompleteHorizonBacktests
    cal=pd.bdate_range('2018-01-02',periods=400);parts=[]
    for symbol in ['sh.600000','sh.600001']:
        values=np.full(len(cal),10.)
        if symbol=='sh.600000':values[100:]=9.
        parts.append(pd.DataFrame(dict(date=cal,symbol=symbol,open=values,close=values,preclose=values,tradestatus='1')))
    raw=pd.concat(parts,ignore_index=True);p=raw[['date','symbol']].copy()
    p['total_return_open']=999.;p['can_buy']=True;p['can_sell']=True
    data=SimpleNamespace(prices=p,calendar=cal,metadata={'quality_policy':{'schema':'causal_quality_research_v1'}})
    records=[]
    for h,signals,symbol in [('5d',cal[::5],'sh.600000'),('20d',cal[::20],'sh.600001'),('12m',cal[:1],'sh.600001')]:
        records.extend(dict(date=date,symbol=symbol,horizon=h,score=1.,available_date=date) for date in signals)
    benchmark=pd.DataFrame(dict(date=cal,open=100.))
    out=tmp_path/'portfolios'
    with pytest.raises(IncompleteHorizonBacktests) as failure:
        backtest_horizon_rankings(data,pd.DataFrame(records),benchmark,'price_index',out,top_k=1,
            commission_bps=0,slippage_bps=0,sell_tax_bps=0,raw_prices=raw,corporate_actions=empty_actions(),reference_tick_size=.01)
    assert [row['horizon'] for row in failure.value.errors]==['5d']
    status=json.loads((out/'incomplete.json').read_text())
    assert status['completed_horizons']==['12m','20d'] and not (out/'summary.json').exists()
    assert (out/'12m/summary.json').exists() and (out/'20d/summary.json').exists()
    assert not (out/'5d/summary.json').exists()
    partial=pd.read_csv(out/'5d/partial_valid_nav.csv',parse_dates=['date'])
    assert partial.date.max()==cal[99] and partial.nav.eq(1.).all()
    problem=json.loads((out/'5d/incomplete.json').read_text())
    assert problem['symbol']=='sh.600000' and problem['date']==str(cal[100].date())
    assert problem['holdings']['sh.600000']>0 and problem['reason']=='reference'

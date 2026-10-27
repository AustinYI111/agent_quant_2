import numpy as np
import pandas as pd
import pytest
from ashare_mvp.eligible_equal_weight import equal_weight_picks,evaluation_windows
from ashare_mvp.data import Dataset
from ashare_mvp.model import Config
from ashare_mvp.pipeline import simulate_monthly
from ashare_mvp.causal_quality import HeldValuationError


def samples():
    cal=pd.bdate_range('2023-01-02',periods=4)
    pool=pd.DataFrame([dict(date=d,symbol='sh.'+str(600000+i),available_date=cal[0],factor=np.nan,score=-i,target=np.nan) for d in cal[[0,2]] for i in range(25)])
    return pool,cal


def test_control_keeps_all_eligible_names_and_native_missing_factors_without_rank_or_outcome_filter():
    pool,cal=samples();result=equal_weight_picks(pool,cal[[0,2]],cal,cal[-1])
    assert len(result)==50 and result.groupby('date').size().eq(25).all()
    np.testing.assert_allclose(result.target_weight,.04)
    np.testing.assert_allclose(result.groupby('date').target_weight.sum(),1.)
    pool['score']=1e9;pool['target']=100
    pd.testing.assert_frame_equal(result,equal_weight_picks(pool,cal[[0,2]],cal,cal[-1]))


def test_membership_changes_only_with_original_decision_keys():
    pool,cal=samples();pool=pool.drop(pool[(pool.date.eq(cal[2]))&pool.symbol.eq('sh.600024')].index)
    result=equal_weight_picks(pool,cal[[0,2]],cal,cal[-1])
    assert result[result.date.eq(cal[2])].target_weight.eq(1/24).all()
    assert result[result.date.eq(cal[0])].target_weight.eq(1/25).all()


@pytest.mark.parametrize('fault',['duplicate','future_availability','non_a','missing_date','small_pool','independent_date','unordered_calendar'])
def test_ambiguous_invisible_or_incomplete_eligible_pools_are_rejected(fault):
    pool,cal=samples();signals=cal[[0,2]];end=cal[-1]
    if fault=='duplicate':pool=pd.concat([pool,pool.iloc[[0]]],ignore_index=True)
    elif fault=='future_availability':pool.loc[0,'available_date']=cal[-1]
    elif fault=='non_a':pool.loc[0,'symbol']='bj.830001'
    elif fault=='missing_date':pool=pool[pool.date.ne(cal[2])]
    elif fault=='small_pool':pool=pool[pool.symbol.lt('sh.600010')]
    elif fault=='independent_date':end=pd.Timestamp('2022-12-31')
    elif fault=='unordered_calendar':cal=cal[::-1]
    with pytest.raises(ValueError):equal_weight_picks(pool,signals,cal,end)


def book(kind):
    cal=pd.bdate_range('2023-01-02',periods=4);names=['sh.600001','sh.600002']
    pool=pd.DataFrame(dict(date=[cal[0]]*2,symbol=names,available_date=[cal[0]]*2))
    picks=equal_weight_picks(pool,cal[:1],cal,cal[-1],minimum_members=2)
    raw=pd.DataFrame([dict(date=d,symbol=s,open=10.,close=10.,preclose=10.,tradestatus='1',terminal_date=pd.NaT) for d in cal for s in names])
    actions=pd.DataFrame(columns=['symbol','ex_date','available_date','pay_date','cash_per_share','share_multiplier'])
    if kind=='dividend':
        raw.loc[raw.date.ge(cal[2]),['open','close','preclose']]=9.
        actions=pd.DataFrame([dict(symbol=s,ex_date=cal[2],available_date=cal[0],pay_date=cal[3],cash_per_share=1.,share_multiplier=1.) for s in names])
    elif kind=='fault':raw.loc[raw.date.eq(cal[2])&raw.symbol.eq(names[1]),['preclose','open','close']]=1.
    elif kind=='blocked':raw.loc[raw.date.eq(cal[1])&raw.symbol.eq(names[1]),'tradestatus']='0'
    prices=raw[['date','symbol']].copy();tradable=raw.tradestatus.eq('1')
    prices['can_buy']=tradable;prices['can_sell']=tradable;prices['total_return_open']=raw.open
    return Dataset(prices,pd.DataFrame(),cal,dict(synthetic=True)),picks,raw,actions


def test_full_pool_receives_actual_gross_dividend_without_artificial_price_loss_or_final_liquidation():
    data,picks,raw,actions=book('dividend')
    nav,orders=simulate_monthly(data,picks,Config(top_k=2),raw,actions,.01,causal_quality_research=True)
    assert len(nav)==3 and len(orders)==2 and orders.side.eq('buy').all()
    np.testing.assert_allclose(nav.nav,1/1.0008,atol=1e-12)


def test_unknown_positive_holding_stops_control_and_keeps_partial_ledger():
    data,picks,raw,actions=book('fault')
    with pytest.raises(HeldValuationError) as caught:
        simulate_monthly(data,picks,Config(top_k=2),raw,actions,.01,causal_quality_research=True)
    assert caught.value.symbol=='sh.600002' and caught.value.reason=='reference'
    assert len(caught.value.nav)==1 and set(caught.value.holdings)==set(picks.symbol)


def test_blocked_new_eligible_name_keeps_cash_instead_of_renormalizing_to_survivor():
    data,picks,raw,actions=book('blocked')
    nav,orders=simulate_monthly(data,picks,Config(top_k=2),raw,actions,.01,causal_quality_research=True)
    assert orders.status.eq('blocked').sum()==1
    assert orders.loc[orders.status.eq('filled'),'notional'].iloc[0]==pytest.approx(.5)
    assert nav.cash.iloc[0]==pytest.approx(.4996)


@pytest.mark.parametrize('external',[False,True])
@pytest.mark.parametrize('unresolved',[False,True])
def test_real_control_producer_runs_all_three_full_holding_calendars_and_seals_outputs(tmp_path,monkeypatch,external,unresolved):
    # Synthetic in-memory test values exercise the real account engine; only
    # the external sealed-protocol loader is replaced, never price accounting.
    import json
    from pathlib import Path
    import ashare_mvp.eligible_equal_weight as module
    from ashare_mvp.phase2_protocol import sha256
    from ashare_mvp.horizon_labels import label_interval,HORIZONS
    cal=pd.bdate_range('2023-01-02','2024-02-01');names=['sh.'+str(600000+i) for i in range(20)]
    for name in ['source','dataset','experiment/models','benchmark','ashare_mvp']:(tmp_path/name).mkdir(parents=True,exist_ok=True)
    for name in ['eligible_equal_weight.py','candidate_portfolios.py','pipeline.py','corporate_actions.py','causal_quality.py','costs.py','public_data.py']:
        (tmp_path/'ashare_mvp'/name).write_bytes(Path(module.__file__).with_name(name).read_bytes())
    pd.DataFrame({'date':cal}).to_csv(tmp_path/'dataset/calendar.csv',index=False)
    raw=pd.DataFrame([dict(date=d,symbol=s,open=10.,close=10.,preclose=10.,tradestatus='1',terminal_date=pd.NaT) for d in cal for s in names])
    if unresolved:raw.loc[raw.date.eq(cal[2])&raw.symbol.eq(names[1]),['open','close','preclose']]=1.
    raw.to_csv(tmp_path/'dataset/event_raw_prices.csv',index=False)
    metadata=dict(synthetic=False,quality_policy={'schema':'causal_quality_research_v1'},converted_hashes={n:sha256(tmp_path/'dataset'/n) for n in ['calendar.csv','event_raw_prices.csv']})
    (tmp_path/'dataset/metadata.json').write_text(json.dumps(metadata),encoding='utf8')
    pd.DataFrame(dict(date=[cal[0]]*20,symbol=names,available_date=[cal[0]]*20)).to_csv(tmp_path/'experiment/factor_samples.csv',index=False)
    (tmp_path/'experiment/models/summary.json').write_text(json.dumps(dict(input_frame_sha256={'samples':sha256(tmp_path/'experiment/factor_samples.csv')})),encoding='utf8')
    pd.DataFrame(dict(date=cal,code='sh.000300',open=10.,high=10.,low=10.,close=10.)).to_csv(tmp_path/'benchmark/raw_prices.csv',index=False)
    (tmp_path/'benchmark/summary.json').write_text(json.dumps(dict(return_kind='price_index',raw_sha256=sha256(tmp_path/'benchmark/raw_prices.csv'))),encoding='utf8')
    pd.DataFrame(columns=['symbol','ex_date','available_date','pay_date','cash_per_share','share_multiplier']).to_csv(tmp_path/'source/effective_valuation_actions.csv',index=False)
    records=[]
    for h in ['5d','20d','12m']:
        entry,last=label_interval(cal,cal[0],HORIZONS[h]);assert pd.notna(last)
        for trees in [30,60]:
            folder=tmp_path/'source'/h/str(trees)/'baseline';folder.mkdir(parents=True)
            pd.DataFrame(dict(date=[cal[0]]*20,symbol=names,target_weight=.05)).to_csv(folder/'scheduled_top20.csv',index=False)
            pd.DataFrame(dict(date=cal[(cal>=entry)&(cal<=last)],nav=1.)).to_csv(folder/'daily_nav.csv',index=False)
            for group in ['baseline','market_state','oneil_box','industry','combined']:
                records.append(dict(horizon=h,n_estimators=trees,group=group,status='complete_calendar_mature_ranking_account',requested_end=str(last.date())))
    source_hashes={p.relative_to(tmp_path).as_posix():sha256(p) for parent in ['dataset','experiment','benchmark'] for p in (tmp_path/parent).rglob('*') if p.is_file()}
    family=dict(status='full_family_ranking_accounts_evaluated_selection_pending',protocol_sha256='sealed_fixture',accounts=records,source_hashes=source_hashes,code_hashes={},independent_data_used=False,model_selection_performed=False,formal_model_promoted=False,output_hashes={p.relative_to(tmp_path/'source').as_posix():sha256(p) for p in (tmp_path/'source').rglob('*.csv')})
    (tmp_path/'source/manifest.json').write_text(json.dumps(family),encoding='utf8')
    protocol=dict(horizons=['5d','20d','12m'],research_end=str(cal[-1].date()),execution={'top_k':20})
    monkeypatch.setattr(module,'load_seal',lambda *args:(protocol,{'protocol_sha256':'sealed_fixture'}))
    output_root=tmp_path.with_name(tmp_path.name+'_external') if external else None
    result=module.evaluate_equal_weight(tmp_path,'source','dataset','experiment','benchmark','sealed','out',output_root=output_root)
    target=(output_root if external else tmp_path)/'out'
    assert result['status']=='eligible_equal_weight_attempts_finished' and len(result['accounts'])==3
    if unresolved:
        assert all(r['status']=='incomplete_held_valuation' and 'metrics' not in r and r['date']==str(cal[2].date()) and r['holdings'][names[1]]>0 for r in result['accounts'])
        assert not (target/'12m/daily_nav.csv').exists()
        assert len(pd.read_csv(target/'12m/partial_valid_nav.csv'))==1
        return
    assert all(r['status']=='complete_calendar_mature_equal_weight_control' for r in result['accounts'])
    annual=next(r for r in result['accounts'] if r['horizon']=='12m')
    assert pd.Timestamp(annual['requested_end'])>=cal[1]+pd.DateOffset(months=12)
    assert len(annual['evaluation_windows'])>1 and annual['evaluation_windows'][-1]['requested_full_endpoint'] and not annual['evaluation_windows'][0]['requested_full_endpoint']
    for h in protocol['horizons']:
        trades=pd.read_csv(target/h/'orders.csv');assert trades.side.eq('buy').all() and len(trades)==20
    assert result['independent_data_used'] is False and result['formal_model_promoted'] is False
    for name,digest in result['output_hashes'].items():assert sha256(target/name)==digest


def test_computation_windows_expand_to_original_full_endpoint_without_shortening():
    cal=pd.bdate_range('2021-01-01',periods=300)
    assert evaluation_windows(cal,cal[0],cal[-1])==[cal[62],cal[125],cal[251],cal[-1]]
    assert evaluation_windows(cal,cal[0],cal[10])==[cal[10]]
    with pytest.raises(ValueError):evaluation_windows(cal,cal[0],pd.Timestamp('2022-12-31'))

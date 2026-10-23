from copy import deepcopy
import numpy as np
import pandas as pd
import pytest
from ashare_mvp.data import Dataset
from ashare_mvp.factors import technical_panel
from ashare_mvp.streaming_feature_index import ExchangeCalendar,StreamingFeatureIndex
from ashare_mvp.streaming_snapshot import iter_seed_bars,advance_feature_snapshot


def inputs(kind='cash'):
    dates=pd.bdate_range('2024-01-02',periods=305);symbol='sh.600000';calendar=ExchangeCalendar(dates)
    raw=pd.DataFrame(dict(symbol=symbol,date=dates,open=10.,close=10.,preclose=10.,tradestatus='1',volume=1000.,is_st=0,trading_symbol=symbol,pe_ttm=10.,pb_mrq=2.))
    raw.loc[300:,['open','close','preclose']]=8.33 if kind=='shares' else 9.9
    event=dict(symbol=symbol,ex_date=dates[300],available_date=dates[299],pay_date=dates[302],cash_per_share=0. if kind=='shares' else .1,share_multiplier=1.2 if kind=='shares' else 1.)
    events={} if kind=='missing_event' else {dates[300]:event}
    machine=StreamingFeatureIndex(symbol,calendar,.01);bars=[];state=None
    for index,row in enumerate(raw.to_dict('records')):
        result=machine.advance(row,events.get(row['date']))
        bars.append(dict(date=row['date'],symbol=symbol,quality_segment=result['quality_segment'],close=result['feature_close'],volume=1000.,listing_date=pd.Timestamp('2010-01-01'),is_st=False,eligible=True,earnings_yield=.1,book_yield=.5,trading_symbol=symbol))
        if index==299:state=machine.snapshot()
    old=pd.DataFrame(bars[:300]).tail(253)
    expected=technical_panel(Dataset(pd.DataFrame(bars),pd.DataFrame(),dates,dict(quality_policy={'schema':'causal_quality_research_v1'}))).tail(1)
    return symbol,old,state,raw.iloc[300:].copy(),events,calendar,expected,machine.snapshot()


@pytest.mark.parametrize('kind',['cash','shares','missing_event'])
def test_latest_features_match_full_causal_history_without_mutating_seed(kind):
    symbol,old,state,new,events,cal,expected,final=inputs(kind);before=deepcopy(state)
    actual_state,latest,audit=advance_feature_snapshot(symbol,old,state,new,events,cal,'2010-01-01',new.date.iloc[-1])
    assert actual_state==final and state==before
    columns=['close','momentum_63','momentum_126','momentum_252_skip21','volatility_63','high_252','volume_ma50']
    np.testing.assert_allclose(latest[columns].to_numpy(),expected[columns].to_numpy(),rtol=0,atol=1e-12,equal_nan=True)
    assert latest.earnings_yield.iloc[0]==.1 and latest.book_yield.iloc[0]==.5
    if kind=='missing_event':
        assert len(audit)==1 and audit.reference_fault.iloc[0]
        assert pd.isna(latest.momentum_63.iloc[0])
    else:assert audit.empty


@pytest.mark.parametrize('fault',['future','issuer','unknown_st','volume','reversed','seed_future'])
def test_inference_rejects_future_or_untrustworthy_raw_inputs(fault):
    symbol,old,state,new,events,cal,_,_=inputs()
    if fault=='future':new.loc[new.index[-1],'date']=cal.dates[-1]+pd.Timedelta(days=1)
    if fault=='issuer':new['symbol']='sh.600001'
    if fault=='unknown_st':new['is_st']='unknown'
    if fault=='volume':new['volume']=np.nan
    if fault=='reversed':new=new.iloc[::-1]
    if fault=='seed_future':old.loc[old.index[-1],'date']=cal.dates[-1]
    with pytest.raises(ValueError):advance_feature_snapshot(symbol,old,state,new,events,cal,'2010-01-01',cal.dates[-1])


def test_seed_chunk_boundaries_retain_complete_issuer_groups(tmp_path):
    _,old,_,_,_,_,_,_=inputs()
    first=old.tail(3);second=old.tail(2).assign(symbol='sh.600001',trading_symbol='sh.600001')
    file=tmp_path/'seed.csv';pd.concat([first,second],ignore_index=True).to_csv(file,index=False)
    groups=list(iter_seed_bars(file,chunksize=2))
    assert [key for key,_ in groups]==['sh.600000','sh.600001']
    assert [len(group) for _,group in groups]==[3,2]
    pd.concat([second,first],ignore_index=True).to_csv(file,index=False)
    with pytest.raises(ValueError,match='sorted|repeats'):list(iter_seed_bars(file,chunksize=2))


def test_suspended_missing_volume_is_preserved_and_excluded_without_changing_raw():
    symbol,old,state,new,events,cal,_,_=inputs()
    new.loc[new.index[-1],['tradestatus','volume']]=['0',np.nan]
    before=new.copy(deep=True);audit=[]
    _,latest,_=advance_feature_snapshot(symbol,old,state,new,events,cal,'2010-01-01',cal.dates[-1],volume_audit=audit)
    pd.testing.assert_frame_equal(new,before)
    assert pd.isna(latest.volume.iloc[0]) and pd.isna(latest.volume_ma50.iloc[0])
    assert not latest.eligible.iloc[0]
    assert len(audit)==1 and audit[0]['handling']=='preserved_native_nan'
    assert audit[0]['date']==cal.dates[-1] and audit[0]['ranking_eligible'] is False


@pytest.mark.parametrize('volume',[-1.,float('inf'),1.])
def test_halted_status_does_not_hide_invalid_or_positive_volume(volume):
    symbol,old,state,new,events,cal,_,_=inputs()
    new.loc[new.index[-1],['tradestatus','volume']]=['0',volume]
    with pytest.raises(ValueError,match='volume'):
        advance_feature_snapshot(symbol,old,state,new,events,cal,'2010-01-01',cal.dates[-1])


def test_new_issuer_empty_seed_keeps_numeric_native_missing_features():
    from ashare_mvp.streaming_snapshot import FEATURE_COLUMNS
    symbol,_,_,new,_,cal,_,_=inputs('missing_event')
    _,latest,_=advance_feature_snapshot(symbol,pd.DataFrame(columns=FEATURE_COLUMNS),None,new,{},cal,'2026-01-01',cal.dates[-1])
    for column in ['close','volume','earnings_yield','book_yield','momentum_63','momentum_126','momentum_252_skip21','volatility_63']:
        assert pd.api.types.is_numeric_dtype(latest[column]),column
    assert pd.isna(latest.momentum_63.iloc[0])
    assert latest.earnings_yield.iloc[0]==.1

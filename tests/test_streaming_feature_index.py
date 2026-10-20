import copy
import json
import numpy as np
import pandas as pd
import pytest

from ashare_mvp.causal_quality import causal_feature_index
from ashare_mvp.streaming_feature_index import ExchangeCalendar,StreamingFeatureIndex


def sample(gap=False,unexplained=False,initial_halt=False,late_payment=False):
    calendar=pd.bdate_range('2024-01-02',periods=20)
    event=dict(symbol='sh.600000',ex_date=calendar[4],available_date=calendar[3],pay_date=calendar[12 if late_payment else 6],cash_per_share=1.,share_multiplier=1.5,reference_cash_per_share=.8,reference_share_multiplier=1.4)
    bars=[];previous=10.
    for index,date in enumerate(calendar):
        preclose=round((previous-.8)/1.4,2) if index==4 else previous
        if unexplained and index==10:preclose=round(previous*.8,2)
        halted=index==6 or initial_halt and index<2
        opening=0. if halted else preclose+.02
        close=preclose if halted else preclose+.05
        bars.append(dict(symbol='sh.600000',date=date,open=opening,close=close,preclose=preclose,tradestatus='0' if halted else '1'))
        previous=close
    frame=pd.DataFrame(bars)
    if gap:frame=frame[frame.date.ne(calendar[15])]
    return calendar,frame,pd.DataFrame([event])


def run(calendar,prices,events,restore_every=False):
    contract=ExchangeCalendar(calendar)
    machine=StreamingFeatureIndex('sh.600000',contract,.01)
    lookup={row['ex_date']:row for row in events.to_dict('records')}
    results=[]
    for row in prices.to_dict('records'):
        results.append(machine.advance(row,lookup.get(row['date'])))
        if restore_every:
            machine=StreamingFeatureIndex('sh.600000',contract,.01,json.loads(json.dumps(machine.snapshot(),allow_nan=False)))
    return pd.DataFrame(results),machine


@pytest.mark.parametrize('options',[{},dict(gap=True),dict(unexplained=True),dict(initial_halt=True),dict(unexplained=True,late_payment=True),dict(gap=True,unexplained=True,initial_halt=True)])
def test_streaming_matches_original_causal_price_engine_with_restore_after_every_bar(options):
    calendar,prices,events=sample(**options)
    expected,_=causal_feature_index(prices,events,calendar,.01)
    actual,_=run(calendar,prices,events,True)
    for name in ['quality_segment','quality_break','reference_fault']:
        assert actual[name].tolist()==expected[name].tolist()
    np.testing.assert_allclose(actual.feature_close,expected.feature_close,rtol=0,atol=1e-12,equal_nan=True)
    np.testing.assert_allclose(actual.feature_total_return_open,expected.feature_total_return_open,rtol=0,atol=1e-12,equal_nan=True)


def test_pending_cash_survives_restore_and_is_not_reinvested_on_pay_date():
    calendar,prices,events=sample()
    _,machine=run(calendar,prices.iloc[:7],events)
    snapshot=json.loads(json.dumps(machine.snapshot()))
    assert snapshot['receivables'] and snapshot['cash']==0
    restored=StreamingFeatureIndex('sh.600000',calendar,.01,snapshot)
    resumed=restored.advance(prices.iloc[7].to_dict())
    expected,_=causal_feature_index(prices,events,calendar,.01)
    assert resumed['feature_close']==pytest.approx(expected.feature_close.iloc[7],abs=1e-12)
    assert not restored.snapshot()['receivables']


def test_calendar_extension_is_allowed_but_changing_old_sessions_is_not():
    calendar,prices,events=sample()
    _,machine=run(calendar,prices.iloc[:8],events)
    state=machine.snapshot()
    StreamingFeatureIndex('sh.600000',calendar.append(pd.bdate_range(calendar[-1]+pd.Timedelta(days=1),periods=2)),.01,state)
    with pytest.raises(ValueError,match='calendar changed'):
        StreamingFeatureIndex('sh.600000',calendar.delete(1),.01,state)


@pytest.mark.parametrize('field,value',[('shares',-1),('cash',float('nan')),('symbol','sz.000001'),('reference_tick_size',.001)])
def test_incompatible_or_damaged_state_is_rejected(field,value):
    calendar,prices,events=sample()
    _,machine=run(calendar,prices.iloc[:3],events)
    state=machine.snapshot();state[field]=value
    with pytest.raises(ValueError):StreamingFeatureIndex('sh.600000',calendar,.01,state)


def test_invalid_or_replayed_input_never_mutates_prior_state():
    calendar,prices,events=sample()
    _,machine=run(calendar,prices.iloc[:4],events)
    before=machine.snapshot()
    bad=prices.iloc[4].to_dict();bad['close']=0
    with pytest.raises(ValueError):machine.advance(bad,events.iloc[0].to_dict())
    assert machine.snapshot()==before
    with pytest.raises(ValueError,match='advance strictly'):machine.advance(prices.iloc[3].to_dict())
    assert machine.snapshot()==before


def test_missing_cash_payment_or_external_rights_are_not_guessed():
    calendar,prices,events=sample()
    _,machine=run(calendar,prices.iloc[:4],events)
    before=machine.snapshot()
    for update in [dict(pay_date=None),dict(rights_cash_required=1.)]:
        event=events.iloc[0].to_dict()|update
        with pytest.raises(ValueError):machine.advance(prices.iloc[4].to_dict(),event)
        assert machine.snapshot()==before

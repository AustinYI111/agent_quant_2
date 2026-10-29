import json
import pandas as pd
import pytest
from ashare_mvp.benchmark_extension import extend_price_benchmark
from ashare_mvp.phase2_protocol import sha256


@pytest.fixture
def inputs(tmp_path):
    dates=pd.bdate_range('2025-12-29',periods=5)
    frame=pd.DataFrame(dict(date=dates,code='sh.000300',open=10.,high=11.,low=9.,close=10.5))
    previous=tmp_path/'previous';previous.mkdir()
    frame.iloc[:3].to_csv(previous/'raw_prices.csv',index=False)
    (previous/'summary.json').write_text(json.dumps(dict(schema='benchmark_raw_v1',return_kind='price_index',request=dict(symbol='sh.000300'),raw_sha256=sha256(previous/'raw_prices.csv'))),encoding='utf8')
    recent=tmp_path/'recent.csv';frame.iloc[1:].to_csv(recent,index=False)
    calendar=tmp_path/'calendar.csv';pd.DataFrame(dict(date=dates)).to_csv(calendar,index=False)
    return dict(previous=previous,recent_csv=recent,recent_sha256=sha256(recent),calendar_path=calendar,out=tmp_path/'out')


def test_matching_overlap_extends_benchmark_and_preserves_price_kind(inputs):
    proof=extend_price_benchmark(**inputs)
    assert proof['rows']==5 and proof['new_rows']==2 and proof['overlap_rows']==2
    assert proof['return_kind']=='price_index' and not proof['model_ready']


def test_changed_historical_overlap_is_rejected_before_publication(inputs):
    frame=pd.read_csv(inputs['recent_csv']);frame.loc[0,'close']=999
    frame.to_csv(inputs['recent_csv'],index=False);inputs['recent_sha256']=sha256(inputs['recent_csv'])
    with pytest.raises(ValueError,match='overlapping index prices differ'):
        extend_price_benchmark(**inputs)
    assert not inputs['out'].exists()


def test_missing_benchmark_session_is_not_forward_filled(inputs):
    frame=pd.read_csv(inputs['recent_csv']).iloc[:-1];frame.to_csv(inputs['recent_csv'],index=False)
    inputs['recent_sha256']=sha256(inputs['recent_csv'])
    with pytest.raises(ValueError,match='complete supplied calendar'):
        extend_price_benchmark(**inputs)


def test_source_hash_change_fails_closed(inputs):
    inputs['recent_csv'].write_text('changed')
    with pytest.raises(ValueError,match='source hash'):
        extend_price_benchmark(**inputs)

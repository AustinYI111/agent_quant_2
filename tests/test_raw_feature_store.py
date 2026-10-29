import json
import sqlite3
import pandas as pd
import pytest
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.raw_feature_store import build_raw_feature_store,issuer_raw_quotes,iter_issuer_raw_quotes


@pytest.fixture
def inputs(tmp_path):
    baseline=tmp_path/'baseline';baseline.mkdir()
    pd.DataFrame(dict(date=['2025-12-30','2025-12-31'])).to_csv(baseline/'calendar.csv',index=False)
    pd.DataFrame(dict(date=['2025-12-30','2025-12-31'],symbol='sh.600000',open=[10,11],close=[11,12],preclose=[10,11],tradestatus='1',quality_segment=1)).to_csv(baseline/'event_raw_prices.csv',index=False)
    meta=dict(schema='public_ratios_v1',price_basis='causal_segment_feature_index',synthetic=True,converted_hashes={name:sha256(baseline/name) for name in ['calendar.csv','event_raw_prices.csv']})
    (baseline/'metadata.json').write_text(json.dumps(meta),encoding='utf8')
    market=tmp_path/'market';market.mkdir()
    pd.DataFrame(dict(calendar_date=['2026-01-05'],is_trading_day='1')).to_csv(market/'calendar.csv',index=False)
    pd.DataFrame(dict(date='2026-01-05',code=['sh.600000','sh.689009'],open=[12,20],close=[13,21],preclose=[12,20],tradestatus='1',volume=1000,isST='0',peTTM=10,pbMRQ=2,adjustflag='3')).to_csv(market/'daily_20260105.csv',index=False)
    manifest=dict(schema='market_daily_raw_v1',status='downloaded_raw',synthetic=True,files={name:dict(sha256=sha256(market/name),rows=2 if name.startswith('daily') else 1) for name in ['calendar.csv','daily_20260105.csv']})
    (market/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')
    changes=tmp_path/'changes.json'
    changes.write_text(json.dumps([dict(old_symbol='sz.000022',new_symbol='sz.001872',announcement_date='2018-12-21',effective_date='2018-12-26',share_ratio=1,source_url='https://example.test/explicit-synthetic-fixture')]),encoding='utf8')
    return dict(baseline=baseline,markets=[market],code_changes=changes,out=tmp_path/'store',chunksize=1)


def rewrite_market(inputs,change):
    market=inputs['markets'][0]
    frame=pd.read_csv(market/'daily_20260105.csv',dtype=str)
    change(frame)
    frame.to_csv(market/'daily_20260105.csv',index=False)
    manifest=json.loads((market/'manifest.json').read_text())
    manifest['files']['daily_20260105.csv']['sha256']=sha256(market/'daily_20260105.csv')
    (market/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')


def test_index_retains_raw_values_unknown_history_fields_and_explicit_exclusions(inputs):
    proof=build_raw_feature_store(**inputs)
    assert proof['old_rows']==2 and proof['new_rows']==1 and proof['excluded_rows']==1
    assert proof['synthetic'] and not proof['prediction_ready']
    frame=issuer_raw_quotes(inputs['out'],'sh.600000')
    assert frame.close.tolist()==[11,12,13]
    assert frame.iloc[:2].volume.isna().all() and frame.iloc[:2].trading_symbol.isna().all()
    assert frame.iloc[-1].trading_symbol=='sh.600000' and pd.isna(frame.iloc[-1].quality_segment)
    assert frame.iloc[-1].pe_ttm==10 and frame.iloc[-1].pb_mrq==2 and frame.iloc[-1].is_st==0
    assert frame.iloc[:2].is_st.isna().all()
    assert pd.read_csv(inputs['out']/'excluded_quotes.csv').code.tolist()==['sh.689009']
    assert sha256(inputs['out']/'quotes.sqlite')==proof['database_sha256']
    with pytest.raises(ValueError,match='new raw'):
        build_raw_feature_store(**inputs)


def test_hash_mismatch_cannot_publish_store(inputs):
    (inputs['baseline']/'event_raw_prices.csv').write_text('changed')
    with pytest.raises(ValueError,match='baseline raw evidence'):
        build_raw_feature_store(**inputs)
    assert not inputs['out'].exists()


@pytest.mark.parametrize('column,value',[('adjustflag','2'),('isST','unknown'),('tradestatus','unknown'),('date','2025-12-31')])
def test_wrong_basis_states_or_overlapping_history_fail_closed(inputs,column,value):
    rewrite_market(inputs,lambda frame:frame.__setitem__(column,value))
    with pytest.raises(ValueError):
        build_raw_feature_store(**inputs)
    assert json.loads((inputs['out']/'manifest.json').read_text())['status']=='failed'
    with pytest.raises(ValueError,match='incomplete'):
        issuer_raw_quotes(inputs['out'],'sh.600000')


def test_duplicate_source_dates_never_overwrite_existing_rows(inputs):
    inputs['markets']*=2
    with pytest.raises(ValueError,match='coverage differs'):
        build_raw_feature_store(**inputs)


def test_code_change_uses_verified_stable_issuer_and_keeps_trading_code(inputs):
    rewrite_market(inputs,lambda frame:frame.loc.__setitem__((0,'code'),'sz.001872'))
    proof=build_raw_feature_store(**inputs)
    frame=issuer_raw_quotes(inputs['out'],'sz.000022')
    assert len(frame)==1 and frame.iloc[0].trading_symbol=='sz.001872'
    assert proof['new_rows']==1


def test_tampered_disk_store_cannot_be_read_as_verified(inputs):
    build_raw_feature_store(**inputs)
    assert [symbol for symbol,_ in iter_issuer_raw_quotes(inputs['out'])]==['sh.600000']
    with sqlite3.connect(inputs['out']/'quotes.sqlite') as connection:
        connection.execute('UPDATE quotes SET close=999')
    with pytest.raises(ValueError,match='evidence changed'):
        issuer_raw_quotes(inputs['out'],'sh.600000')

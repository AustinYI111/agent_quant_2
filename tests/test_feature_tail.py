import json
import numpy as np
import pandas as pd
import pytest

from ashare_mvp.data import Dataset
from ashare_mvp.factors import default_library, technical_panel
from ashare_mvp.feature_tail import load_feature_tail
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.public_conversion import public_snapshot


@pytest.fixture
def source(tmp_path):
    calendar = pd.bdate_range('2020-01-01', periods=700)
    pieces = []
    for number, symbol in enumerate(['sh.600000', 'sz.000001', 'sh.688001']):
        rng = np.random.default_rng(number+42)
        frame = pd.DataFrame(dict(date=calendar, symbol=symbol, quality_segment=1,
            close=np.exp(np.cumsum(rng.normal(0, .01, len(calendar)))), volume=rng.integers(1000, 10000, len(calendar)).astype(float),
            is_st=False, eligible=True, listing_date=pd.Timestamp('2000-01-01'), earnings_yield=.05+number*.01,
            book_yield=.5+number*.1, trading_symbol=symbol))
        if number == 2:
            frame.loc[640:, 'quality_segment'] = 2
        pieces.append(frame)
    prices = pd.concat(pieces, ignore_index=True)
    funds = pd.DataFrame(dict(symbol=['sh.600000', 'sz.000001', 'sh.688001'], report_date=pd.Timestamp('2022-03-31'), available_date=pd.Timestamp('2022-04-29'), roe=[.1,.2,.3], revenue_growth=[.3,.2,.1], earnings_growth=[.2,.5,.1], debt_ratio=[.6,.4,.3]))
    prices.to_csv(tmp_path/'prices.csv', index=False)
    funds.to_csv(tmp_path/'fundamentals.csv', index=False)
    pd.DataFrame(dict(date=calendar)).to_csv(tmp_path/'calendar.csv', index=False)
    metadata = dict(schema='public_ratios_v1', certified=False, synthetic=True, price_basis='causal_segment_feature_index', quality_policy=dict(schema='causal_quality_research_v1'), converted_hashes={name:sha256(tmp_path/name) for name in ['prices.csv','fundamentals.csv','calendar.csv']})
    (tmp_path/'metadata.json').write_text(json.dumps(metadata), encoding='utf8')
    # Compare against the exact original CSV parser, not pre-serialization values.
    full = Dataset(pd.read_csv(tmp_path/'prices.csv', parse_dates=['date','listing_date']), pd.read_csv(tmp_path/'fundamentals.csv', parse_dates=['report_date','available_date']), calendar, metadata)
    return tmp_path, full


@pytest.mark.parametrize('chunksize', [53, 257, 100000])
def test_tail_matches_original_features_across_chunk_and_segment_boundaries(source, chunksize):
    root, full = source
    date = full.calendar[-1]
    tail = load_feature_tail(root, date, chunksize)
    assert tail.prices.groupby(['symbol', 'quality_segment']).size().max() == 253
    original = default_library().compute(public_snapshot(full, technical_panel(full), date), oneil=True)
    bounded = default_library().compute(public_snapshot(tail, technical_panel(tail), date), oneil=True)
    pd.testing.assert_frame_equal(original, bounded, rtol=0, atol=1e-11)
    assert pd.isna(bounded.loc['sh.688001', 'momentum_63'])


def test_future_rows_do_not_enter_historical_feature_tail(source):
    root, full = source
    date = full.calendar[645]
    data = load_feature_tail(root, date, 199)
    assert data.prices.date.max() == date and data.calendar[-1] == date
    original = default_library().compute(public_snapshot(full, technical_panel(full), date))
    bounded = default_library().compute(public_snapshot(data, technical_panel(data), date))
    pd.testing.assert_frame_equal(original, bounded, rtol=0, atol=1e-11)


def test_input_corruption_and_unsealed_basis_block_tail(source):
    root, full = source
    (root/'prices.csv').write_text('tampered', encoding='utf8')
    with pytest.raises(ValueError, match='hash mismatch'):
        load_feature_tail(root)
    metadata = full.metadata | dict(price_basis='unadjusted')
    (root/'metadata.json').write_text(json.dumps(metadata), encoding='utf8')
    with pytest.raises(ValueError, match='causal public-ratio'):
        load_feature_tail(root)

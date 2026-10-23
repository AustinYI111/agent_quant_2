"""Bounded-memory feature loading; not a valuation or execution dataset."""
from pathlib import Path
import json
import pandas as pd

from .data import Dataset
from .phase2_protocol import day, sha256


def load_feature_tail(directory, asof=None, chunksize=100000):
    """Hash-check sealed inputs and retain 253 bars per causal price segment.

    The longest original feature uses shift(252). No adjustment/eligibility rule
    or factor implementation is changed. Historical holdings must use the full
    raw-share event accounting path, never this truncated feature-only view.
    """
    if isinstance(chunksize, bool) or not isinstance(chunksize, int) or chunksize < 1:
        raise ValueError('positive integer chunk size required')
    root = Path(directory)
    metadata = json.loads((root/'metadata.json').read_text(encoding='utf8'))
    if (metadata.get('schema') != 'public_ratios_v1' or metadata.get('certified') is not False
            or metadata.get('quality_policy', {}).get('schema') != 'causal_quality_research_v1'
            or metadata.get('price_basis') != 'causal_segment_feature_index'):
        raise ValueError('sealed causal public-ratio feature dataset required')
    hashes = metadata.get('converted_hashes', {})
    for name in ['prices.csv', 'fundamentals.csv', 'calendar.csv']:
        if sha256(root/name) != hashes.get(name):
            raise ValueError('converted public file hash mismatch: '+name)
    calendar = pd.DatetimeIndex(pd.to_datetime(pd.read_csv(root/'calendar.csv').date))
    if (calendar.empty or calendar.tz is not None or calendar.hasnans or calendar.has_duplicates
            or not calendar.is_monotonic_increasing or not calendar.equals(calendar.normalize())):
        raise ValueError('invalid sealed exchange calendar')
    end = day(asof) if asof is not None else calendar[-1]
    if end not in calendar:
        raise ValueError('asof outside sealed exchange calendar')
    columns = ['date', 'symbol', 'quality_segment', 'close', 'volume', 'is_st', 'eligible',
               'listing_date', 'earnings_yield', 'book_yield', 'trading_symbol']
    pieces = []
    # Match the original public CSV parser; do not silently alter input floats.
    for chunk in pd.read_csv(root/'prices.csv', usecols=columns, dtype={'symbol': str, 'trading_symbol': str},
                             parse_dates=['date', 'listing_date'], chunksize=chunksize):
        current = chunk[chunk.date.le(end)]
        if not current.empty:
            pieces.append(current.sort_values(['symbol', 'date']).groupby(['symbol', 'quality_segment'], sort=False).tail(253))
    if not pieces:
        raise ValueError('no historical feature bars at asof')
    prices = pd.concat(pieces, ignore_index=True).sort_values(['symbol', 'date'])
    prices = prices.groupby(['symbol', 'quality_segment'], sort=False).tail(253).reset_index(drop=True)
    if prices.duplicated(['symbol', 'date']).any():
        raise ValueError('duplicate feature price identity')
    if not prices.date.isin(calendar).all():
        raise ValueError('feature price outside exchange calendar')
    for column in ['eligible', 'is_st']:
        if not prices[column].map(lambda value: isinstance(value, bool)).all():
            raise ValueError('unknown feature eligibility')
    fundamentals = pd.read_csv(root/'fundamentals.csv', dtype={'symbol': str}, parse_dates=['report_date', 'available_date'])
    fundamentals = fundamentals[fundamentals.available_date.le(end)].copy()
    return Dataset(prices, fundamentals, calendar[calendar <= end], metadata)

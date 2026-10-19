"""Separate missing listed-life quotes from unresolved post-listing valuation.

This is a diagnostic only. A delisting date supplies neither a liquidation
price nor a cash receipt and never authorizes deleting historical positions.
"""
import pandas as pd


def audit_lifetime_coverage(prices, lifetimes, calendar):
    cal = pd.DatetimeIndex(calendar)
    if cal.empty or cal.hasnans or cal.has_duplicates or cal.tz is not None or not cal.is_monotonic_increasing or not cal.equals(cal.normalize()):
        raise ValueError('ordered normalized lifetime calendar required')
    if not {'symbol', 'date'}.issubset(prices) or not {'symbol', 'listing_date', 'terminal_date'}.issubset(lifetimes):
        raise ValueError('quote and lifetime schema required')
    p = prices[['symbol', 'date']].copy()
    lives = lifetimes[['symbol', 'listing_date', 'terminal_date']].copy()
    for frame, fields in [(p, ['date']), (lives, ['listing_date', 'terminal_date'])]:
        if frame.symbol.isna().any():
            raise ValueError('lifetime security key required')
        for field in fields:
            frame[field] = pd.to_datetime(frame[field])
            known = frame[field].dropna()
            if known.dt.tz is not None or not known.eq(known.dt.normalize()).all():
                raise ValueError('normalized lifetime clocks required')
    if p.date.isna().any() or p.duplicated(['symbol', 'date']).any() or not p.date.isin(cal).all():
        raise ValueError('unique observed quote keys required')
    if lives.listing_date.isna().any() or lives.terminal_date.lt(lives.listing_date).any():
        raise ValueError('ordered security lifetimes required')
    if not set(p.symbol).issubset(set(lives.symbol)):
        raise ValueError('observed security has no verified lifetime')
    observed = {symbol: pd.DatetimeIndex(bars.date) for symbol, bars in p.groupby('symbol', sort=False)}
    records = []
    # Multiple rows represent already verified, canonicalized same-issuer codes.
    # Any open-ended successor keeps the issuer live; an old code's outDate
    # cannot be mistaken for the final issuer's terminal date.
    for symbol, history in lives.groupby('symbol', sort=True):
        listing = history.listing_date.min()
        terminal = pd.NaT if history.terminal_date.isna().any() else history.terminal_date.max()
        expected = cal[cal >= listing]
        missing = expected.difference(observed.get(symbol, pd.DatetimeIndex([])))
        live_missing = missing if pd.isna(terminal) else missing[missing < terminal]
        terminal_missing = pd.DatetimeIndex([]) if pd.isna(terminal) else missing[missing >= terminal]
        records.append(dict(symbol=symbol, listing_date=listing, terminal_date=terminal,
            missing_sessions=len(missing), missing_lifetime_quotes=len(live_missing),
            unresolved_post_terminal_valuations=len(terminal_missing),
            first_missing_lifetime_quote=live_missing.min(),
            first_unresolved_terminal_valuation=terminal_missing.min(),
            terminal_settlement_verified=False))
    columns=['symbol','listing_date','terminal_date','missing_sessions','missing_lifetime_quotes',
             'unresolved_post_terminal_valuations','first_missing_lifetime_quote',
             'first_unresolved_terminal_valuation','terminal_settlement_verified']
    result = pd.DataFrame(records, columns=columns)
    summary = dict(missing_lifetime_quotes=int(result.missing_lifetime_quotes.sum()),
        unresolved_post_terminal_valuations=int(result.unresolved_post_terminal_valuations.sum()),
        terminal_affected_issuers=int(result.unresolved_post_terminal_valuations.gt(0).sum()),
        terminal_settlement_verified=False,
        policy='Diagnostic only; preserve all quotes and issuers; do not fill prices or infer settlement from outDate')
    return result, summary

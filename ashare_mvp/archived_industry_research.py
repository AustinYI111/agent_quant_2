"""Retrospective reconstruction from explicit historical release snapshots.

This is a separate provenance policy from genuinely observed daily membership.
The caller must first verify original release/attachment hashes and extraction.
Publication dates gate research availability; actual observation UTC is retained.
"""
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from ashare_mvp.phase2_protocol import day, utc

FEATURES = ['industry_relative_momentum_63', 'industry_relative_momentum_126', 'industry_breadth_50']


def select_archived_research_memberships(memberships, releases, calendar, signal_date, research_end, audited_at=None):
    signal, end = day(signal_date), day(research_end)
    observed_now = utc(audited_at if audited_at is not None else datetime.now(timezone.utc))
    if signal > end:
        raise ValueError('independent signal cannot enter archived research reconstruction')
    cal = pd.DatetimeIndex(pd.to_datetime(calendar))
    if cal.empty or cal.tz is not None or cal.hasnans or cal.has_duplicates or not cal.is_monotonic_increasing or not cal.equals(cal.normalize()) or cal.max() > end or signal not in cal:
        raise ValueError('ordered research-only exchange calendar required')
    required = {'release_id', 'period_end', 'published_date', 'first_observed_at_utc', 'source_sha256', 'snapshot_kind', 'members_count'}
    if not required.issubset(releases) or releases.empty:
        raise ValueError('explicit official historical release catalog required')
    r = releases.copy()
    if r[list(required)].isna().any().any() or r.release_id.duplicated().any() or not r.snapshot_kind.eq('official_archived_industry_release').all():
        raise ValueError('unique archived release identities required; current membership is not accepted')
    if not r.source_sha256.astype(str).str.fullmatch('[0-9a-f]{64}').all():
        raise ValueError('original attachment hashes required')
    for name in ['period_end', 'published_date']:
        r[name] = r[name].map(day)
    r['first_observed_at_utc'] = pd.to_datetime(r.first_observed_at_utc.map(utc), utc=True)
    observed_day = r.first_observed_at_utc.dt.tz_convert('Asia/Shanghai').dt.tz_localize(None).dt.normalize()
    if r.published_date.duplicated().any() or r.period_end.gt(r.published_date).any() or r.published_date.gt(end).any() or r.published_date.gt(observed_day).any() or r.first_observed_at_utc.gt(observed_now).any():
        raise ValueError('historical publication, reporting period or actual observation clocks invalid')
    if not {'symbol', 'industry', 'release_id'}.issubset(memberships):
        raise ValueError('release-bound membership rows required')
    m = memberships.copy()
    if m[['symbol', 'industry', 'release_id']].isna().any().any() or m.duplicated(['release_id', 'symbol']).any() or not m.release_id.isin(r.release_id).all():
        raise ValueError('unique membership keys bound to known releases required')
    if not m.industry.astype(str).str.fullmatch('[A-S][0-9]{2}').all() or m.symbol.astype(str).str.strip().eq('').any():
        raise ValueError('literal historical major-industry codes required')
    counts = pd.to_numeric(r.members_count, errors='raise')
    if not np.isfinite(counts).all() or counts.le(0).any() or counts.mod(1).ne(0).any() or not np.array_equal(m.groupby('release_id').size().reindex(r.release_id, fill_value=0).to_numpy(), counts.to_numpy()):
        raise ValueError('complete release row counts required; no truncated snapshot')
    # Date-only publication becomes usable at the next actual exchange session.
    # It is never substituted for the attachment's real observation timestamp.
    positions = cal.searchsorted(r.published_date, side='right')
    r['visible_date'] = [cal[i] if i < len(cal) else pd.NaT for i in positions]
    known = r[r.visible_date.notna() & r.visible_date.le(signal)].sort_values(['visible_date', 'published_date'])
    columns = ['symbol', 'industry', 'release_id', 'published_date', 'visible_date', 'first_observed_at_utc', 'source_sha256']
    if known.empty:
        return pd.DataFrame(columns=columns)
    release = known.iloc[-1]
    selected = m[m.release_id.eq(release.release_id)][['symbol', 'industry', 'release_id']].copy()
    for name in columns[3:]:
        selected[name] = release[name]
    # Each release is a complete snapshot: an absent issuer stays unknown,
    # rather than inheriting an older classification after its removal.
    return selected[columns].reset_index(drop=True)


def archived_research_industry_features(snapshot, memberships, releases, calendar, signal_date, research_end, audited_at=None):
    required = {'symbol', 'raw_momentum_63', 'raw_momentum_126', 'close_above_ma50'}
    if not required.issubset(snapshot) or snapshot.symbol.isna().any() or snapshot.symbol.duplicated().any():
        raise ValueError('unique eligible raw industry snapshot required')
    signal = day(signal_date)
    if 'date' in snapshot and not pd.to_datetime(snapshot.date).eq(signal).all():
        raise ValueError('one research signal date required')
    selected = select_archived_research_memberships(memberships, releases, calendar, signal, research_end, audited_at)
    p = snapshot.merge(selected, on='symbol', how='left', validate='one_to_one')
    for name in ['raw_momentum_63', 'raw_momentum_126', 'close_above_ma50']:
        p[name] = pd.to_numeric(p[name], errors='raise')
        if np.isinf(p[name]).any():
            raise ValueError('infinite raw industry input rejected')
    if not p.close_above_ma50.dropna().isin([0., 1.]).all():
        raise ValueError('known breadth inputs must be binary')
    result = p[['symbol', 'industry', 'release_id', 'published_date', 'visible_date', 'first_observed_at_utc', 'source_sha256']].copy()
    result['industry_known'] = p.industry.notna()
    for raw, feature in zip(['raw_momentum_63', 'raw_momentum_126', 'close_above_ma50'], FEATURES):
        groups = p.groupby('industry', dropna=True, sort=False)[raw]
        reference = 0. if raw == 'close_above_ma50' else p[raw].mean()
        result[feature] = (groups.transform('mean') - reference).where(groups.transform('count').ge(10) & p.industry.notna())
    result['date'] = signal
    result.attrs.update(provenance_policy='archived_primary_release_reconstruction', historical_original_bytes_certified=False,
                        genuine_historical_observation_claimed=False, independent_data_used=False,
                        source_hashes_must_be_verified_by_caller=True)
    return result

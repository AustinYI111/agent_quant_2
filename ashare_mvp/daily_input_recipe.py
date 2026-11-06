"""Prepare portable frozen research inputs from verified daily observations.

Does not fetch, train, predict, archive or value holdings. Known cash references
must match; other discontinuities stay explicit under the frozen research rule.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import numpy as np
import pandas as pd
from .workspace_paths import WorkspacePaths
from .phase2_protocol import day, utc, sha256, load_seal, completed_date_limit
from .public_data import is_ashare
from .security_identity import stitch_code_prices
from .price_action_audit import audit_price_actions
from .price_reference import reference_matches


def now():
    return datetime.now(timezone.utc)


def prepare(project, work_root, previous_snapshot, previous_snapshot_sha256,
            previous_inputs, capture, financial_refresh, financial_audit,
            actions, actions_audit, signal, out, additional_markets=(),
            additional_financial_history=(), exploratory=False):
    if not exploratory:
        raise ValueError('explicit exploratory mode required')
    signal = day(signal)
    started = utc(now())
    limit = completed_date_limit(started)
    if signal > limit:
        raise ValueError('signal has not passed the frozen 17:20 gate')
    paths = WorkspacePaths(project, work_root)
    code_names = ['daily_input_recipe.py', 'workspace_paths.py', 'phase2_protocol.py',
                  'public_data.py', 'security_identity.py', 'price_action_audit.py', 'price_reference.py']
    def code_hashes():
        return {name: hashlib.sha256(Path(__file__).with_name(name).read_text(encoding='utf-8-sig').replace('\r\n', '\n').encode()).hexdigest() for name in code_names}
    executed_code = code_hashes()
    target = paths.resolve(out)
    inputs = [previous_snapshot, previous_inputs, capture, financial_refresh,
              financial_audit, actions, actions_audit, *additional_markets,
              *additional_financial_history]
    resolved = [paths.resolve(value) for value in inputs]
    if (target.exists() or not target.is_relative_to(paths.work) or
            any(target.is_relative_to(p) or p.is_relative_to(target) for p in resolved)):
        raise ValueError('fresh separate external recipe output required')
    sources = {}

    def bind(file, expected=None):
        digest = sha256(file)
        if expected is not None and digest != expected:
            raise ValueError('recipe input hash differs: ' + paths.name(file))
        if paths.name(file) in sources and sources[paths.name(file)] != digest:
            raise ValueError('input changed during recipe preparation')
        sources[paths.name(file)] = digest
        return digest

    def read(file, expected=None):
        bind(file, expected)
        return json.loads(file.read_text(encoding='utf8'))

    previous = paths.resolve(previous_snapshot)
    parent = read(previous / 'manifest.json', previous_snapshot_sha256)
    if (parent.get('status') != 'research_snapshot_saved' or
            parent.get('schema') != 'frozen_daily_research_snapshot_v1' or
            parent.get('model_selection_performed') is not False):
        raise ValueError('complete prior frozen inference snapshot required')
    previous_day = day(parent['signal_date'])
    if previous_day >= signal or utc(parent['completed_at_utc']) > started:
        raise ValueError('ordered prior signal and observed completion required')
    recipe_file = paths.resolve(previous_inputs)
    expected = parent.get('source_hashes', {}).get(paths.name(recipe_file))
    if expected is None:
        raise ValueError('previous recipe is not bound by its snapshot')
    config = read(recipe_file, expected)
    if config.get('schema') != 'frozen_daily_inputs_v1':
        raise ValueError('frozen input recipe schema differs')
    dataset_names = [config[k] for k in ['seal', 'baseline', 'seed', 'raw_store', 'financials', 'actions']]
    dataset_names += list(config['markets']) + list(config.get('financial_history', []))
    dataset_names += [row['file'] for row in config['masters']]
    if any(target.is_relative_to(paths.resolve(name)) or paths.resolve(name).is_relative_to(target) for name in dataset_names):
        raise ValueError('recipe output overlaps an inherited input')
    _, seal = load_seal(paths.resolve(config['seal']), paths.project)
    if parent['protocol_sha256'] != seal['protocol_sha256']:
        raise ValueError('prior snapshot uses another protocol')

    collected = paths.resolve(capture)
    cm = read(collected / 'capture.json')
    if (cm.get('status') != 'raw_acquired' or cm.get('signal_date') != str(signal.date())
            or cm.get('synthetic') is not False or
            cm.get('protocol_sha256') != seal['protocol_sha256']):
        raise ValueError('complete real capture for this signal required')
    if utc(cm['completed_at_utc']) > started:
        raise ValueError('capture observation is in the future')
    if not {'market/exchange_calendar.csv', 'market/latest/manifest.json', 'reference/capture.json'}.issubset(cm['output_hashes']):
        raise ValueError('capture lacks core output hashes')
    for name, digest in cm['output_hashes'].items():
        file = paths.resolve(paths.name(collected) + '/' + name)
        bind(file, digest)
    calendar_file = collected / 'market/exchange_calendar.csv'
    calendar = pd.read_csv(calendar_file, dtype=str)
    if not calendar.is_trading_day.isin(['0', '1']).all():
        raise ValueError('known exchange-session flags required')
    all_days = pd.DatetimeIndex(pd.to_datetime(calendar.calendar_date))
    if (all_days.hasnans or all_days.has_duplicates or all_days.tz is not None
            or not all_days.equals(pd.date_range(all_days[0], all_days[-1]))):
        raise ValueError('complete ordered exchange calendar required')
    cal = all_days[calendar.is_trading_day.eq('1').to_numpy()]
    completed = cal[cal <= limit]
    if not len(completed) or completed[-1] != signal or not (cal > signal).any():
        raise ValueError('exact latest completed session and next open required')
    old_calendar_file = paths.resolve(config['calendar'])
    old_expected = parent.get('source_hashes', {}).get(paths.name(old_calendar_file))
    if old_expected is None:
        raise ValueError('prior calendar is not bound by its snapshot')
    bind(old_calendar_file, old_expected)
    old_calendar = pd.read_csv(old_calendar_file, dtype=str)
    old_cal = pd.DatetimeIndex(pd.to_datetime(old_calendar.loc[old_calendar.is_trading_day.eq('1'), 'calendar_date']))
    baseline_calendar = paths.resolve(config['raw_store']) / 'calendar.csv'
    baseline_expected = parent['source_hashes'].get(paths.name(baseline_calendar))
    if baseline_expected is None:
        raise ValueError('sealed baseline calendar is not bound by prior snapshot')
    bind(baseline_calendar, baseline_expected)
    baseline_cal = pd.DatetimeIndex(pd.read_csv(baseline_calendar, parse_dates=['date']).date)
    old_cal = baseline_cal.union(old_cal).sort_values()
    if not old_cal[(old_cal >= cal[0]) & (old_cal <= previous_day)].equals(cal[cal <= previous_day]):
        raise ValueError('new calendar revises known previous sessions')
    changes_file = paths.resolve(config['code_changes'])
    bind(changes_file, parent['source_hashes'].get(paths.name(changes_file)))
    changes = pd.read_json(changes_file, convert_dates=False)

    def market(directory):
        manifest = read(directory / 'manifest.json')
        if (manifest.get('schema') != 'market_daily_raw_v1' or
                manifest.get('status') != 'downloaded_raw' or manifest.get('synthetic') is not False):
            raise ValueError('complete real market input required')
        bind(directory / 'calendar.csv', manifest['files']['calendar.csv']['sha256'])
        result = []
        for name, info in manifest['files'].items():
            if not name.startswith('daily_'):
                continue
            file = paths.resolve(paths.name(directory) + '/' + name)
            bind(file, info['sha256'])
            frame = pd.read_csv(file, dtype={'code': str, 'tradestatus': str, 'isST': str, 'adjustflag': str}, float_precision='round_trip')
            date = day(frame.date.iloc[0]) if len(frame) else pd.NaT
            if (len(frame) != info['rows'] or pd.isna(date) or
                    not pd.to_datetime(frame.date).eq(date).all() or frame.code.isna().any() or frame.code.duplicated().any()
                    or not frame.adjustflag.eq('3').all() or not frame.isST.isin(['0', '1']).all()
                    or not frame.tradestatus.isin(['0', '1']).all()):
                raise ValueError('raw market identity, row count or price basis differs')
            observed = utc(info['retrieved_at'])
            release = (date + pd.Timedelta(hours=17, minutes=20)).tz_localize('Asia/Shanghai').tz_convert('UTC')
            if not release <= observed <= started:
                raise ValueError('market observation precedes close or is in the future')
            frame = frame[frame.code.map(is_ashare)].rename(columns={'code': 'symbol'})
            frame['date'] = pd.to_datetime(frame.date)
            canonical, rejected = stitch_code_prices(frame, changes)
            if not rejected.empty:
                raise ValueError('unverified or inactive quote alias')
            values = canonical[['open', 'high', 'low', 'close', 'preclose']].apply(pd.to_numeric, errors='raise')
            activity = canonical[['volume', 'amount']].apply(pd.to_numeric, errors='raise')
            active = canonical.tradestatus.eq('1')
            if (not np.isfinite(values).all().all() or values.lt(0).any().any()
                    or values[['close', 'preclose']].le(0).any().any()
                    or values.loc[active].le(0).any().any()
                    or not np.isfinite(activity.loc[active]).all().all() or activity.lt(0).any().any()
                    or values.loc[active, 'high'].lt(values.loc[active, ['open', 'close', 'low']].max(axis=1)).any()
                    or values.loc[active, 'low'].gt(values.loc[active, ['open', 'close', 'high']].min(axis=1)).any()):
                raise ValueError('invalid raw OHLC or activity fields')
            result.append((date, canonical))
        return result

    inherited = [paths.resolve(value) for value in config['markets']]
    added = [paths.resolve(value) for value in additional_markets] + [collected / 'market/latest']
    if len(set(added)) != len(added) or set(added) & set(inherited):
        raise ValueError('duplicate or overlapping market directory')
    frames = {}
    for directory in added:
        for date, frame in market(directory):
            if not previous_day < date <= signal or date in frames:
                raise ValueError('new market sessions must be distinct and after prior signal')
            frames[date] = frame
    if set(frames) != set(cal[(cal > previous_day) & (cal <= signal)]):
        raise ValueError('missing exchange sessions in new market inputs')
    previous_frames = []
    for directory in inherited:
        expected = parent['source_hashes'].get(paths.name(directory / 'manifest.json'))
        if expected is None:
            raise ValueError('inherited market is not bound by prior snapshot')
        manifest = read(directory / 'manifest.json', expected)
        name = 'daily_' + previous_day.strftime('%Y%m%d') + '.csv'
        if name in manifest.get('files', {}):
            previous_frames.extend(frame for date, frame in market(directory) if date == previous_day)
    if len(previous_frames) != 1:
        raise ValueError('one original previous-session quote required')
    quotes = pd.concat(previous_frames + [frames[d] for d in sorted(frames)], ignore_index=True)
    values = quotes[['open', 'close', 'preclose']].apply(pd.to_numeric, errors='raise')
    if (not np.isfinite(values).all().all() or values[['close', 'preclose']].le(0).any().any()
            or values.open.lt(0).any() or quotes.duplicated(['date', 'symbol']).any()):
        raise ValueError('invalid or ambiguous raw valuations')
    reference = read(collected / 'reference/capture.json')
    if reference.get('status') != 'raw_acquired' or reference.get('protocol_sha256') != seal['protocol_sha256']:
        raise ValueError('complete matching reference capture required')
    key = 'stock_all_' + signal.strftime('%Y%m%d')
    item = reference['queries'][key]
    names_file = collected / 'reference' / (key + '.csv')
    bind(names_file, item['sha256'])
    observed_names = utc(item['observed_at_utc'])
    names = pd.read_csv(names_file, dtype=str)
    if not {'code', 'code_name', 'tradeStatus'}.issubset(names):
        raise ValueError('dated names and security status required')
    names = names[names.code.map(is_ashare)]
    latest = frames[signal]
    if (item.get('status') != 'raw_acquired' or len(names) != len(latest) or names.code.duplicated().any()
            or set(names.code) != set(latest.trading_symbol) or observed_names > started
            or names.code_name.isna().any() or names.code_name.str.strip().eq('').any()
            or not names.set_index('code').tradeStatus.sort_index().equals(latest.set_index('trading_symbol').tradestatus.sort_index().rename('tradeStatus'))):
        raise ValueError('current security scope differs from raw quotes')

    financial = paths.resolve(financial_refresh)
    fm = read(financial / 'refresh.json')
    fa = read(paths.resolve(financial_audit))
    if (fm.get('status') != 'dependencies_refreshed_before_join' or day(fm['signal_date']) > signal
            or utc(fm['completed_at_utc']) > started
            or fa.get('status') != 'all_combined_versions_independently_selected'
            or fa.get('refresh_manifest_sha256') != sha256(financial / 'refresh.json')
            or not utc(fm['completed_at_utc']) <= utc(fa['audited_at_utc']) <= started
            or fa['records'] != fm['combined']['records']):
        raise ValueError('independently verified visible financial refresh required')
    financial_dir = financial / 'history/combined'
    summary = read(financial_dir / 'summary.json', fm['output_hashes']['history/combined/summary.json'])
    bind(financial_dir / 'financial_panel.csv', summary['panel_sha256'])
    if summary.get('partial_source') is not False:
        raise ValueError('partial financial dependency source')

    action_dir = paths.resolve(actions)
    am = read(action_dir / 'summary.json')
    aa = read(paths.resolve(actions_audit))
    if (am.get('status') != 'cash_terms_staged_raw_quote_check_pending'
            or aa.get('status') != 'raw_intake_and_cash_extensions_independently_verified'
            or aa.get('staged_summary_sha256') != sha256(action_dir / 'summary.json')
            or not utc(am['created_at_utc']) <= utc(aa['audited_at_utc']) <= started):
        raise ValueError('independently reviewed cash stage required')
    bind(action_dir / 'actions.csv', am['output_hashes']['actions.csv'])
    events = pd.read_csv(action_dir / 'actions.csv', float_precision='round_trip', low_memory=False)
    old_action_dir = paths.resolve(config['actions'])
    old_expected = parent['source_hashes'].get(paths.name(old_action_dir / 'summary.json'))
    if old_expected is None:
        raise ValueError('prior cash source is not bound by snapshot')
    old_am = read(old_action_dir / 'summary.json', old_expected)
    bind(old_action_dir / 'actions.csv', old_am['output_hashes']['actions.csv'])
    old_events = pd.read_csv(old_action_dir / 'actions.csv', float_precision='round_trip', low_memory=False)
    if len(events) < len(old_events) or not set(old_events).issubset(events):
        raise ValueError('prior cash records missing from new stage')
    for field in old_events:
        before = old_events[field].reset_index(drop=True)
        after = events.iloc[:len(old_events)][field].reset_index(drop=True)
        if field.endswith('date'):
            before, after = pd.to_datetime(before, format='mixed'), pd.to_datetime(after, format='mixed')
        try:
            pd.testing.assert_series_equal(before, after, check_dtype=False, check_names=False, check_exact=True)
        except AssertionError as error:
            raise ValueError('prior cash values or dates changed') from error
    for field in ['ex_date', 'available_date', 'pay_date']:
        events[field] = pd.to_datetime(events[field], format='mixed')
    if (events.duplicated(['symbol', 'ex_date']).any() or events.available_date.gt(events.ex_date).any()
            or events.pay_date.lt(events.ex_date).any()):
        raise ValueError('ambiguous or late-admitted cash event')
    window = cal[(cal >= previous_day) & (cal <= signal)]
    issues, quality = audit_price_actions(quotes, events, window, .01)
    lookup = quotes.set_index(['date', 'symbol'])
    checks = []
    for event in events[events.ex_date.gt(previous_day) & events.ex_date.le(signal)].itertuples(index=False):
        prior = cal[cal.get_loc(event.ex_date) - 1]
        if (prior, event.symbol) not in lookup.index or (event.ex_date, event.symbol) not in lookup.index:
            raise ValueError('reviewed cash reference lacks adjacent observed quotes')
        cash = getattr(event, 'reference_cash_per_share', np.nan)
        multiplier = getattr(event, 'reference_share_multiplier', np.nan)
        cash = event.cash_per_share if pd.isna(cash) else cash
        multiplier = event.share_multiplier if pd.isna(multiplier) else multiplier
        if not reference_matches(lookup.loc[(event.ex_date, event.symbol), 'preclose'], lookup.loc[(prior, event.symbol), 'close'], cash, multiplier, .01):
            raise ValueError('reviewed cash reference disagrees with raw price')
        checks.append(dict(symbol=event.symbol, ex_date=str(event.ex_date.date()), reference_matches=True))

    history = [paths.name(paths.resolve(name)) for name in config.get('financial_history', [])]
    if len(history) != len(set(history)):
        raise ValueError('previous financial history repeats a snapshot')
    for name in [config['financials'], *additional_financial_history]:
        name = paths.name(paths.resolve(name))
        if name not in history and name != paths.name(financial_dir):
            extra = paths.resolve(name)
            info = read(extra / 'summary.json')
            bind(extra / 'financial_panel.csv', info['panel_sha256'])
            history.append(name)
    config.update(markets=config['markets'] + [paths.name(p) for p in added],
                  calendar=paths.name(calendar_file), financials=paths.name(financial_dir),
                  financial_history=history, actions=paths.name(action_dir))
    for name, digest in sources.items():
        if sha256(paths.resolve(name)) != digest:
            raise ValueError('input changed during recipe preparation')
    if code_hashes() != executed_code:
        raise ValueError('preparation implementation changed during run')
    target.mkdir(parents=True)
    (target / 'frozen_inputs.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    issues.to_csv(target / 'quality_issues.csv', index=False)
    proof = dict(schema='guarded_daily_input_preparation_v1', status='research_recipe_prepared_quote_issues_retained',
                 created_at_utc=utc(now()).isoformat(), signal_date=str(signal.date()), previous_signal_date=str(previous_day.date()),
                 input_hashes=sources, code_hashes=executed_code, new_sessions=[str(d.date()) for d in sorted(frames)], quoted_ashares=len(latest),
                 quality=quality, reviewed_cash_reference_checks=checks, prior_cash_events_preserved=len(old_events),
                 financial_observation_date=fm['signal_date'], financial_records=fa['records'],
                 security_status_observed_at_utc=observed_names.isoformat(), prospective_security_observation=observed_names.tz_convert('Asia/Shanghai').date()==signal.date(),
                 model_ready=False, predictions_created=False, model_selection_performed=False, holdings_valued=False,
                 output_hashes={p.name: sha256(p) for p in target.iterdir() if p.is_file()},
                 limitations=['Research input preparation only; unknown discontinuities remain segmented by the frozen inference policy.',
                              'Known reviewed cash references must match. This does not certify terminal values, original vintages or live execution.'])
    (target / 'preparation.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf8')
    return proof


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', default='.')
    for name in ['work-root', 'previous-snapshot', 'previous-snapshot-sha256', 'previous-inputs', 'capture',
                 'financial-refresh', 'financial-audit', 'actions', 'actions-audit', 'signal', 'out']:
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--additional-markets', action='append', default=[])
    parser.add_argument('--additional-financial-history', action='append', default=[])
    parser.add_argument('--exploratory', action='store_true')
    result = prepare(**vars(parser.parse_args()))
    print(json.dumps({k: result[k] for k in ['status', 'signal_date', 'new_sessions', 'quoted_ashares', 'model_ready']}))

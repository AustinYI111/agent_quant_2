"""Union sealed historical distributions without treating plan coverage as complete."""
from io import BytesIO
from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd

from .public_data import is_ashare
from .security_identity import canonical_event_symbols, code_change_payload


def merge_dividend_events(base, supplements, calendar_path, out, code_changes=None):
    """Preserve plan issues; add independently normalized historical events once."""
    base, target = Path(base), Path(out)
    supplements = [Path(p) for p in supplements]
    if not supplements or len({p.resolve() for p in supplements}) != len(supplements):
        raise ValueError('distinct supplemental sources required')
    if target.exists() and any(target.iterdir()):
        raise ValueError('event union output must be empty')
    snapshots = {}

    def read(directory, name, label, digest=None):
        raw = (directory / name).read_bytes()
        if digest is not None and hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('event union input hash mismatch: ' + label)
        snapshots[label] = raw
        return raw

    raw_calendar = Path(calendar_path).read_bytes()
    snapshots['calendar.csv'] = raw_calendar
    calendar_hash = hashlib.sha256(raw_calendar).hexdigest()
    dates = pd.read_csv(BytesIO(raw_calendar), dtype=str)
    cal = pd.DatetimeIndex(pd.to_datetime(dates.loc[dates.is_trading_day.eq('1'), 'calendar_date']
        if {'calendar_date', 'is_trading_day'}.issubset(dates) else dates['date']))
    if cal.empty or cal.hasnans or cal.has_duplicates or not cal.is_monotonic_increasing or cal.tz is not None or not cal.equals(cal.normalize()):
        raise ValueError('ordered normalized event calendar required')
    meta = json.loads(read(base, 'summary.json', 'base_summary.json'))
    if pd.Timestamp(meta['evaluation_start']) > cal.min() or pd.Timestamp(meta['evaluation_end']) < cal.max():
        raise ValueError('base event coverage does not span calendar')
    actions = pd.read_csv(BytesIO(read(base, 'actions.csv', 'base_actions.csv', meta['actions_sha256'])))
    if 'coverage_sha256' not in meta:
        raise ValueError('sealed base coverage hash required')
    coverage = pd.read_csv(BytesIO(read(base, 'coverage.csv', 'base_coverage.csv', meta['coverage_sha256'])))
    if len(actions) != meta['matched_events'] or coverage.status.value_counts().to_dict() != meta['status_counts']:
        raise ValueError('base event counts differ from summary')
    policy = None if code_changes is None else code_change_payload(code_changes).encode()
    if meta.get('code_changes_sha256'):
        original = read(base, 'input_code_changes.json', 'base_code_changes.json', meta['code_changes_sha256'])
        if policy is None or hashlib.sha256(policy).hexdigest() != hashlib.sha256(original).hexdigest():
            raise ValueError('matching base event identity policy required')
    elif policy is not None:
        actions = canonical_event_symbols(actions, code_changes).rename(columns={"source_symbol": "base_provider_symbol"})

    def validate(frame, supplemental=False):
        required = {'symbol', 'ex_date', 'available_date', 'pay_date', 'cash_per_share', 'share_multiplier'}
        if not required.issubset(frame) or frame.symbol.isna().any() or not frame.symbol.map(is_ashare).all():
            raise ValueError('supported event security/schema required')
        for field in ['ex_date', 'available_date', 'pay_date', 'announcement_date', 'stock_market_date', 'share_credit_date']:
            if field not in frame: continue
            frame[field] = pd.to_datetime(frame[field])
            known = frame[field].dropna()
            if known.dt.tz is not None or not known.eq(known.dt.normalize()).all():
                raise ValueError('normalized event clocks required')
        if frame[['ex_date', 'available_date']].isna().any().any() or frame.duplicated(['symbol', 'ex_date']).any():
            raise ValueError('ambiguous event keys/clocks')
        for field, minimum in [('cash_per_share', 0), ('share_multiplier', 0)]:
            frame[field] = pd.to_numeric(frame[field], errors='raise')
            if not np.isfinite(frame[field]).all() or frame[field].lt(minimum).any():
                raise ValueError('invalid event terms')
        if frame.share_multiplier.le(0).any(): raise ValueError('invalid event shares')
        if not frame.ex_date.isin(cal).all() or not frame.available_date.isin(cal).all() or frame.available_date.gt(frame.ex_date).any():
            raise ValueError('event visibility outside calendar/execution')
        if (frame.cash_per_share.gt(0) & frame.pay_date.isna()).any() or frame.pay_date.lt(frame.ex_date).any():
            raise ValueError('invalid cash payment clock')
        if supplemental:
            if 'announcement_date' not in frame or frame.announcement_date.isna().any() or frame.announcement_date.ge(frame.available_date).any():
                raise ValueError('supplemental announcement must precede availability')
            if not {'source_file', 'source_row', 'event_vintage_certified'}.issubset(frame):
                raise ValueError('supplemental provenance required')
            if not frame.event_vintage_certified.eq(False).all():
                raise ValueError('unsupported supplemental vintage certification')
        return frame

    actions = validate(actions).set_index(['symbol', 'ex_date'], drop=False)
    audit, additions = [], []
    for number, directory in enumerate(supplements):
        prefix = f'supplement_{number}_'
        info = json.loads(read(directory, 'summary.json', prefix + 'summary.json'))
        if info.get('status') != 'normalized' or info.get('rejected') != 0 or info.get('calendar_sha256') != calendar_hash:
            raise ValueError('fully normalized supplement with matching calendar required')
        manifest = json.loads(read(directory, 'input_manifest.json', prefix + 'manifest.json', info['input_manifest_sha256']))
        if manifest.get('schema') != 'dividend_raw_v1' or manifest.get('status') != 'downloaded_raw' or manifest.get('request', {}).get('year_type') != 'operate':
            raise ValueError('supported execution-year distribution source required')
        read(directory, 'input_calendar.csv', prefix + 'calendar.csv', calendar_hash)
        extra = pd.read_csv(BytesIO(read(directory, 'actions.csv', prefix + 'actions.csv', info['actions_sha256'])))
        if len(extra) != info['accepted']: raise ValueError('supplemental row count mismatch')
        if code_changes is not None:
            extra = canonical_event_symbols(extra, code_changes).rename(columns={"source_symbol": "supplemental_provider_symbol"})
        extra = validate(extra, supplemental=True)
        for row in extra.to_dict('records'):
            filename, index = row['source_file'], row['source_row']
            source = manifest.get('files', {}).get(filename)
            if not isinstance(filename, str) or Path(filename).name != filename or source is None or not np.isfinite(index) or index < 0 or int(index) != index or index >= source['rows']:
                raise ValueError('invalid supplemental raw row provenance')
            key = (row['symbol'], row['ex_date'])
            status = 'added_historical_distribution'
            if key in actions.index:
                prior = actions.loc[key]
                if any(not np.isclose(prior[field], row[field], rtol=0, atol=1e-8) for field in ['cash_per_share', 'share_multiplier']):
                    raise ValueError('conflicting distribution entitlements')
                for field in ['pay_date', 'stock_market_date', 'share_credit_date']:
                    if pd.notna(prior.get(field)) and pd.notna(row.get(field)) and prior[field] != row[field]:
                        raise ValueError('conflicting distribution settlement dates')
                if row['cash_per_share'] > 0 and prior.pay_date != row['pay_date']:
                    raise ValueError('conflicting cash payment dates')
                actions.at[key, 'available_date'] = max(prior.available_date, row['available_date'])
                status = 'corroborated_existing_distribution'
            else:
                security = row['symbol'].split('.')[1] + '.' + row['symbol'].split('.')[0].upper()
                # Never use an extra source to silently clear an unresolved market plan.
                dated = pd.to_datetime(coverage.get('ex_date', pd.Series(index=coverage.index, dtype=str)), errors='coerce')
                if (coverage.security.eq(security) & dated.eq(row['ex_date']) & coverage.status.isin(['unresolved', 'missing_payment_evidence'])).any():
                    raise ValueError('supplement overlaps unresolved market plan; reconcile explicitly')
                additions.append(dict(security=security, symbol=row['symbol'], ex_date=row['ex_date'],
                    status='supplemental_payment_evidence', reason='sealed historical distribution absent from matched plan subset'))
                row['event_vintage_certified'] = False
                actions = pd.concat([actions.reset_index(drop=True), pd.DataFrame([row])], ignore_index=True).set_index(['symbol', 'ex_date'], drop=False)
            audit.append(dict(symbol=row['symbol'], ex_date=row['ex_date'], status=status,
                supplemental_source=number, source_file=filename, source_row=int(index),
                source_actions_sha256=info['actions_sha256']))
    output = actions.reset_index(drop=True).sort_values(['symbol', 'ex_date'])
    coverage = pd.concat([coverage, pd.DataFrame(additions)], ignore_index=True)
    target.mkdir(parents=True, exist_ok=True)
    output.to_csv(target / 'actions.csv', index=False)
    coverage.to_csv(target / 'coverage.csv', index=False)
    pd.DataFrame(audit).to_csv(target / 'supplement_audit.csv', index=False)
    for label, raw in snapshots.items(): (target / ('input_' + label)).write_bytes(raw)
    result = dict(meta)
    result.update(schema='historical_distribution_union_v1', matched_events=len(output),
        records=len(coverage), in_scope_records=int((~coverage.status.isin(['excluded_exchange', 'outside_evaluation_window'])).sum()),
        status_counts=coverage.status.value_counts().to_dict(),
        certified=False, model_ready=False, supplemental_events_added=len(additions),
        supplemental_records_checked=len(audit),
        actions_sha256=hashlib.sha256((target / 'actions.csv').read_bytes()).hexdigest(),
        coverage_sha256=hashlib.sha256((target / 'coverage.csv').read_bytes()).hexdigest(),
        supplement_audit_sha256=hashlib.sha256((target / 'supplement_audit.csv').read_bytes()).hexdigest(),
        input_hashes={label: hashlib.sha256(raw).hexdigest() for label, raw in snapshots.items()},
        limitations=list(meta.get('limitations', [])) + ['supplemental records are not independent proof of complete event history; original vintages unverified'])
    if policy is not None:
        (target / 'input_code_changes.json').write_bytes(policy)
        result['code_changes_sha256'] = hashlib.sha256(policy).hexdigest()
    (target / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result

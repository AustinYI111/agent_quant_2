"""Immutable phase-two research boundaries and forecast timing checks."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import pandas as pd


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def utc(value):
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp) or timestamp.tz is None:
        raise ValueError('an explicit timezone is required for observation and creation clocks')
    return timestamp.tz_convert('UTC')


def day(value):
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp) or timestamp.tz is not None or timestamp != timestamp.normalize():
        raise ValueError('expected a timezone-free exchange session date')
    return timestamp


def validate_protocol(protocol):
    if protocol.get('schema') != 'phase2_research_protocol_v1':
        raise ValueError('unsupported phase-two protocol')
    utc(protocol['frozen_at_utc'])
    if day(protocol['research_end']) >= day(protocol['independent_start']):
        raise ValueError('research and independent validation overlap')
    if day(protocol['prospective_start']) < day(protocol['independent_start']):
        raise ValueError('prospective boundary precedes independent data')
    if set(protocol['models']) != {'5d', '20d', '12m'}:
        raise ValueError('all three frozen baseline models are required')
    if [g['id'] for g in protocol['candidate_groups']] != ['industry', 'market_state', 'oneil_box', 'combined']:
        raise ValueError('candidate groups and their order must be prespecified')
    if protocol['validation']['independent_data_may_select_parameters'] is not False:
        raise ValueError('independent results cannot select parameters')
    if protocol['validation']['unknown_outcomes'] != 'unmatured_or_unresolved_never_zero':
        raise ValueError('unknown returns must remain unknown')
    for item in protocol['models'].values():
        path = Path(item['relative_path'])
        if path.is_absolute() or '..' in path.parts or ':' in str(path):
            raise ValueError('portable relative model paths required')
        if day(item['trained_asof']) >= day(protocol['independent_start']):
            raise ValueError('baseline was created inside independent validation')
        if set(item['files_sha256']) != {'model.txt', 'metadata.json', 'feature_input_policy.json', 'training_dataset_metadata.json'}:
            raise ValueError('complete frozen model contract hashes required')
    return protocol


def verify_models(protocol, project):
    validate_protocol(protocol)
    root = Path(project).resolve()
    for horizon, item in protocol['models'].items():
        folder = (root / item['relative_path']).resolve()
        if not folder.is_relative_to(root):
            raise ValueError('frozen model escapes project')
        for name, expected in item['files_sha256'].items():
            if sha256(folder / name) != expected:
                raise ValueError('frozen model contract changed: ' + horizon + '/' + name)
        meta = json.loads((folder / 'metadata.json').read_text(encoding='utf8'))
        if meta['horizon']['key'] != horizon or day(meta['trained_asof']) != day(item['trained_asof']):
            raise ValueError('model horizon or historical creation date differs')
    return True


def seal(source, out, project):
    raw = Path(source).read_bytes()
    protocol = validate_protocol(json.loads(raw))
    verify_models(protocol, project)
    target = Path(out)
    if target.exists():
        raise ValueError('protocol seal output must be new; never replace an existing experiment')
    target.mkdir(parents=True)
    (target / 'protocol.json').write_bytes(raw)
    evidence = dict(schema='phase2_protocol_seal_v1', protocol_sha256=hashlib.sha256(raw).hexdigest(),
                    sealed_at_utc=datetime.now(timezone.utc).isoformat(), models_verified=True)
    (target / 'seal.json').write_text(json.dumps(evidence, indent=2), encoding='utf8')
    return evidence


def load_seal(directory, project):
    root = Path(directory)
    proof = json.loads((root / 'seal.json').read_text(encoding='utf8'))
    if proof.get('schema') != 'phase2_protocol_seal_v1' or sha256(root / 'protocol.json') != proof['protocol_sha256']:
        raise ValueError('sealed research protocol changed')
    protocol = validate_protocol(json.loads((root / 'protocol.json').read_text(encoding='utf8')))
    verify_models(protocol, project)
    return protocol, proof


def require_selection_rows(frame, protocol):
    """Labels and signal rows used for selection must both be research-only."""
    end = day(protocol['research_end'])
    for column in ['date', 'label_end']:
        values = pd.to_datetime(frame[column], errors='raise')
        if values.isna().any() or values.dt.tz is not None or not values.eq(values.dt.normalize()).all() or values.gt(end).any():
            raise ValueError('selection contains unknown or independent dates: ' + column)
    if pd.to_datetime(frame.label_end).lt(pd.to_datetime(frame.date)).any():
        raise ValueError('label end precedes signal')
    return True


def completed_date_limit(now, release_time='17:20'):
    local = utc(now).tz_convert('Asia/Shanghai')
    hour, minute = map(int, release_time.split(':'))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError('invalid exchange release time')
    limit = local.tz_localize(None).normalize()
    if (local.hour, local.minute) < (hour, minute):
        limit -= pd.Timedelta(days=1)
    return limit


def forecast_role(signal_date, created_at, latest_input_observed_at, calendar, protocol):
    signal, created, observed = day(signal_date), utc(created_at), utc(latest_input_observed_at)
    if created < utc(protocol['frozen_at_utc']):
        raise ValueError('forecast creation precedes the protocol freeze')
    sessions = pd.DatetimeIndex(pd.to_datetime(calendar))
    if sessions.tz is not None or sessions.hasnans or sessions.has_duplicates or not sessions.is_monotonic_increasing or not sessions.equals(sessions.normalize()) or signal not in sessions:
        raise ValueError('invalid complete exchange calendar or signal date')
    if observed > created:
        raise ValueError('forecast uses an input observed in its future')
    decision_floor = (signal + pd.Timedelta(hours=17, minutes=20)).tz_localize('Asia/Shanghai').tz_convert('UTC')
    if created < decision_floor:
        raise ValueError('daily close snapshot cannot be published before the release cutoff')
    later = sessions[sessions > signal]
    if later.empty:
        raise ValueError('next exchange session is required; never guess a weekday')
    entry = (later[0] + pd.Timedelta(hours=9, minutes=30)).tz_localize('Asia/Shanghai').tz_convert('UTC')
    if signal <= day(protocol['research_end']):
        role = 'research_history'
    elif signal < day(protocol['independent_start']):
        raise ValueError('signal falls outside the declared data partitions')
    elif signal >= day(protocol['prospective_start']) and created < entry:
        role = 'prospective_paper_forecast'
    else:
        role = 'retrospective_independent_snapshot'
    return dict(role=role, signal_date=str(signal.date()), created_at_utc=created.isoformat(),
                latest_input_observed_at_utc=observed.isoformat(), next_entry_open_utc=entry.isoformat(),
                prospective=role == 'prospective_paper_forecast', certified=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['seal', 'check'])
    parser.add_argument('--project', default='.')
    parser.add_argument('--protocol')
    parser.add_argument('--out')
    parser.add_argument('--seal')
    args = parser.parse_args()
    if args.command == 'seal':
        if not args.protocol or not args.out: parser.error('--protocol and --out are required')
        result = seal(args.protocol, args.out, args.project)
    else:
        if not args.seal: parser.error('--seal is required')
        _, result = load_seal(args.seal, args.project)
    print(json.dumps(result))

"""Typed rights reconciliation; ordinary cash/share audits remain unchanged.

This view deliberately has no ordinary day_faults method: it cannot silently
waive a rights fault for an account that does not implement paid subscriptions.
Only a separately named rights account may consume this view.
"""
from pathlib import Path
from datetime import datetime, timezone
import json

from .disk_market import ReferenceAudit, code_hashes, write_manifest
from .phase2_protocol import sha256
from .rights_terms import load_rights_evidence, rights_reference, serialize_case


def relative_under(root, name):
    root = Path(root).resolve()
    p = Path(name)
    if p.is_absolute() or '..' in p.parts or ':' in str(p):
        raise ValueError('relative rights audit paths required')
    p = (root/p).resolve()
    if not p.is_relative_to(root):
        raise ValueError('rights audit path escapes root')
    return p


def reconcile_cases(store, parent, events):
    checks = []
    for event in events:
        if event['outcome'] != 'success':
            continue
        date, symbol = event['ex_date'], event['symbol']
        if ((parent.actions.symbol == symbol) & (parent.actions.ex_date == date)).any():
            raise ValueError('ordinary and rights ex-events overlap; explicit reconciliation required')
        i = store.calendar.get_loc(date)
        if i == 0:
            raise ValueError('previous raw session required for rights reference')
        previous = store.calendar[i-1]
        before = store.day(previous, {symbol}).get(symbol)
        current = store.day(date, {symbol}).get(symbol)
        if before is None or current is None:
            raise ValueError('missing raw prices for rights reconciliation')
        proof = rights_reference(before['close'], current['preclose'], event)
        if not proof['matches']:
            raise ValueError('rights reference does not match original raw quote')
        checks.append(dict(event_id=event['event_id'], symbol=symbol,
                           date=str(date.date()), previous_session=str(previous.date()),
                           previous_close=float(before['close']),
                           parent_fault_present=symbol in parent.day_faults(date), **proof))
    return checks


def build_rights_reference(store, parent_directory, config, project, evidence_root, out):
    work = Path(evidence_root).resolve()
    source = Path(parent_directory).resolve()
    target = Path(out).resolve()
    if not source.is_relative_to(work) or not target.is_relative_to(work):
        raise ValueError('reference audit must remain under explicit work root')
    if target.exists() or any(target.is_relative_to(p) or p.is_relative_to(target)
                              for p in [source, store.path, Path(project).resolve()]):
        raise ValueError('fresh rights reference directory outside inputs required')
    parent = ReferenceAudit(source, store)
    try:
        events, evidence = load_rights_evidence(config, project, work, store.calendar)
        checks = reconcile_cases(store, parent, events)
        manifest = dict(schema='rights_reference_audit_v1', status='rights_reference_complete',
                        recorded_at_utc=datetime.now(timezone.utc).isoformat(),
                        requires_rights_wallet=True, retrospective_reference_only=True,
                        parent_directory=source.relative_to(work).as_posix(),
                        parent_manifest_sha256=sha256(source/'manifest.json'),
                        quote_store_manifest_sha256=sha256(store.path/'manifest.json'),
                        quote_database_sha256=store.manifest['database_sha256'],
                        config=config, events=[serialize_case(e) for e in events],
                        reconciliations=checks, **evidence,
                        code_hashes=code_hashes(['rights_reference_audit.py','rights_terms.py']))
        target.mkdir(parents=True)
        write_manifest(target/'manifest.json', manifest)
        return manifest
    finally:
        parent.close()


class RightsReferenceAudit:
    def __init__(self, directory, store, project, evidence_root):
        self.path = Path(directory).resolve()
        self.manifest = m = json.loads((self.path/'manifest.json').read_text(encoding='utf8'))
        if (m.get('schema') != 'rights_reference_audit_v1'
            or m.get('status') != 'rights_reference_complete'
            or m.get('requires_rights_wallet') is not True
            or m.get('retrospective_reference_only') is not True
            or m.get('quote_store_manifest_sha256') != sha256(store.path/'manifest.json')
            or m.get('quote_database_sha256') != store.manifest['database_sha256']
            or code_hashes(list(m['code_hashes'])) != m['code_hashes']):
            raise ValueError('complete unchanged typed rights reference required')
        parent_path = relative_under(evidence_root, m['parent_directory'])
        if sha256(parent_path/'manifest.json') != m['parent_manifest_sha256']:
            raise ValueError('ordinary parent audit changed')
        self.parent = ReferenceAudit(parent_path, store)
        try:
            self.events, proof = load_rights_evidence(m['config'], project, evidence_root, store.calendar)
            if (any(m.get(k) != v for k,v in proof.items())
                or m['events'] != [serialize_case(e) for e in self.events]
                or m['reconciliations'] != reconcile_cases(store, self.parent, self.events)):
                raise ValueError('rights evidence, terms or raw reconciliation changed')
            self.actions = self.parent.actions
            self.explained = {(r['date'], r['symbol']) for r in m['reconciliations']}
        except Exception:
            self.parent.close()
            raise

    def close(self):
        self.parent.close()

    def day_faults_for_rights(self, date):
        key = str(date.date())
        return self.parent.day_faults(date) - {s for d,s in self.explained if d == key}

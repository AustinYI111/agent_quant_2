"""Normalize a new earnings capture without backdating observed revisions."""
from pathlib import Path
import json
import tempfile
import pandas as pd

from .balance_batch import gate_first_observed
from .financial_batch import normalize_financial_batch
from .phase2_protocol import sha256


def normalize_observed_financials(directory, calendar, out):
    source, target = Path(directory), Path(out)
    if target.exists():
        raise ValueError('new observed-financial output required')
    manifest = json.loads((source/'manifest.json').read_text(encoding='utf8'))
    if manifest.get('schema') != 'quarterly_financial_raw_v1' or manifest.get('status') != 'downloaded_raw':
        raise ValueError('complete sealed earnings capture required')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='observed-financials-', dir=target.parent) as temporary:
        staged = Path(temporary)/'panel'
        original = normalize_financial_batch(source, calendar, staged)
        frame = pd.read_csv(staged/'financial_panel.csv', parse_dates=['report_date','available_date'])
        pieces = []
        for quarter, group in frame.groupby('origin_quarter', dropna=False, sort=False):
            date = str(pd.Timestamp(quarter).date())
            info = manifest['quarters'].get(date)
            if not info or info.get('status') != 'ok':
                raise ValueError('missing complete-quarter observation evidence')
            pieces.append(gate_first_observed(group, info['retrieved_at'], calendar))
        if not pieces:
            raise ValueError('empty observed financial panel')
        result = pd.concat(pieces, ignore_index=True)
        result.to_csv(staged/'financial_panel.csv', index=False)
        proof = original | dict(schema='observed_financial_panel_v1', original_vintages_verified=False,
            prior_normalized_panel_sha256=original['panel_sha256'], panel_sha256=sha256(staged/'financial_panel.csv'),
            policy='Original notice/update/ingest next-session rule, no earlier than complete-quarter first-observed China date.',
            visible_records=int((result.available_date.notna() & result.symbol.notna()).sum()))
        (staged/'summary.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf8')
        staged.rename(target)
    return proof

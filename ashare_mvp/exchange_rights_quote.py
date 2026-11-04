"""Reviewed SSE monthly quotation, usable only by the typed rights audit.

The historical publication is retrospective price evidence. It never supplies
subscription entitlements, issue counts, availability dates or model features.
"""
from pathlib import Path
from decimal import Decimal
import json
import re

import pandas as pd

from .phase2_protocol import sha256, utc, day


def _under(root, name):
    root = Path(root).resolve()
    p = Path(name)
    if p.is_absolute() or '..' in p.parts or ':' in str(p):
        raise ValueError('relative exchange evidence path required')
    p = (root / p).resolve()
    if not p.is_relative_to(root):
        raise ValueError('exchange evidence escapes root')
    return p


def _rows(text, side):
    if side == 'identity':
        required = ['股票代码', '股权登记日', '配股比例', '配股价格']
        pattern = r'^(\d{6})\s+(.+?)\s+(\d{4}\.\d{2}\.\d{2})\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)$'
    else:
        required = ['除权基准日', '除权前日收盘价', '除权报价', '股份上市日']
        pattern = r'^([\d.]+)\s+([\d.]+)\s+(\d{4}\.\d{2}\.\d{2})\s+([\d.]+)\s+([\d.]+)\s+(\d{4}\.\d{2}\.\d{2})$'
    if not isinstance(text, str) or not all(h in text for h in required):
        raise ValueError('explicit exchange table headings required')
    return [m.groups() for line in text.splitlines()
            if (m := re.fullmatch(pattern, line.strip()))]


def load_exchange_quote(item, event, evidence_root):
    """Bind paired pages by row position, issuer, dates, terms and capture bytes."""
    if (item.get('schema') != 'reviewed_sse_monthly_rights_quote_v1'
            or item.get('reviewed') is not True
            or item.get('retrospective_reference_only') is not True
            or item.get('historical_availability_asserted') is not False):
        raise ValueError('reviewed retrospective exchange quotation required')
    url = item['source_url']
    if not re.fullmatch(r'https://www\.sse\.com\.cn/aboutus/publication/monthly/documents/c/\d+/files/[a-f0-9]{32}\.pdf', url):
        raise ValueError('primary SSE monthly attachment required')
    pdf = _under(evidence_root, item['source_file'])
    capture = _under(evidence_root, item['capture_file'])
    if sha256(pdf) != item['source_sha256'] or sha256(capture) != item['capture_sha256']:
        raise ValueError('exchange PDF or capture changed')
    with pdf.open('rb') as stream:
        if stream.read(5) != b'%PDF-':
            raise ValueError('original exchange PDF required')
    c = json.loads(capture.read_text(encoding='utf8'))
    observed, reviewed = utc(item['observed_at_utc']), utc(item['reviewed_at_utc'])
    if (c.get('schema') != 'primary_exchange_monthly_capture_v1'
            or c.get('source_url') != url or c.get('final_url') != url
            or c.get('sha256') != item['source_sha256'] or c.get('bytes') != pdf.stat().st_size
            or c.get('historical_availability_asserted') is not False
            or c.get('used_for_historical_selection') is not False
            or utc(c['completed_at_utc']) != observed
            or not utc(c['started_at_utc']) <= observed <= reviewed <= pd.Timestamp.now(tz='UTC')):
        raise ValueError('exchange capture identity or clocks differ')
    pages = item['reviewed_pages']
    if (not isinstance(pages, list) or len(pages) != 2
            or any(isinstance(p, bool) or not isinstance(p, int) or p < 1 for p in pages)
            or pages[1] != pages[0] + 1 or pages[1] > c['page_count']):
        raise ValueError('two reviewed consecutive table pages required')
    left = _rows(c['extracted_pages'].get(str(pages[0])), 'identity')
    right = _rows(c['extracted_pages'].get(str(pages[1])), 'quotation')
    if not left or len(left) != len(right):
        raise ValueError('paired exchange pages must have identical nonzero row counts')
    matches = [(i, r) for i, r in enumerate(left) if r[0] == event['symbol'].split('.')[1]]
    if len(matches) != 1 or event['symbol'].split('.')[0] != 'sh' or event['outcome'] != 'success':
        raise ValueError('unique successful SSE rights identity required')
    i, row = matches[0]
    quote = right[i]
    if (day(row[2]) != event['record_date'] or Decimal(row[3]) != 0
            or Decimal(row[4]) != event['offer_ratio']
            or Decimal(row[5]) != event['subscription_price']
            or Decimal(quote[0]) != 0 or Decimal(quote[1]) != 0
            or day(quote[2]) != event['ex_date'] or day(quote[5]) != event['tradable_from']):
        raise ValueError('exchange table issuer terms or settlement dates differ')
    for value in quote[3:5]:
        d = Decimal(value)
        if not d.is_finite() or d <= 0 or d.quantize(Decimal('.01')) != d:
            raise ValueError('positive one-cent exchange quotations required')
    proof = dict(symbol=event['symbol'], event_id=event['event_id'],
        ex_date=str(event['ex_date'].date()), previous_close=quote[3],
        ex_reference=quote[4], source_sha256=item['source_sha256'],
        source_url=url, reviewed_pages=pages, row_number=i+1,
        observed_at_utc=item['observed_at_utc'], reviewed_at_utc=item['reviewed_at_utc'],
        retrospective_reference_only=True, historical_availability_asserted=False)
    root = Path(evidence_root).resolve()
    return proof, {pdf.relative_to(root).as_posix(): item['source_sha256'],
                   capture.relative_to(root).as_posix(): item['capture_sha256']}


def reconcile_exchange_quote(previous, observed, event, formula_proof):
    """No formula substitution: independently match both published quote fields."""
    q = event['reviewed_exchange_quote']
    if (q['symbol'] != event['symbol'] or q['event_id'] != event['event_id']
            or q['ex_date'] != str(event['ex_date'].date())
            or q['retrospective_reference_only'] is not True):
        raise ValueError('reviewed quotation belongs to a different rights event')
    matches = (Decimal(str(previous)) == Decimal(q['previous_close'])
               and Decimal(str(observed)) == Decimal(q['ex_reference']))
    return dict(matches=matches, observed_preclose=str(observed),
        reference_basis='reviewed_primary_exchange_quotation', exchange_quote=q,
        capital_count_formula_diagnostic=formula_proof,
        reference_role='retrospective quote reconciliation only; not an input to offer subscription or earlier ranking')

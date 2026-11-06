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
    if item.get('schema') == 'reviewed_sse_monthly_rights_json_quote_v1':
        return load_exchange_json_quote(item, event, evidence_root)
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


def _definition_sources(item, evidence_root, reviewed):
    """Bind publisher script headings to fields; never infer from price fit."""
    d = item['field_definition']
    script = _under(evidence_root, d['script_file'])
    receipt = _under(evidence_root, d['capture_file'])
    if sha256(script) != d['script_sha256'] or sha256(receipt) != d['capture_sha256']:
        raise ValueError('exchange field definition changed')
    c = json.loads(receipt.read_text(encoding='utf8'))
    if (c.get('source_url') != 'https://www.sse.com.cn/xhtml/home/2021public/querySearch/search_publication_branch_2021.js'
            or c.get('sha256') != d['script_sha256'] or utc(c['observed_at_utc']) > reviewed):
        raise ValueError('official monthly field definition identity or clock differs')
    script_text = script.read_text(encoding='utf8')
    def array(method, variable):
        pattern = method + r'\s*:\s*function\s*\([^)]*\)\s*\{.*?var\s+' + variable + r'\s*=\s*\[(.*?)\]'
        found = re.search(pattern, script_text, re.S)
        if found is None:
            raise ValueError('explicit monthly field mapping required')
        return re.findall(r'"([^"\n]+)"', found[1])
    fields = ['CODE','ABBR','RECDATE','BONRATE','RIGRATE','RIGPRICE','TRANRATE','PERFEE','EXDATE','PREPRICE','EXPRICE','FIRDATE']
    headings = ['股票代码','股票简称','股权登记日','送股比例','配股比例','配股价格','转配比例','每股转让费','除权基准日','除权前日收盘价','除权报价','股份上市日']
    if (array('detailTableSeven', 'rowData') != fields or array('initTableSeven', 'theadList') != headings
            or 'COMMON_SSE_SSGS_M_LC_RIGHTS_C' not in script_text):
        raise ValueError('publisher monthly field mapping differs')
    root = Path(evidence_root).resolve()
    return {script.relative_to(root).as_posix(): d['script_sha256'],
            receipt.relative_to(root).as_posix(): d['capture_sha256']}


def load_exchange_json_quote(item, event, evidence_root):
    """Validate a captured page of the official monthly rights query, offline."""
    from urllib.parse import urlsplit, parse_qsl
    if (item.get('schema') != 'reviewed_sse_monthly_rights_json_quote_v1'
            or item.get('reviewed') is not True or item.get('retrospective_reference_only') is not True
            or item.get('historical_availability_asserted') is not False):
        raise ValueError('reviewed retrospective exchange JSON quotation required')
    original = _under(evidence_root, item['source_file'])
    receipt = _under(evidence_root, item['capture_file'])
    if sha256(original) != item['source_sha256'] or sha256(receipt) != item['capture_sha256']:
        raise ValueError('exchange JSON or capture changed')
    c = json.loads(receipt.read_text(encoding='utf8'))
    observed, reviewed = utc(item['observed_at_utc']), utc(item['reviewed_at_utc'])
    if (c.get('schema') != 'sse_monthly_rights_json_capture_v1'
            or c.get('source_url') != item['source_url'] or c.get('final_url') != item['source_url']
            or c.get('sha256') != item['source_sha256'] or c.get('bytes') != original.stat().st_size
            or c.get('historical_original_vintage_verified') is not False or c.get('events_applied') is not False
            or utc(c['observed_at_utc']) != observed
            or not utc(c['started_at_utc']) <= observed <= reviewed <= pd.Timestamp.now(tz='UTC')):
        raise ValueError('exchange JSON capture identity or clocks differ')
    u = urlsplit(item['source_url'])
    query = parse_qsl(u.query, keep_blank_values=True)
    params = c['params']
    required = {'sqlId','isPagination','pageHelp.pageSize','pageHelp.pageNo','pageHelp.beginPage','pageHelp.cacheSize','pageHelp.endPage','RECDATE'}
    if (u.scheme != 'https' or u.netloc != 'query.sse.com.cn' or u.path != '/commonQuery.do' or u.fragment
            or len(query) != len(dict(query)) or set(dict(query)) != required or set(params) != required
            or dict(query) != {k:str(v) for k,v in params.items()}
            or params['sqlId'] != 'COMMON_SSE_SSGS_M_LC_RIGHTS_C' or params['isPagination'] != 'true'
            or params['RECDATE'] != event['record_date'].strftime('%Y%m')):
        raise ValueError('original monthly query parameters differ')
    for key in ['pageHelp.pageSize','pageHelp.pageNo','pageHelp.beginPage','pageHelp.cacheSize','pageHelp.endPage']:
        if isinstance(params[key], bool) or not isinstance(params[key], int) or params[key] < 1:
            raise ValueError('integer exchange pagination required')
    size, number = params['pageHelp.pageSize'], params['pageHelp.pageNo']
    if size > 100 or not params['pageHelp.beginPage'] <= number <= params['pageHelp.endPage']:
        raise ValueError('bounded valid exchange page required')
    body = json.loads(original.read_text(encoding='utf8'))
    rows, page = body.get('result'), body.get('pageHelp')
    if (body.get('sqlId') != params['sqlId'] or body.get('isPagination') != 'true'
            or body.get('actionErrors') or body.get('fieldErrors')
            or not isinstance(rows,list) or not isinstance(page,dict) or page.get('data') != rows):
        raise ValueError('successful consistent monthly response required')
    for key in ['pageNo','pageSize','pageCount','total']:
        if isinstance(page.get(key),bool) or not isinstance(page.get(key),int) or page[key] < 1:
            raise ValueError('explicit nonempty response pagination required')
    if (page['pageNo'] != number or page['pageSize'] != size
            or page['pageCount'] != (page['total'] + size - 1)//size
            or number > page['pageCount'] or len(rows) != min(size,page['total']-(number-1)*size)):
        raise ValueError('missing, partial or inconsistent monthly page')
    if any(not isinstance(r,dict) or not re.fullmatch(r'\d{6}',str(r.get('CODE','')))
           or not re.fullmatch(params['RECDATE']+r'\d{2}',str(r.get('RECDATE',''))) for r in rows):
        raise ValueError('monthly row identity or requested period differs')
    selected = [(i,r) for i,r in enumerate(rows) if r['CODE'] == event['symbol'].split('.')[1]]
    if len(selected) != 1 or not event['symbol'].startswith('sh.') or event['outcome'] != 'success':
        raise ValueError('unique successful SSE rights identity required')
    i, row = selected[0]
    def source_day(name):
        value = row[name]
        if not isinstance(value,str) or not re.fullmatch(r'\d{8}',value):
            raise ValueError('exact exchange settlement date required')
        return pd.to_datetime(value, format='%Y%m%d')
    def source_decimal(name):
        value = row.get(name)
        if not isinstance(value,str) or not re.fullmatch(r'\d+(?:\.\d+)?',value):
            raise ValueError('explicit nonnegative exchange number required')
        return Decimal(value)
    if (source_day('RECDATE') != event['record_date'] or source_day('EXDATE') != event['ex_date']
            or source_day('FIRDATE') != event['tradable_from']
            or source_decimal('RIGRATE') != event['offer_ratio'] or source_decimal('RIGPRICE') != event['subscription_price']
            or any(source_decimal(k) != 0 for k in ['BONRATE','TRANRATE','PERFEE'])):
        raise ValueError('exchange JSON issuer terms or settlement dates differ')
    for name in ['PREPRICE','EXPRICE']:
        value = source_decimal(name)
        if not value.is_finite() or value <= 0 or value.quantize(Decimal('.01')) != value:
            raise ValueError('positive one-cent exchange quotations required')
    sources = _definition_sources(item, evidence_root, reviewed)
    root = Path(evidence_root).resolve()
    sources.update({original.relative_to(root).as_posix():item['source_sha256'],
                    receipt.relative_to(root).as_posix():item['capture_sha256']})
    proof = dict(symbol=event['symbol'],event_id=event['event_id'],ex_date=str(event['ex_date'].date()),
        previous_close=row['PREPRICE'],ex_reference=row['EXPRICE'],source_sha256=item['source_sha256'],
        source_url=item['source_url'],source_format='sse_monthly_json',row_number=(number-1)*size+i+1,
        field_definition_sha256=item['field_definition']['script_sha256'],
        observed_at_utc=item['observed_at_utc'],reviewed_at_utc=item['reviewed_at_utc'],
        retrospective_reference_only=True,historical_availability_asserted=False)
    return proof, sources

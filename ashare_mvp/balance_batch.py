"""Quarterly public balance cross-sections with explicit first-observed clocks.

Request contract: AKShare stock_zcfz_em / Eastmoney RPT_DMSK_FN_BALANCE.
This endpoint has NOTICE_DATE but does not supply an update/version clock.
Never invent UPDATE_DATE or use the current response as an original vintage.
"""
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
import time

import numpy as np
import pandas as pd

from .network_retry import fetch_with_retry
from .phase2_protocol import day, sha256, utc


def get_balance_page(report_date, page):
    params = dict(sortColumns='NOTICE_DATE,SECURITY_CODE', sortTypes='-1,-1', pageSize='500',
                  pageNumber=str(page), reportName='RPT_DMSK_FN_BALANCE', columns='ALL',
                  filter='(SECURITY_TYPE_CODE in ("058001001","058001008"))'
                         '(TRADE_MARKET_CODE!="069001017")'+f"(REPORT_DATE='{report_date}')")
    request = Request('https://datacenter-web.eastmoney.com/api/data/v1/get?'+urlencode(params),
                      headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
    with urlopen(request, timeout=25) as response:
        return json.load(response)


def _quarter(date, path, fetch, delay):
    first = fetch_with_retry(lambda: fetch(date, 1))
    result = first.get('result')
    if first.get('success') is not True or not result:
        raise ValueError('balance provider failed')
    count, pages = int(result['count']), int(result['pages'])
    if count < 1 or not 1 <= pages <= 100 or pages != (count+499)//500:
        raise ValueError('invalid balance pagination')
    path.mkdir(parents=True, exist_ok=True)
    rows, hashes, clocks = [], {}, []
    for page in range(1, pages+1):
        payload = first if page == 1 else fetch_with_retry(lambda: fetch(date, page))
        current = payload.get('result')
        if (payload.get('success') is not True or not current or int(current['count']) != count
                or int(current['pages']) != pages):
            raise ValueError('balance page total changed or provider failed')
        data = current.get('data')
        expected = min(500, count-(page-1)*500)
        if not isinstance(data, list) or len(data) != expected:
            raise ValueError('balance page missing rows')
        file = path/f'page_{page:03d}.json'
        file.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf8')
        hashes[file.name] = sha256(file)
        clocks.append(datetime.now(timezone.utc).isoformat())
        rows.extend(data)
        if page < pages:
            time.sleep(delay)
    frame = pd.DataFrame(rows)
    required = {'SECUCODE', 'SECURITY_CODE', 'REPORT_DATE', 'NOTICE_DATE', 'TOTAL_ASSETS', 'TOTAL_LIABILITIES'}
    if (not required.issubset(frame) or frame.SECUCODE.isna().any()
            or frame.duplicated(['SECUCODE', 'REPORT_DATE']).any()):
        raise ValueError('balance schema or unique identity differs')
    periods = pd.to_datetime(frame.REPORT_DATE, errors='raise')
    if periods.isna().any() or not periods.eq(pd.Timestamp(date)).all():
        raise ValueError('balance report period differs')
    notices = pd.to_datetime(frame.NOTICE_DATE, errors='raise')
    if notices.isna().any() or notices.lt(periods).any():
        raise ValueError('missing or invalid balance publication clock')
    security = frame.SECUCODE.astype(str).str.split('.')
    if not frame.SECURITY_CODE.astype(str).eq(security.str[0]).all():
        raise ValueError('balance security code mismatch')
    frame.to_csv(path/'records.csv', index=False)
    hashes['records.csv'] = sha256(path/'records.csv')
    return dict(status='ok', rows=count, pages=pages, hashes=hashes,
                first_observed_at_utc=min(clocks), complete_observed_at_utc=max(clocks),
                provider_update_clock_supplied='UPDATE_DATE' in frame, original_vintages_verified=False)


def download_balance_batch(out, start, end, *, resume=False, fetch=get_balance_page, delay=.2):
    start, end = day(start), day(end)
    if start > end or delay < 0:
        raise ValueError('invalid balance acquisition range or delay')
    dates = pd.date_range(start, end, freq='QE')
    if dates.empty:
        raise ValueError('balance range has no quarter end')
    path = Path(out)
    manifest_file = path/'manifest.json'
    request = dict(start=str(start.date()), end=str(end.date()))
    if resume:
        manifest = json.loads(manifest_file.read_text(encoding='utf8'))
        if manifest.get('schema') != 'quarterly_balance_raw_v1' or manifest.get('request') != request:
            raise ValueError('balance resume request differs')
    else:
        if path.exists() and any(path.iterdir()):
            raise ValueError('new empty balance output required')
        path.mkdir(parents=True, exist_ok=True)
        manifest = dict(schema='quarterly_balance_raw_v1', request=request, source='Eastmoney RPT_DMSK_FN_BALANCE',
                        synthetic=False, certified=False, model_ready=False, original_vintages_verified=False,
                        quarters={}, limitations=['Current supplier snapshot; NOTICE_DATE is not a version clock.',
                            'First observed clocks must gate use; do not backdate updated records.',
                            'Partial reporting coverage is not complete historical A-share financial coverage.'])
    def save():
        manifest['updated_at_utc'] = datetime.now(timezone.utc).isoformat()
        pending = manifest_file.with_suffix('.pending.json')
        pending.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf8')
        pending.replace(manifest_file)
    manifest['status'] = 'running'
    save()
    failures = 0
    for date in dates:
        key = str(date.date()); folder = path/f'{date.year}Q{date.quarter}'
        prior = manifest['quarters'].get(key)
        if prior and prior.get('status') == 'ok':
            for name, expected in prior['hashes'].items():
                if Path(name).name != name or sha256(folder/name) != expected:
                    raise ValueError('balance cached evidence changed')
            continue
        try:
            manifest['quarters'][key] = _quarter(key, folder, fetch, delay)
            failures = 0
        except Exception as exc:
            manifest['quarters'][key] = dict(status='failed', error=str(exc))
            failures += 1
        save()
        print(f"Balance acquisition: {sum(row['status']=='ok' for row in manifest['quarters'].values())}/{len(dates)} quarters", flush=True)
        if failures >= 3:
            break
    manifest['requested_quarters'] = len(dates)
    manifest['completed_quarters'] = sum(row['status']=='ok' for row in manifest['quarters'].values())
    manifest['status'] = 'downloaded_raw' if manifest['completed_quarters'] == len(dates) else 'partial'
    save()
    return manifest


def gate_first_observed(frame, observed_at, calendar):
    """No newly captured revision can become visible before its observation day.

    Per-forecast UTC clocks still have to pass prediction_ledger's timing gate.
    A date-only panel cannot by itself certify intraday publication timing.
    """
    sessions = pd.DatetimeIndex(pd.to_datetime(calendar))
    if (sessions.empty or sessions.tz is not None or sessions.hasnans or sessions.has_duplicates
            or not sessions.is_monotonic_increasing or not sessions.equals(sessions.normalize())):
        raise ValueError('ordered normalized exchange sessions required')
    observation = utc(observed_at)
    local_day = observation.tz_convert('Asia/Shanghai').tz_localize(None).normalize()
    result = frame.copy()
    available = pd.to_datetime(result.available_date, errors='raise')
    if available.dt.tz is not None or not available.dropna().eq(available.dropna().dt.normalize()).all():
        raise ValueError('normalized financial availability dates required')
    floor = available.where(available.ge(local_day), local_day)
    positions = sessions.searchsorted(floor, side='left')
    result['available_date'] = [sessions[pos] if pd.notna(original) and pos < len(sessions) else pd.NaT
                                for original, pos in zip(available, positions)]
    result['first_observed_at_utc'] = observation.isoformat()
    result['original_vintages_verified'] = False
    return result


def normalize_balance_batch(directory, calendar_path, out):
    source, target = Path(directory), Path(out)
    if target.exists() and any(target.iterdir()):
        raise ValueError('new empty normalized balance output required')
    manifest = json.loads((source/'manifest.json').read_text(encoding='utf8'))
    if manifest.get('schema') != 'quarterly_balance_raw_v1' or manifest.get('status') != 'downloaded_raw':
        raise ValueError('complete sealed balance batch required')
    calendar = pd.read_csv(calendar_path, dtype=str)
    sessions = pd.DatetimeIndex(pd.to_datetime(calendar.loc[calendar.is_trading_day.eq('1'), 'calendar_date']))
    # Validate calendar even when all balances would be outside its boundary.
    gate_first_observed(pd.DataFrame(dict(available_date=[pd.Timestamp('2026-01-01')])),
                        manifest['updated_at_utc'], sessions)
    panels, excluded = [], []
    for date, info in manifest['quarters'].items():
        if info.get('status') != 'ok':
            raise ValueError('unfinished balance quarter')
        period = day(date); folder = source/f'{period.year}Q{period.quarter}'
        for name, expected in info['hashes'].items():
            if Path(name).name != name or sha256(folder/name) != expected:
                raise ValueError('balance raw hash mismatch')
        frame = pd.read_csv(folder/'records.csv', dtype={'SECUCODE': str, 'SECURITY_CODE': str})
        if len(frame) != info['rows']:
            raise ValueError('balance row count changed')
        symbols = frame.SECUCODE.str.replace(r'^(\d{6})\.(SH|SZ)$', lambda match:match[2].lower()+'.'+match[1], regex=True)
        from .public_data import is_ashare
        if not frame.SECUCODE.str.fullmatch(r'\d{6}\.(SH|SZ)', na=False).all():
            raise ValueError('malformed balance security identity')
        supported = symbols.map(is_ashare)
        if not supported.all():
            outside = frame.loc[~supported, ['SECUCODE','SECURITY_CODE','REPORT_DATE']].copy()
            outside['reason'] = 'outside_explicit_shanghai_shenzhen_ashare_universe'
            excluded.append(outside)
        # The raw batch includes depositary receipts; preserve their exclusion
        # explicitly. This never removes positions or quotes from an account.
        frame, symbols = frame.loc[supported].copy(), symbols.loc[supported]
        if frame.empty:
            continue
        publication = pd.to_datetime(frame.NOTICE_DATE, errors='raise').dt.normalize()
        reports = pd.to_datetime(frame.REPORT_DATE, errors='raise').dt.normalize()
        if publication.isna().any() or reports.isna().any() or publication.le(reports).any():
            raise ValueError('invalid balance publication clock')
        assets = pd.to_numeric(frame.TOTAL_ASSETS, errors='raise')
        liabilities = pd.to_numeric(frame.TOTAL_LIABILITIES, errors='raise')
        if ((assets.notna() & (~np.isfinite(assets) | assets.le(0)))
                | (liabilities.notna() & (~np.isfinite(liabilities) | liabilities.lt(0)))).any():
            raise ValueError('invalid balance amounts')
        positions = sessions.searchsorted(publication, side='right')
        panel = pd.DataFrame(dict(symbol=symbols, report_date=reports, publication_date=publication,
            provider_update_date=pd.NaT, debt_ratio=liabilities/assets, certified=False,
            available_date=[sessions[pos] if pos < len(sessions) else pd.NaT for pos in positions]))
        # Use complete-quarter observation conservatively for every row.
        panel = gate_first_observed(panel, info['complete_observed_at_utc'], sessions)
        panels.append(panel)
    if not panels:
        raise ValueError('no completed balance quarters')
    result = pd.concat(panels, ignore_index=True).sort_values(['symbol','report_date','available_date'])
    if result.duplicated(['symbol','report_date','available_date']).any():
        raise ValueError('ambiguous normalized balance versions')
    target.mkdir(parents=True, exist_ok=True)
    result.to_csv(target/'balance_panel.csv', index=False)
    excluded_frame = pd.concat(excluded, ignore_index=True) if excluded else pd.DataFrame(columns=['SECUCODE','SECURITY_CODE','REPORT_DATE','reason'])
    excluded_frame.to_csv(target/'excluded_securities.csv', index=False)
    proof = dict(schema='observed_balance_panel_v1', rows=len(result), records=len(result), symbols=int(result.symbol.nunique()),
                 partial_source=False,
                 excluded_rows=len(excluded_frame), excluded_sha256=sha256(target/'excluded_securities.csv'),
                 panel_sha256=sha256(target/'balance_panel.csv'), input_manifest_sha256=sha256(source/'manifest.json'),
                 calendar_sha256=sha256(calendar_path), certified=False, original_vintages_verified=False,
                 policy='Next session after NOTICE_DATE, no earlier than first-observed China date; unknown update clock stays missing.')
    (target/'summary.json').write_text(json.dumps(proof, indent=2), encoding='utf8')
    return proof

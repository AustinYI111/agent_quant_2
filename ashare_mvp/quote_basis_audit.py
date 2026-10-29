"""Read-only comparison of raw prices and reported change bases.

An unadjusted K-line change is not an independently supplied exchange ex-rights
reference. Never replace preclose using close-minus-change or fit an action ratio.
The field order is the explicitly requested Eastmoney f51..f61 daily contract.
"""
from decimal import Decimal,ROUND_HALF_UP,InvalidOperation
from pathlib import Path
from datetime import datetime,timezone
import json,re
import pandas as pd
from .phase2_protocol import sha256,utc,day
from .disk_market import QuoteStore,code_hashes,write_manifest
from .public_data import is_ashare

FIELDS='f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'
TICK=Decimal('.01')


def number(value):
    try:d=Decimal(str(value))
    except InvalidOperation as error:raise ValueError('finite quote number required') from error
    if not d.is_finite():raise ValueError('finite quote number required')
    return d


def parse_raw_history(payload,parameters):
    if parameters.get('fqt')!='0' or parameters.get('klt')!='101' or parameters.get('fields2')!=FIELDS:
        raise ValueError('explicit unadjusted daily f51..f61 contract required')
    secid=parameters.get('secid','');match=re.fullmatch(r'([01])\.(\d{6})',secid)
    if not match:raise ValueError('identified Shanghai/Shenzhen source required')
    symbol=('sh.' if match[1]=='1' else 'sz.')+match[2]
    data=payload.get('data')
    if not is_ashare(symbol) or payload.get('rc')!=0 or not data or data.get('code')!=match[2] or data.get('market')!=int(match[1]):
        raise ValueError('source status or issuer identity differs')
    if data.get('decimal')!=2:raise ValueError('two-decimal raw stock prices required')
    anchor=number(data.get('preKPrice'))
    if anchor<=0:raise ValueError('known window anchor required')
    lower=pd.to_datetime(parameters['beg'],format='%Y%m%d');upper=pd.to_datetime(parameters['end'],format='%Y%m%d')
    rows=[];last=None
    for raw in data.get('klines') or []:
        cells=raw.split(',')
        if len(cells)!=11:raise ValueError('daily source row width differs')
        date=day(cells[0]);values=[number(x) for x in cells[1:]]
        op,close,high,low,volume,amount,amplitude,pct,change,turn=values
        if not lower<=date<=upper or last is not None and date<=last:raise ValueError('unique ordered in-window trading rows required')
        if min(op,close,high,low)<=0 or high<max(op,close,low) or low>min(op,close,high) or min(volume,amount,amplitude,turn)<0:raise ValueError('invalid raw daily quote domain')
        rows.append(dict(date=date,symbol=symbol,open=op,close=close,pct=pct,change=change));last=date
    if not rows:raise ValueError('nonempty raw daily history required')
    return symbol,anchor,rows


def compare_change_bases(rows,anchor,original):
    """Original is a date-keyed mapping of open/close/preclose, no price repairs."""
    records=[];previous=anchor
    for row in rows:
        date=str(row['date'].date());bar=original.get(date)
        if bar is None:raise ValueError('missing original comparison quote')
        close=row['close'];observed=number(bar['preclose'])
        if previous<=0 or observed<=0:raise ValueError('positive comparison bases required')
        raw_pct=((close/previous-1)*100).quantize(TICK,rounding=ROUND_HALF_UP)
        reference_pct=((close/observed-1)*100).quantize(TICK,rounding=ROUND_HALF_UP)
        raw_change=(close-previous).quantize(TICK,rounding=ROUND_HALF_UP)
        records.append(dict(date=date,symbol=row['symbol'],independent_open=str(row['open']),independent_close=str(close),
                            original_open=str(number(bar['open'])),original_close=str(number(bar['close'])),
                            open_matches=row['open']==number(bar['open']),close_matches=close==number(bar['close']),
                            preceding_unadjusted_close=str(previous),original_reference_preclose=str(observed),
                            reported_pct=str(row['pct']),reported_change=str(row['change']),
                            recomputed_raw_pct=str(raw_pct),recomputed_reference_pct=str(reference_pct),
                            raw_change_matches=row['change']==raw_change,raw_pct_matches=row['pct']==raw_pct,
                            reference_pct_matches=row['pct']==reference_pct,
                            close_minus_change=str(close-row['change']),independent_ex_reference_supplied=False))
        previous=close
    return records


def audit_captured_history(store_directory,capture_directory,out):
    source=Path(capture_directory).resolve();target=Path(out).resolve()
    if target.exists() or target.is_relative_to(source) or source.is_relative_to(target):raise ValueError('fresh separate audit output required')
    capture=source/'capture.json';m=json.loads(capture.read_text(encoding='utf8'));body=source/'response.json'
    if m.get('status')!='raw_captured' or sha256(body)!=m['response_sha256'] or utc(m['observed_at_utc'])>utc(datetime.now(timezone.utc)):raise ValueError('unchanged actually observed raw response required')
    symbol,anchor,rows=parse_raw_history(json.loads(body.read_text(encoding='utf8')),m['parameters'])
    store=QuoteStore(store_directory)
    try:
        lower=rows[0]['date'];upper=rows[-1]['date'];sessions=store.calendar[(store.calendar>=lower)&(store.calendar<=upper)]
        original={str(d.date()):store.day(d,{symbol}).get(symbol) for d in sessions}
        original={d:b for d,b in original.items() if b is not None}
        active={d for d,b in original.items() if b['tradestatus']=='1'};seen={str(r['date'].date()) for r in rows}
        previous=store.calendar[store.calendar<lower][-1];prior=store.day(previous,{symbol}).get(symbol)
        records=compare_change_bases(rows,anchor,original)
        proof=dict(schema='independent_raw_quote_basis_audit_v1',status='comparison_complete_reference_unresolved',
                   audited_at_utc=datetime.now(timezone.utc).isoformat(),symbol=symbol,rows=len(rows),source_capture_sha256=sha256(capture),
                   response_sha256=sha256(body),quote_store_manifest_sha256=sha256(store.path/'manifest.json'),
                   original_database_sha256=store.manifest['database_sha256'],code_hashes=code_hashes(['quote_basis_audit.py']),
                   comparisons=records,active_dates_missing_from_independent=sorted(active-seen),independent_dates_not_active_original=sorted(seen-active),
                   absent_original_sessions=len(sessions)-len(original),window_anchor_matches_original_prior_close=prior is not None and anchor==number(prior['close']),
                   exact_active_date_coverage=active==seen and len(original)==len(sessions),
                   all_open_close_prices_match=all(r['open_matches'] and r['close_matches'] for r in records),
                   all_reported_changes_match_raw_preceding_close=all(r['raw_change_matches'] and r['raw_pct_matches'] for r in records),
                   direct_exchange_ex_reference_crosscheck_available=False,reference_fault_waived=False,original_quotes_changed=False,
                   historical_vintages_verified=False,definition_authority='https://akshare.akfamily.xyz/data/stock/stock_quote.html',
                   limitations=['Only this issuer and these observed active dates compared; not full-history validation.',
                                'Only open and close prices compared across providers; volume units not assumed equal.',
                                'Source fqt0 change fields can match a preceding raw close without independently supplying an exchange ex-rights reference.',
                                'No adjustment ratio chosen, no price repaired and no account stop bypassed.'])
        if sha256(body)!=m['response_sha256']:raise ValueError('raw source changed during audit')
        target.mkdir(parents=True);pd.DataFrame(records).to_csv(target/'comparison.csv',index=False);write_manifest(target/'manifest.json',proof)
        return proof
    finally:store.close()

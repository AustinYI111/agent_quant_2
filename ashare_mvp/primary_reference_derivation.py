"""Quote-independent A-share reference allocation from reviewed primary counts."""
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal
from urllib.parse import urlparse
import json,math
import pandas as pd
from .phase2_protocol import sha256,day,utc


def allocated_a_share_cash(cash_per_eligible_share,eligible_a_shares,excluded_a_shares,h_shares,issuer_total_shares):
    counts=[eligible_a_shares,excluded_a_shares,h_shares,issuer_total_shares]
    if any(isinstance(v,bool) or not isinstance(v,int) or v<0 for v in counts) or eligible_a_shares<=0 or issuer_total_shares<=0:
        raise ValueError('literal nonnegative integer share counts required')
    if eligible_a_shares+excluded_a_shares+h_shares!=issuer_total_shares:
        raise ValueError('A and H share classes must reconcile to issuer total')
    if isinstance(cash_per_eligible_share,bool):raise ValueError('literal positive cash required')
    cash=Decimal(str(cash_per_eligible_share))
    if not cash.is_finite() or cash<=0:raise ValueError('literal positive cash required')
    # No market price, outcome, candidate score or adjustable tolerance is input.
    return float(cash*eligible_a_shares/(eligible_a_shares+excluded_a_shares))


def verify_primary_reference_derivations(records,project,calendar,research_end):
    root=Path(project).resolve();cal=pd.DatetimeIndex(pd.to_datetime(calendar));end=day(research_end)
    if cal.empty or cal.hasnans or cal.has_duplicates or cal.tz is not None or not cal.is_monotonic_increasing or not cal.equals(cal.normalize()) or cal.max()>end:raise ValueError('ordered research-only calendar required')
    def local(name):
        p=Path(name)
        if p.is_absolute() or '..' in p.parts or ':' in str(p):raise ValueError('project-relative primary reference source required')
        p=(root/p).resolve()
        if not p.is_relative_to(root):raise ValueError('primary source escapes project')
        return p
    sources={};audit=[];seen=set()
    for row in records:
        if 'reference_derivation' not in row:continue
        d=row['reference_derivation'];ex=day(row['ex_date']);key=(row['symbol'],ex)
        required={'schema','cash_per_eligible_share','eligible_a_shares','excluded_a_shares','h_shares','issuer_total_shares','primary_sources','reference_terms_literal'}
        if not isinstance(d,dict) or set(d)!=required or d['schema']!='primary_a_share_cash_allocation_v1' or d['reference_terms_literal'] is not False or key in seen or ex>end:raise ValueError('unique explicit research reference derivation required')
        seen.add(key)
        expected=allocated_a_share_cash(d['cash_per_eligible_share'],d['eligible_a_shares'],d['excluded_a_shares'],d['h_shares'],d['issuer_total_shares'])
        if not math.isclose(row['reference_cash_per_share'],expected,rel_tol=0,abs_tol=1e-15) or row['expected_cash_per_share']!=float(d['cash_per_eligible_share']) or row['expected_share_multiplier']!=1. or row['reference_share_multiplier']!=1.:
            raise ValueError('derived reference differs from primary counts or holder economics')
        declarations=d['primary_sources']
        if not isinstance(declarations,list) or len(declarations)!=2 or {s.get('role') for s in declarations}!={'holder_cash_and_share_classes','excluded_a_share_scope'}:raise ValueError('holder and A-share scope primary sources required')
        visible=[]
        for source in declarations:
            required_source={'role','source_file','source_sha256','source_capture_file','source_capture_sha256','source_url','source_page','announcement_date','observed_at_utc','reviewed'}
            if not required_source.issubset(source) or source['reviewed'] is not True:raise ValueError('reviewed primary reference source required')
            file,capture=map(local,[source['source_file'],source['source_capture_file']])
            if sha256(file)!=source['source_sha256'] or sha256(capture)!=source['source_capture_sha256']:raise ValueError('primary reference source changed')
            with file.open('rb') as f:
                if f.read(5)!=b'%PDF-':raise ValueError('primary reference notice must be PDF')
            pub=day(source['announcement_date']);position=cal.searchsorted(pub,side='right')
            if position>=len(cal) or cal[position]>ex or source['source_url'].split('/')[-2]!=str(pub.date()) or urlparse(source['source_url']).scheme!='https' or urlparse(source['source_url']).hostname!='static.cninfo.com.cn':raise ValueError('primary reference publication clock or official host invalid')
            page=source['source_page']
            if isinstance(page,bool) or not isinstance(page,int) or page<1:raise ValueError('positive primary reference page required')
            observed=utc(source['observed_at_utc'])
            if observed>pd.Timestamp(datetime.now(timezone.utc)) or observed.tz_convert('Asia/Shanghai').tz_localize(None).normalize()<pub:raise ValueError('primary reference observation clock invalid')
            c=json.loads(capture.read_text(encoding='utf8'))
            if c.get('schema')!='original_candidate_notice_capture_v1' or c.get('code')!=row['symbol'].split('.')[-1] or c.get('ex_date')!=str(ex.date()):raise ValueError('primary reference capture identity differs')
            matches=[n for n in c.get('notices',[]) if n['sha256']==source['source_sha256'] and n['url']==source['source_url']]
            if len(matches)!=1 or (capture.parent/matches[0]['file']).resolve()!=file or utc(matches[0]['observed_at_utc'])!=observed:raise ValueError('original primary reference observation must be retained')
            sources[file.relative_to(root).as_posix()]=source['source_sha256'];sources[capture.relative_to(root).as_posix()]=source['source_capture_sha256'];visible.append(cal[position])
        if day(row['available_date'])!=max(visible) or day(row['announcement_date'])!=max(day(s['announcement_date']) for s in declarations):raise ValueError('derived availability must include the latest primary source')
        audit.append(dict(symbol=row['symbol'],ex_date=str(ex.date()),available_date=str(max(visible).date()),reference_cash_per_share=expected,holder_cash_per_share=row['expected_cash_per_share'],reference_terms_literal=False,prices_or_outcomes_used_in_derivation=False))
    for name,digest in sources.items():
        if sha256(root/name)!=digest:raise ValueError('primary reference source changed during verification')
    return dict(schema='verified_primary_reference_derivations_v1',events=len(audit),source_hashes=sources,derivations=audit,actual_holder_economics_changed=False,prices_or_outcomes_used_in_derivation=False,independent_data_used=False,historical_vintages_certified=False)

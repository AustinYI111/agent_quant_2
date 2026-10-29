"""Stage primary-reviewed cash events without editing prior holder terms.

This is not the final raw-quote gate or a confirmation of an investor receipt.
Non-cash share changes and paid rights require their separate account policies.
"""
from pathlib import Path
from datetime import datetime,timezone
import argparse,json,re
import numpy as np
import pandas as pd
from .workspace_paths import WorkspacePaths
from .phase2_protocol import sha256,utc,day
from .disk_market import code_hashes,write_manifest
from .reference_overrides import apply_reference_overrides


def extend(project,work_root,parent_actions,intake,review,calendar,out):
    paths=WorkspacePaths(project,work_root);parent,source,review_file,calfile,target=map(paths.resolve,[parent_actions,intake,review,calendar,out])
    if target.exists() or not target.is_relative_to(paths.work) or any(target.is_relative_to(p) or p.is_relative_to(target) for p in [parent,source,review_file,calfile]):raise ValueError('fresh separate external cash-event stage required')
    original=json.loads((parent/'summary.json').read_text(encoding='utf8'));m=json.loads((source/'intake.json').read_text(encoding='utf8'));r=json.loads(review_file.read_text(encoding='utf8'))
    if m.get('status')!='raw_candidates_classified_primary_review_pending' or m.get('candidates_applied') is not False:raise ValueError('unchanged unadmitted source intake required')
    if r.get('schema')!='current_primary_company_action_review_v2' or r.get('status')!='terms_reviewed_quote_check_pending' or r.get('raw_quote_check_completed') is not False or not r.get('events'):raise ValueError('explicit primary review with pending quote gate required')
    sources={}
    def bind(p,digest=None):
        actual=sha256(p)
        if digest is not None and actual!=digest:raise ValueError('cash-event source changed: '+p.name)
        sources[paths.name(p)]=actual
    expected=original.get('output_hashes',{}).get('actions.csv',original.get('actions_sha256'))
    if not expected:raise ValueError('bound prior event table required')
    bind(parent/'actions.csv',expected);bind(parent/'summary.json');bind(source/'intake.json');bind(review_file);bind(calfile)
    if m['source_hashes'].get(paths.name(parent/'actions.csv'))!=expected:raise ValueError('candidate intake used different prior events')
    for name,digest in m['output_hashes'].items():bind(paths.resolve(paths.name(source)+'/'+name),digest)
    actions=pd.read_csv(parent/'actions.csv',float_precision='round_trip',low_memory=False)
    if not {'symbol','ex_date','available_date','pay_date','cash_per_share','share_multiplier'}.issubset(actions):raise ValueError('complete prior holder terms and clocks required')
    if actions.duplicated(['symbol','ex_date']).any():raise ValueError('ambiguous prior events')
    c=pd.read_csv(calfile,dtype=str);cal=pd.DatetimeIndex(pd.to_datetime(c.loc[c.is_trading_day.eq('1'),'calendar_date']))
    if cal.empty or cal.hasnans or cal.has_duplicates or not cal.is_monotonic_increasing:raise ValueError('ordered known event calendar required')
    queue=pd.read_csv(source/'notice_review_queue.csv',float_precision='round_trip');new=[];overrides=[];audits=[];seen=set()
    for event in r['events']:
        e=dict(event);key=(e['symbol'],str(day(e['ex_date']).date()))
        if key in seen:raise ValueError('duplicate reviewed cash event')
        seen.add(key)
        candidates=queue[queue.symbol.eq(key[0])&pd.to_datetime(queue.ex_date).eq(day(e['ex_date']))]
        if len(candidates)!=1 or candidates.iloc[0].status not in ['new_event_needs_notice','existing_terms_conflict','unresolved_provider_terms_or_dates']:raise ValueError('one explicit candidate required; overlapping plans need separate review')
        if e.get('reviewed') is not True or e.get('bank_receipt_verified') is not False or e.get('original_vintages_verified') is not False:raise ValueError('explicit reviewed yet uncertified cash source required')
        pages=e.get('reviewed_pages',[]);parameters=e.get('parameter_pages',{})
        required={'cash_per_share','share_multiplier','record_date','ex_date','pay_date','reference_cash_per_share'}
        if not pages or any(isinstance(p,bool) or not isinstance(p,int) or p<1 for p in pages) or not required.issubset(parameters) or any(isinstance(p,bool) or not isinstance(p,int) or p<1 for p in parameters.values()) or not set(parameters.values()).issubset(set(pages)):raise ValueError('cash/reference/date parameters require reviewed source pages')
        pdf=paths.resolve(e['source_file']);capture=paths.resolve(e['capture_file']);bind(pdf,e['source_sha256']);bind(capture,e['capture_sha256'])
        with pdf.open('rb') as stream:
            if stream.read(5)!=b'%PDF-':raise ValueError('original cash notice PDF required')
        observed=utc(e['first_observed_at_utc']);reviewed=utc(e['reviewed_at_utc'])
        if observed>reviewed or reviewed>pd.Timestamp.now(tz='UTC'):raise ValueError('reversed or future cash source clocks')
        url=re.fullmatch(r'https://static\.cninfo\.com\.cn/finalpage/(\d{4}-\d{2}-\d{2})/\d+\.PDF',e['source_url'],re.IGNORECASE)
        if url is None or day(url[1])!=day(e['announcement_date']):raise ValueError('primary publication identity differs')
        receipt=json.loads(capture.read_text(encoding='utf8'))
        matches=[n for n in receipt.get('notices',[]) if n.get('url')==e['source_url'] and n.get('sha256')==e['source_sha256'] and utc(n['observed_at_utc'])==observed]
        if receipt.get('code')!=key[0].split('.')[1] or receipt.get('ex_date')!=key[1] or len(matches)!=1 or '实施' not in matches[0].get('title',''):raise ValueError('primary issuer/event/observation anchor differs')
        for field in ['record_date','ex_date','pay_date','announcement_date','available_date']:e[field]=day(e[field])
        ix=cal.get_indexer([e['ex_date']])[0]
        if ix<=0 or e['record_date']!=cal[ix-1] or e['pay_date']<e['ex_date']:raise ValueError('cash registry/ex/payment order invalid')
        published=cal.searchsorted(e['announcement_date'],side='right');observed_day=observed.tz_convert('Asia/Shanghai').tz_localize(None).normalize();known=cal.searchsorted(observed_day,side='left')
        if published>=len(cal) or known>=len(cal) or not max(cal[published],cal[known])<=e['available_date']<=e['ex_date']:raise ValueError('cash event backdates its observation or publication availability')
        for field in ['cash_per_share','share_multiplier','reference_cash_per_share','reference_share_multiplier']:
            e[field]=float(e[field])
            if not np.isfinite(e[field]) or e[field]<0:raise ValueError('finite nonnegative reviewed terms required')
        if e['share_multiplier']!=1. or e['reference_share_multiplier']!=1.:raise ValueError('non-cash share changes require separate settlement review')
        if not isinstance(e.get('differential_distribution'),bool):raise ValueError('explicit cash reference kind required')
        if e['differential_distribution']:
            if e.get('reference_basis')!='issuer_declared_rounded_reference' or e['reference_cash_per_share']>e['cash_per_share']:raise ValueError('differential reference must have an explicit declared basis')
        elif e.get('reference_basis')!='ordinary_cash_distribution_no_share_change' or e['reference_cash_per_share']!=e['cash_per_share']:raise ValueError('ordinary cash reference must equal holder cash')
        existing=actions[actions.symbol.eq(key[0])&pd.to_datetime(actions.ex_date).eq(e['ex_date'])]
        if len(existing):
            old=existing.iloc[0]
            if old.cash_per_share!=e['cash_per_share'] or old.share_multiplier!=1. or day(old.pay_date)!=e['pay_date']:raise ValueError('cannot alter prior holder terms in an extension')
            audits.append(dict(symbol=key[0],ex_date=key[1],status='existing_event_not_rewritten'));continue
        new.append(dict(symbol=e['symbol'],provider_symbol=e['symbol'],payment_provider_symbol=e['symbol'],ex_date=e['ex_date'],available_date=e['available_date'],pay_date=e['pay_date'],
                        cash_per_share=e['cash_per_share'],share_multiplier=1.,source_period=e['source_period'],event_vintage_certified=False,
                        source_notice_url=e['source_url'],source_notice_sha256=e['source_sha256'],source_notice_page=parameters['cash_per_share'],source_payment_page=parameters['pay_date'],
                        event_evidence_type='reviewed_primary_implementation_notice',first_observed_at_utc=observed.isoformat(),independent_payment_confirmation=False))
        overrides.append(dict(symbol=e['symbol'],ex_date=e['ex_date'],expected_cash_per_share=e['cash_per_share'],expected_share_multiplier=1.,
                              reference_cash_per_share=e['reference_cash_per_share'],reference_share_multiplier=1.,announcement_date=e['announcement_date'],available_date=e['available_date'],
                              source_url=e['source_url'],source_sha256=e['source_sha256'],source_page=parameters['reference_cash_per_share'],reviewed=True))
        ex_open=(e['ex_date']+pd.Timedelta(hours=9,minutes=30)).tz_localize('Asia/Shanghai').tz_convert('UTC')
        supplied=candidates.iloc[0].get('provider_cash_per_share',np.nan)
        provider_cash=None if pd.isna(supplied) else float(supplied)
        audits.append(dict(symbol=key[0],ex_date=key[1],status='cash_terms_staged_quote_pending',cash_per_share=e['cash_per_share'],reference_cash_per_share=e['reference_cash_per_share'],
                           provider_cash_per_share=provider_cash,
                           primary_cash_differs_from_provider=None if provider_cash is None else not np.isclose(e['cash_per_share'],provider_cash,rtol=0,atol=1e-12),
                           observed_before_ex_open=observed<ex_open,bank_receipt_verified=False))
    updated=pd.concat([actions,pd.DataFrame(new)],ignore_index=True) if new else actions.copy()
    if overrides:updated=apply_reference_overrides(updated,pd.DataFrame(overrides),cal)
    for field in ['ex_date','available_date','pay_date']:
        updated[field]=pd.to_datetime(updated[field],format='mixed')
    for name in actions:
        before=actions[name].reset_index(drop=True);after=updated.iloc[:len(actions)][name].reset_index(drop=True)
        if name.endswith('date'):before=pd.to_datetime(before,format='mixed');after=pd.to_datetime(after,format='mixed')
        pd.testing.assert_series_equal(before,after,check_dtype=False,check_names=False,check_exact=True)
    for name,digest in sources.items():
        if sha256(paths.resolve(name))!=digest:raise ValueError('cash source changed during staging')
    target.mkdir(parents=True);updated.to_csv(target/'actions.csv',index=False)
    proof=dict(schema='reviewed_cash_extension_v1',status='cash_terms_staged_raw_quote_check_pending',created_at_utc=datetime.now(timezone.utc).isoformat(),events=len(updated),new_events=len(new),
               source_hashes=sources,output_hashes={'actions.csv':sha256(target/'actions.csv')},event_audit=audits,existing_holder_terms_changed=False,raw_quote_check_completed=False,
               prediction_ready=False,bank_receipts_verified=False,original_vintages_verified=False,certified=False,code_hashes=code_hashes(['reviewed_cash_extension.py','reference_overrides.py','workspace_paths.py']),
               limitations=['Cash-only extension; no split/share-credit/paid-rights admission.','Primary notice review is not the completed raw quote gate, original-vintage certification or investor receipt.'])
    write_manifest(target/'summary.json',proof);return proof


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project',default='.')
    for name in ['work-root','parent-actions','intake','review','calendar','out']:parser.add_argument('--'+name,required=True)
    result=extend(**vars(parser.parse_args()));print(json.dumps(dict(status=result['status'],events=result['events'],new_events=result['new_events'],prediction_ready=False)))

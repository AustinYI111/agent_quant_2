"""Complete explicitly anchored partial research cash events from primary notices.

This changes retrospective holder economics, never training labels or quotes.
It is separate from both missing-event additions and reference-only overlays.
"""
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal
from urllib.parse import urlparse
import json
import numpy as np
import pandas as pd
from .phase2_protocol import sha256,utc,day
from .public_data import is_ashare


def complete_research_cash(actions,parent_file,evidence_file,project,calendar,research_end):
    root=Path(project).resolve()
    def local(value):
        p=Path(value)
        if p.is_absolute() or '..' in p.parts or ':' in str(p): raise ValueError('project-relative cash completion paths required')
        p=(root/p).resolve()
        if not p.is_relative_to(root): raise ValueError('cash completion path escapes project')
        return p
    parent,review=map(local,[parent_file,evidence_file])
    config=json.loads(review.read_text(encoding='utf8'))
    if config.get('schema')!='official_research_cash_completion_v1' or config.get('expected_parent_actions_sha256')!=sha256(parent):
        raise ValueError('exact sealed parent cash input required')
    original=pd.read_csv(parent,float_precision='round_trip',low_memory=False)
    for col in ['ex_date','available_date','pay_date']:original[col]=pd.to_datetime(original[col],format='mixed')
    result=actions.copy().reset_index(drop=True)
    if len(result)<len(original) or not set(original).issubset(result): raise ValueError('complete original cash input required')
    pd.testing.assert_frame_equal(original,result.iloc[:len(original)][original.columns],check_dtype=False,check_exact=True)
    if result.duplicated(['symbol','ex_date']).any(): raise ValueError('unique cash event identities required')
    cal=pd.DatetimeIndex(pd.to_datetime(calendar));end=day(research_end)
    if cal.empty or cal.hasnans or cal.has_duplicates or cal.tz is not None or not cal.is_monotonic_increasing or not cal.equals(cal.normalize()) or cal.max()>end:
        raise ValueError('ordered research-only calendar required')
    records=config.get('events',[])
    if not isinstance(records,list) or not records: raise ValueError('reviewed cash completion events required')
    hashes={parent.relative_to(root).as_posix():sha256(parent),review.relative_to(root).as_posix():sha256(review)}
    seen=set();audit=[]
    for row in records:
        required={'symbol','ex_date','announcement_date','available_date','pay_date','cash_per_share','share_multiplier','reference_cash_per_share','payout_scope','components','expected_existing','reviewed','source_url','source_sha256','source_page','payment_source_page','source_file','source_capture_file','source_capture_sha256','observed_at_utc'}
        if not required.issubset(row) or row['reviewed'] is not True or row['payout_scope']!='ordinary_a_share_holders' or not is_ashare(row['symbol']): raise ValueError('explicit ordinary A-share primary cash review required')
        symbol=row['symbol'];ex,pub,visible,pay=map(day,[row['ex_date'],row['announcement_date'],row['available_date'],row['pay_date']])
        key=(symbol,ex)
        if key in seen or ex>end: raise ValueError('unique research-only cash completions required')
        seen.add(key)
        selected=result.index[result.symbol.eq(symbol)&result.ex_date.eq(ex)]
        if len(selected)!=1 or selected[0]>=len(original): raise ValueError('existing partial parent event required')
        i=selected[0];prior=result.loc[i];expected=row['expected_existing']
        anchors={'cash_per_share','share_multiplier','pay_date','available_date','reference_cash_per_share','reference_share_multiplier'}
        if not isinstance(expected,dict) or set(expected)!=anchors: raise ValueError('exact partial-event and reference anchors required')
        for name in anchors:
            actual=prior.get(name,np.nan);want=expected[name]
            if want is None:
                if pd.notna(actual): raise ValueError('unexpected existing reference value')
            elif name in ['pay_date','available_date']:
                if actual!=day(want): raise ValueError('existing cash date differs')
            elif actual!=want: raise ValueError('existing partial cash terms differ')
        components=row['components']
        if not isinstance(components,list) or len(components)<2 or any(not isinstance(c,dict) or set(c)!={'id','cash_per_share'} or not isinstance(c['id'],str) or not c['id'] for c in components): raise ValueError('named primary cash components required')
        if len({c['id'] for c in components})!=len(components): raise ValueError('duplicate cash component')
        amounts=[Decimal(str(c['cash_per_share'])) for c in components]
        total=Decimal(str(row['cash_per_share']));old=Decimal(str(prior.cash_per_share))
        if any(not c.is_finite() or c<=0 for c in amounts) or not total.is_finite() or not old.is_finite() or old<=0 or sum(amounts)!=total or amounts.count(old)!=1 or total<=old:
            raise ValueError('explicit positive component sum and exact partial anchor required')
        if row['share_multiplier']!=1. or prior.share_multiplier!=1. or row['reference_cash_per_share']!=float(total):
            raise ValueError('cash-only ordinary non-differential completion required')
        pos=cal.searchsorted(pub,side='right')
        if pos>=len(cal) or visible not in cal or ex not in cal or not cal[pos]<=visible<=ex or pay!=prior.pay_date or pay<ex or prior.available_date>ex:
            raise ValueError('cash completion publication/ex/payment clocks invalid')
        for name in ['source_page','payment_source_page']:
            v=row[name]
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not np.isfinite(v) or v<1 or v%1: raise ValueError('positive primary source page required')
        source,capture=map(local,[row['source_file'],row['source_capture_file']])
        if sha256(source)!=row['source_sha256'] or sha256(capture)!=row['source_capture_sha256']: raise ValueError('primary cash source changed')
        with source.open('rb') as stream:
            if stream.read(5)!=b'%PDF-':raise ValueError('primary cash source must be PDF')
        if urlparse(row['source_url']).scheme!='https' or urlparse(row['source_url']).hostname!='static.cninfo.com.cn': raise ValueError('official primary cash notice required')
        observed=utc(row['observed_at_utc'])
        if observed>pd.Timestamp(datetime.now(timezone.utc)) or observed.tz_convert('Asia/Shanghai').tz_localize(None).normalize()<pub: raise ValueError('cash observation clock invalid')
        raw=json.loads(capture.read_text(encoding='utf8'))
        if raw.get('schema')!='original_candidate_notice_capture_v1' or raw.get('code')!=symbol.split('.')[-1] or raw.get('ex_date')!=str(ex.date()): raise ValueError('cash completion capture identity differs')
        matching=[n for n in raw.get('notices',[]) if n['sha256']==row['source_sha256'] and n['url']==row['source_url']]
        if len(matching)!=1 or (capture.parent/matching[0]['file']).resolve()!=source or utc(matching[0]['observed_at_utc'])!=observed: raise ValueError('original cash observation must be retained')
        if row['source_url'].split('/')[-2]!=str(pub.date()): raise ValueError('source publication date differs')
        hashes[source.relative_to(root).as_posix()]=row['source_sha256'];hashes[capture.relative_to(root).as_posix()]=row['source_capture_sha256']
        result.at[i,'cash_per_share']=float(total);result.at[i,'reference_cash_per_share']=float(total)
        result.at[i,'available_date']=max(prior.available_date,visible)
        result.at[i,'cash_completion_old_cash_per_share']=float(old)
        result.at[i,'cash_completion_components_json']=json.dumps(components,ensure_ascii=False)
        result.at[i,'cash_completion_source_url']=row['source_url'];result.at[i,'cash_completion_source_sha256']=row['source_sha256']
        result.at[i,'cash_completion_observed_at_utc']=row['observed_at_utc']
        result.at[i,'cash_completion_independent_payment_confirmation']=False
        audit.append(dict(symbol=symbol,ex_date=str(ex.date()),previous_cash_per_share=float(old),completed_cash_per_share=float(total),components=components))
    for name,digest in hashes.items():
        if sha256(root/name)!=digest:raise ValueError('cash completion source changed during construction')
    untouched=[i for i in result.index if (result.at[i,'symbol'],result.at[i,'ex_date']) not in seen]
    pd.testing.assert_frame_equal(actions.reset_index(drop=True).loc[untouched],result.loc[untouched,actions.columns],check_dtype=False,check_exact=True)
    return result,dict(schema='reviewed_research_cash_completion_v1',events=len(audit),source_hashes=hashes,corrections=audit,existing_partial_entitlements_changed=True,unrelated_events_unchanged=True,training_features_or_labels_changed=False,independent_payment_confirmation=False,historical_vintages_certified=False,limitation='Retrospective primary-notice completion; not a reference-only overlay, vendor-absence claim or investor bank receipt.')

from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal
from collections import Counter
import argparse,json,re,math
import pandas as pd
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.workspace_paths import WorkspacePaths

parser=argparse.ArgumentParser(description='Independently verify a captured uncomplicated cash-plan intake and staged primary extension.')
for name in ['project','work-root','source','staged','inputs','review','archive','expected-archive-sha256','out']:parser.add_argument('--'+name,required=True)
args=parser.parse_args();paths=WorkspacePaths(args.project,args.work_root);root=paths.project;work=paths.work
source=paths.resolve(args.source);staged=paths.resolve(args.staged)
target=paths.resolve(args.out)
assert not target.exists() and target.is_relative_to(work) and all(not target.is_relative_to(p) and not p.is_relative_to(target) for p in [source,staged])
m=json.loads((source/'intake.json').read_text(encoding='utf8'));stage=json.loads((staged/'summary.json').read_text(encoding='utf8'))
assert m['status']=='raw_candidates_classified_primary_review_pending' and stage['status']=='cash_terms_staged_raw_quote_check_pending'
for proof,base in [(m,source),(stage,staged)]:
    for name,digest in proof['source_hashes'].items():assert sha256(paths.resolve(name))==digest
    for name,digest in proof['output_hashes'].items():assert sha256(paths.resolve(paths.name(base)+'/'+name))==digest
coverage=pd.read_csv(source/'coverage.csv',float_precision='round_trip');queue=pd.read_csv(source/'notice_review_queue.csv',float_precision='round_trip')
config=json.loads(paths.resolve(args.inputs).read_text(encoding='utf8'))
prior=paths.resolve(config['parent_actions']);old=pd.read_csv(prior/'actions.csv',float_precision='round_trip',low_memory=False)
lookup={(r.symbol,pd.Timestamp(r.ex_date)):r for r in old.itertuples(index=False)}
identity=pd.read_json(paths.resolve(config['code_changes']),convert_dates=False);parents=dict(zip(identity.new_symbol,identity.old_symbol))
def canonical(code):
    seen=set()
    while code in parents:
        assert code not in seen;seen.add(code);code=parents[code]
    return code
def regular(code):return bool(re.fullmatch(r'(?:sh\.(?:600|601|603|605|688)\d{3}|sz\.(?:000|001|002|003|300|301|302)\d{3})',code))
calendar=pd.read_csv(paths.resolve(config['calendar']),dtype=str);cal=pd.DatetimeIndex(pd.to_datetime(calendar.loc[calendar.is_trading_day.eq('1'),'calendar_date']))
raw=json.loads((source/'raw_plans/manifest.json').read_text(encoding='utf8'));counts=Counter();offset=0;queue_keys=[];seen=set();maximum_term_error=0.
for period,info in raw['periods'].items():
    date=pd.Timestamp(period);file=source/'raw_plans'/f'{date.year}Q{date.quarter}'/'records.csv';frame=pd.read_csv(file,float_precision='round_trip');assert len(frame)==info['rows']
    for i,r in enumerate(frame.to_dict('records')):
        got=coverage.iloc[offset];offset+=1
        assert got.source_file==paths.name(file) and got.source_row==i and got.source_period==period and got.source_security==r['SECUCODE']
        assert got.observed_at_utc==info['retrieved_at'] and not got.candidate_applied and not got.payment_date_supplied
        match=re.fullmatch(r'(\d{6})\.(SH|SZ|BJ)',str(r['SECUCODE']));provider=(match[2].lower()+'.'+match[1]) if match else '';symbol=canonical(provider)
        if not regular(symbol):status='excluded_instrument'
        elif r['ASSIGN_PROGRESS']!='实施分配':status='not_implemented'
        else:
            ex=pd.Timestamp(r['EX_DIVIDEND_DATE']);assert pd.notna(ex)
            if not pd.Timestamp(m['window_start'])<=ex<=pd.Timestamp(m['window_end']):status='outside_review_window'
            else:
                assert ex in cal and cal[cal.searchsorted(pd.Timestamp(r['NOTICE_DATE']),side='right')]<=ex
                seen.add((symbol,ex));text=str(r['IMPL_PLAN_PROFILE']).split('(')[0].split('（')[0].replace('转增','转').strip()
                assert re.fullmatch(r'10(?:(?:送|转|派)[0-9]+(?:\.[0-9]+)?(?:股|元)?)+',text)
                literals={k:Decimal(v)/10 for k,v in re.findall(r'(送|转|派)([0-9]+(?:\.[0-9]+)?)',text)}
                amounts={}
                for field,tag in [('PRETAX_BONUS_RMB','派'),('BONUS_RATIO','送'),('IT_RATIO','转')]:
                    value=Decimal(str(r[field]))/10 if pd.notna(r[field]) else Decimal(0)
                    assert abs(value-literals.get(tag,Decimal(0)))<=Decimal('.000001');amounts[tag]=value
                cash=float(amounts['派']);shares=float(1+amounts['送']+amounts['转'])
                term_error=max(abs(got.provider_cash_per_share-cash),abs(got.provider_share_multiplier-shares))
                assert term_error<=1e-14;maximum_term_error=max(maximum_term_error,term_error)
                if (symbol,ex) not in lookup:status='new_event_needs_notice';queue_keys.append((symbol,str(ex.date())))
                else:
                    before=lookup[(symbol,ex)]
                    status='existing_terms_match' if abs(before.cash_per_share-cash)<=1e-12 and abs(before.share_multiplier-shares)<=1e-12 else 'existing_terms_conflict'
                    if status!='existing_terms_match':queue_keys.append((symbol,str(ex.date())))
        assert got.status==status;counts[status]+=1
assert offset==len(coverage)==m['raw_rows'] and dict(counts)==m['status_counts']
assert set(queue_keys)==set(zip(queue.symbol,queue.ex_date)) and len(queue_keys)==len(queue)==m['review_queue_rows']
new=pd.read_csv(staged/'actions.csv',float_precision='round_trip',low_memory=False)
assert len(new)==len(old)+stage['new_events']==stage['events']
for c in old:
    before=old[c];after=new.iloc[:len(old)][c].reset_index(drop=True)
    if c.endswith('date'):before=pd.to_datetime(before,format='mixed');after=pd.to_datetime(after,format='mixed')
    pd.testing.assert_series_equal(before,after,check_dtype=False,check_names=False,check_exact=True)
review=json.loads(paths.resolve(args.review).read_text(encoding='utf8'))
for e in review['events']:
    saved=new[new.symbol.eq(e['symbol'])&new.ex_date.eq(e['ex_date'])];assert len(saved)==1;saved=saved.iloc[0]
    for field in ['cash_per_share','share_multiplier','reference_cash_per_share','reference_share_multiplier']:assert saved[field]==e[field]
    for field in ['available_date','pay_date']:assert saved[field]==e[field]
    assert pd.Timestamp(saved.available_date)>=pd.Timestamp(e['first_observed_at_utc']).tz_convert('Asia/Shanghai').tz_localize(None).normalize()
observed_before_ex_open=all(e.get('observed_before_ex_open',False) for e in stage['event_audit'])
archive=paths.resolve(args.archive)
assert sha256(archive)==args.expected_archive_sha256
result=dict(schema='independent_current_action_intake_audit_v1',status='raw_intake_and_cash_extensions_independently_verified',audited_at_utc=datetime.now(timezone.utc).isoformat(),
            intake_manifest_sha256=sha256(source/'intake.json'),staged_summary_sha256=sha256(staged/'summary.json'),records=len(coverage),status_counts=dict(counts),maximum_provider_term_error=maximum_term_error,
            notice_review_queue=len(queue),reviewed_current_cash_events=len(review['events']),remaining_unreviewed_candidates=len(queue)-len(review['events']),prior_events_preserved_exactly=len(old),staged_events=len(new),
            all_new_sources_observed_before_ex_open=observed_before_ex_open,raw_quote_check_completed=False,prediction_ready=False,prediction_archive_verified_unchanged=True,verification_code_sha256=sha256(Path(__file__)),
            limitations=['Checks this actual four-period capture and its uncomplicated unique cash keys; not a general independent verifier for all malformed or overlapping future inputs.',
                         'Unreviewed candidates remain pending. Rights, terminal events, original vintages and investor bank receipts remain unverified.'])
target.parent.mkdir(parents=True,exist_ok=True)
with target.open('x',encoding='utf8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
print(json.dumps(result,ensure_ascii=False))

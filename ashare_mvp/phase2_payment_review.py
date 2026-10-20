"""Reproduce notice-anchored payment reconciliation without changing sources."""
from pathlib import Path
import json
import pandas as pd
from .phase2_protocol import sha256
from .dividend_reconciliation import reconcile_dividend_sources
from .reviewed_payment_groups import reconcile_reviewed_groups
from .reference_overrides import apply_reference_overrides
from .instrument_scope import classify_instrument_scope


def reconcile_phase2_payments(plans, payments, calendar, review, notices, out, code_changes):
    plans,payments,calendar,review,notices,out,code_changes=map(Path,[plans,payments,calendar,review,notices,out,code_changes])
    if out.exists():raise ValueError('new reviewed payment output required')
    for source in [plans,payments,calendar,review,notices,code_changes]:
        if out.resolve()==source.resolve() or out.resolve() in source.resolve().parents or source.resolve() in out.resolve().parents:
            raise ValueError('reviewed output must be separate from inputs')
    config=json.loads(review.read_text(encoding='utf8'))
    if config.get('schema')!='phase2_official_payment_review_v1':raise ValueError('review schema differs')
    inputs={}
    def capture(path,expected=None):
        digest=sha256(path)
        if expected is not None and digest!=expected:raise ValueError('review source hash differs')
        inputs[path]=digest
    for path in [review,calendar,code_changes,plans/'manifest.json',payments/'summary.json',payments/'actions.csv']:capture(path)
    manifest=json.loads((plans/'manifest.json').read_text(encoding='utf8'))
    if manifest.get('status')!='downloaded_raw':raise ValueError('complete raw plan capture required')
    for period,info in manifest['periods'].items():
        date=pd.Timestamp(period)
        for name,digest in info['hashes'].items():
            if Path(name).name!=name:raise ValueError('raw plan basename required')
            capture(plans/f'{date.year}Q{date.quarter}'/name,digest)
    if not config.get('notices'):raise ValueError('sealed local notice PDFs required')
    known=set()
    for notice in config['notices']:
        if Path(notice['file']).name!=notice['file']:raise ValueError('notice basename required')
        capture(notices/notice['file'],notice['sha256'])
        known.add((notice['url'],notice['sha256']))
    for field in ['standalone','groups','references','instrument_scope']:
        for event in config[field]:
            if (event['source_url'],event['source_sha256']) not in known:raise ValueError('review event missing sealed PDF')
    changes=pd.read_json(code_changes,convert_dates=False)
    initial=reconcile_dividend_sources(plans,payments,calendar,out/'base_reconciliation',reviewed_events=pd.DataFrame(config['standalone']),code_changes=changes)
    actions=pd.read_csv(out/'base_reconciliation/actions.csv')
    coverage=pd.read_csv(out/'base_reconciliation/coverage.csv')
    raw=pd.concat([pd.read_csv(plans/f'{pd.Timestamp(period).year}Q{pd.Timestamp(period).quarter}'/'records.csv').assign(source_period=period) for period in manifest['periods']],ignore_index=True)
    cal=pd.read_csv(calendar,dtype=str)
    dates=cal.loc[cal.is_trading_day.eq('1'),'calendar_date'] if {'calendar_date','is_trading_day'}.issubset(cal) else cal.date
    dates=pd.DatetimeIndex(pd.to_datetime(dates))
    actions,coverage,group_audit=reconcile_reviewed_groups(actions,coverage,raw,config['groups'],dates)
    actions=apply_reference_overrides(actions,pd.DataFrame(config['references']),dates)
    coverage,scope_audit,scope_policy=classify_instrument_scope(coverage,actions,pd.DataFrame(config['instrument_scope']),dates)
    source=pd.read_csv(payments/'actions.csv',parse_dates=['ex_date'])
    from .security_identity import canonical_event_symbols
    source=canonical_event_symbols(source,changes)
    keys=set(zip(actions.symbol,pd.to_datetime(actions.ex_date,format='mixed')))
    extras=source[[key not in keys for key in zip(source.symbol,source.ex_date)]].copy()
    for path,digest in inputs.items():
        if sha256(path)!=digest:raise ValueError('review input changed during processing')
    for name,frame in [('actions',actions),('coverage',coverage),('group_audit',group_audit),('instrument_scope_audit',scope_audit),('unmatched_accepted_payment_events',extras)]:frame.to_csv(out/f'{name}.csv',index=False)
    (out/'review.json').write_bytes(review.read_bytes())
    result=dict(schema='phase2_reviewed_payments_v1',status='reviewed_subset',events=len(actions),status_counts=coverage.status.value_counts().to_dict(),
        unmatched_accepted_payment_events=len(extras),input_hashes={str(path):digest for path,digest in inputs.items()},
        output_hashes={path.name:sha256(path) for path in out.glob('*.csv')},instrument_scope_policy=scope_policy,
        initial_status_counts=initial['status_counts'],certified=False,model_ready=False,
        limitations=config['limitations']+['Unmatched independent events remain explicit and are not silently dropped.','Price-reference continuity still requires raw-quote validation.'])
    (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return result

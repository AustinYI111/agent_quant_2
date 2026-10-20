"""Notice-anchored same-day cash distributions across report periods.

Unreviewed duplicate keys are never summed. All implemented periods at the
security/ex-date must be anchored, as must any existing partial cash event.
"""
from io import BytesIO
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd
from .reviewed_dividends import reviewed_distribution_lookup


def reconcile_reviewed_groups(actions, coverage, raw_plans, evidence, calendar):
    a, c, raw = actions.copy(), coverage.copy(), raw_plans.copy()
    cal = pd.DatetimeIndex(calendar)
    if cal.empty or cal.hasnans or cal.has_duplicates or cal.tz is not None or not cal.is_monotonic_increasing or not cal.equals(cal.normalize()):
        raise ValueError('ordered normalized reviewed payment calendar required')
    if not {'symbol','ex_date','pay_date','available_date','cash_per_share','share_multiplier'}.issubset(a) or not {'security','source_period','status','ex_date','cash_per_share','share_multiplier','available_date'}.issubset(c):
        raise ValueError('payment and coverage schema required')
    if a[['symbol','ex_date']].isna().any().any() or a.assign(ex_date=pd.to_datetime(a.ex_date,format='mixed')).duplicated(['symbol','ex_date']).any():
        raise ValueError('unique existing payment keys required')
    if not isinstance(evidence,list) or not evidence:raise ValueError('explicit reviewed payment groups required')
    seen=set(); audits=[]
    for group in evidence:
        components=group.get('components');symbol=group.get('symbol');ex=pd.Timestamp(group.get('ex_date'))
        if not isinstance(components,list) or not components:raise ValueError('explicit component anchors required')
        key=(symbol,ex)
        if key in seen:raise ValueError('duplicate reviewed payment group')
        seen.add(key)
        periods=[str(pd.Timestamp(r['source_period']).date()) for r in components]
        if len(periods)!=len(set(periods)):raise ValueError('duplicate reviewed report period')
        cash=float(group['cash_per_share']);reference=float(group['reference_cash_per_share'])
        if not np.isfinite([cash,reference]).all() or min(cash,reference)<0:raise ValueError('invalid reviewed total/reference cash')
        if group['share_multiplier']!=1:raise ValueError('cash-only reviewed group required')
        security=symbol.split('.')[1]+'.'+symbol.split('.')[0].upper()
        match=raw[raw.SECUCODE.eq(security)&pd.to_datetime(raw.EX_DIVIDEND_DATE,format='mixed').eq(ex)&raw.ASSIGN_PROGRESS.eq('实施分配')]
        actual_periods=pd.to_datetime(match.source_period).dt.strftime('%Y-%m-%d').tolist()
        if sorted(actual_periods)!=sorted(periods):raise ValueError('all same-day implemented periods must be anchored')
        indices=[];visible=[]
        for component,period in zip(components,periods):
            notice={k:group[k] for k in ['symbol','ex_date','announcement_date','available_date','pay_date','cash_per_share','share_multiplier','source_url','source_sha256','source_page','reviewed']}
            notice.update(source_period=period,expected_provider_cash_per_share=component['expected_cash_per_share'],
                expected_provider_share_multiplier=1.,expected_provider_description=component['expected_description'])
            reviewed_distribution_lookup(pd.DataFrame([notice]),raw,cal)
            selected=c[c.security.eq(security)&pd.to_datetime(c.source_period,format='mixed').eq(pd.Timestamp(period))]
            if len(selected)!=1:raise ValueError('unique original coverage component required')
            row=selected.iloc[0]
            if row.status!=component['expected_status'] or row.status not in ['missing_payment_evidence','matched_payment_evidence']:
                raise ValueError('coverage component status differs')
            if pd.Timestamp(row.ex_date)!=ex or not np.isclose(float(row.cash_per_share),component['expected_cash_per_share'],rtol=0,atol=1e-8) or float(row.share_multiplier)!=1:
                raise ValueError('coverage component terms differ')
            indices.append(selected.index[0]);visible.append(pd.Timestamp(row.available_date))
        if len(components)>1 and not np.isclose(sum(r['expected_cash_per_share'] for r in components),cash,rtol=0,atol=1e-8):
            raise ValueError('component sum differs from reviewed total')
        selected=a[a.symbol.eq(symbol)&pd.to_datetime(a.ex_date,format='mixed').eq(ex)]
        prior=group.get('expected_existing')
        old_cash=None
        if prior is None:
            if not selected.empty:raise ValueError('unexpected existing payment; cannot add total twice')
        else:
            if len(selected)!=1:raise ValueError('reviewed existing partial event missing')
            row=selected.iloc[0]
            if not np.isclose(float(row.cash_per_share),prior['cash_per_share'],rtol=0,atol=1e-12) or float(row.share_multiplier)!=1 or pd.Timestamp(row.pay_date)!=pd.Timestamp(prior['pay_date']) or pd.Timestamp(row.source_period)!=pd.Timestamp(prior['source_period']):
                raise ValueError('existing partial payment anchor differs')
            old_cash=float(row.cash_per_share);visible.append(pd.Timestamp(row.available_date))
        available=max([pd.Timestamp(group['available_date'])]+visible)
        if available>ex:raise ValueError('combined payment not visible before execution')
        event=dict(symbol=symbol,provider_symbol=symbol,payment_provider_symbol=symbol,ex_date=str(ex.date()),
            available_date=str(available.date()),pay_date=group['pay_date'],cash_per_share=cash,share_multiplier=1.,
            source_period=periods[0],source_periods_json=json.dumps(sorted(periods)),event_vintage_certified=False,
            source_notice_url=group['source_url'],source_notice_sha256=group['source_sha256'],source_notice_page=group['source_page'],
            reference_cash_per_share=reference,reference_source_url=group['source_url'],reference_source_sha256=group['source_sha256'],
            reference_source_page=group['reference_source_page'])
        for field in ['reference_source_page','payment_source_page']:
            page=float(group[field])
            if not np.isfinite(page) or page<1 or page%1:raise ValueError('reviewed reference/payment source page required')
        event['source_payment_page']=group['payment_source_page']
        a=a.drop(index=selected.index)
        a=pd.concat([a,pd.DataFrame([event])],ignore_index=True)
        for index in indices:
            c.at[index,'status']='matched_reviewed_notice'
            c.at[index,'reason']='official notice anchors complete same-day total; event ledger records it exactly once'
            c.at[index,'reviewed_combined_cash_per_share']=cash
            c.at[index,'available_date']=str(available.date())
        audits.append(dict(symbol=symbol,ex_date=str(ex.date()),source_periods_json=json.dumps(sorted(periods)),
            previous_cash_per_share=old_cash,cash_per_share=cash,reference_cash_per_share=reference,
            source_url=group['source_url'],source_sha256=group['source_sha256']))
    if a.duplicated(['symbol','ex_date']).any():raise ValueError('reviewed payment output collision')
    return a,c,pd.DataFrame(audits)


def reconcile_reviewed_payment_files(data,market,calendar,evidence,out):
    data,market,target=map(Path,[data,market,out])
    if target.exists() and any(target.iterdir()):raise ValueError('reviewed payment output must be empty')
    if any(target.resolve()==p.resolve() or target.resolve() in p.resolve().parents or p.resolve() in target.resolve().parents for p in [data,market]):
        raise ValueError('reviewed payment output must be separate from sources')
    snapshots={}
    def read(path,label,expected=None):
        raw=Path(path).read_bytes()
        if expected is not None and hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('reviewed payment source hash mismatch: '+label)
        snapshots[label]=raw
        return raw
    meta=json.loads(read(data/'summary.json','parent_summary.json'))
    manifest_raw=read(market/'manifest.json','market_manifest.json',meta['market_manifest_sha256']);manifest=json.loads(manifest_raw)
    if manifest.get('status')!='downloaded_raw':raise ValueError('complete market plan source required')
    calendar_raw=read(calendar,'calendar.csv',meta['input_hashes']['calendar.csv'])
    f=pd.read_csv(BytesIO(calendar_raw),dtype=str);cal=pd.to_datetime(f.loc[f.is_trading_day.eq('1'),'calendar_date'])
    actions=pd.read_csv(BytesIO(read(data/'actions.csv','parent_actions.csv',meta['actions_sha256'])))
    coverage=pd.read_csv(BytesIO(read(data/'coverage.csv','parent_coverage.csv',meta['coverage_sha256'])))
    if len(actions)!=meta['matched_events'] or coverage.status.value_counts().to_dict()!=meta['status_counts']:raise ValueError('parent event counts disagree')
    plans=[]
    for period,info in manifest['periods'].items():
        day=pd.Timestamp(period);folder=market/f'{day.year}Q{day.quarter}'
        if info.get('status')!='ok':raise ValueError('incomplete plan period')
        # Validate every original page and flattened record file.
        for name,digest in info['hashes'].items():
            if Path(name).name!=name:raise ValueError('invalid market plan filename')
            raw=(folder/name).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('market plan hash mismatch')
        frame=pd.read_csv(folder/'records.csv');frame['source_period']=period
        if len(frame)!=info['rows'] or frame.duplicated(['SECUCODE','REPORT_DATE']).any() or not pd.to_datetime(frame.REPORT_DATE).eq(day).all():raise ValueError('market plan keys/count mismatch')
        plans.append(frame)
    notices=json.loads(read(evidence,'reviewed_groups.json'))
    a,c,audit=reconcile_reviewed_groups(actions,coverage,pd.concat(plans,ignore_index=True),notices,cal)
    if 'code_changes_sha256' in meta:read(data/'input_code_changes.json','code_changes.json',meta['code_changes_sha256'])
    target.mkdir(parents=True,exist_ok=True)
    for name,raw in snapshots.items():(target/('input_'+name)).write_bytes(raw)
    a.to_csv(target/'actions.csv',index=False);c.to_csv(target/'coverage.csv',index=False);audit.to_csv(target/'reviewed_group_audit.csv',index=False)
    summary=dict(meta,schema='reviewed_payment_groups_v1',records=len(c),matched_events=len(a),
        status_counts=c.status.value_counts().to_dict(),reviewed_payment_groups=len(notices),certified=False,model_ready=False,
        input_hashes={name:hashlib.sha256(raw).hexdigest() for name,raw in snapshots.items()},
        actions_sha256=hashlib.sha256((target/'actions.csv').read_bytes()).hexdigest(),
        coverage_sha256=hashlib.sha256((target/'coverage.csv').read_bytes()).hexdigest(),
        reviewed_group_audit_sha256=hashlib.sha256((target/'reviewed_group_audit.csv').read_bytes()).hexdigest())
    (target/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
    return summary

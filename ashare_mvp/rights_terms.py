"""Ordinary-share rights terms; offer decisions and later results stay separate."""
from pathlib import Path
from decimal import Decimal,localcontext,ROUND_HALF_UP
import json,re
import pandas as pd
from .disk_market import exchange_dates
from .phase2_protocol import day,utc,sha256
from .public_data import is_ashare


def positive_decimal(value):
    d=Decimal(str(value))
    if not d.is_finite() or d<=0:raise ValueError('positive finite rights amount required')
    return d


def next_session(date,calendar):
    date=day(date);cal=exchange_dates(calendar);i=cal.searchsorted(date,side='right')
    if i>=len(cal):raise ValueError('publication clock exceeds known calendar')
    return cal[i]


def normalize_rights_case(spec,calendar):
    cal=exchange_dates(calendar);e=dict(spec)
    if not e.get('event_id') or not is_ashare(e.get('symbol','')):raise ValueError('identified ordinary A-share rights case required')
    for name in ['record_date','subscription_start','subscription_end','expected_result_date']:
        e[name]=day(e[name])
        if e[name] not in cal:raise ValueError('rights execution dates must be exchange sessions')
    if not e['record_date']<e['subscription_start']<=e['subscription_end']<e['expected_result_date']:raise ValueError('invalid registry/payment/result order')
    e['offer_available_date']=next_session(e['offer_publication_date'],cal)
    if e['offer_available_date']>e['record_date']:raise ValueError('offer not visible by registry close')
    e['expected_result_available_date']=next_session(e['expected_result_date'],cal)
    e['offer_ratio']=positive_decimal(e['offer_ratio']);e['subscription_price']=positive_decimal(e['subscription_price'])
    if e.get('subscription_fee_bps') is not None:
        if isinstance(e['subscription_fee_bps'],bool):raise ValueError('numeric nonnegative declared subscription fee required')
        try:fee=Decimal(str(e['subscription_fee_bps']))
        except Exception as error:raise ValueError('numeric declared subscription fee required') from error
        if not fee.is_finite() or fee<0 or fee>=10000:raise ValueError('nonnegative finite declared subscription fee required')
        e['subscription_fee_bps']=float(fee)
    if e.get('outcome') not in [None,'success','failed','canceled']:raise ValueError('unknown declared rights outcome')
    e['result_available_date']=None if not e.get('result_publication_date') else next_session(e['result_publication_date'],cal)
    if e['outcome'] is not None and e['result_available_date'] is None:raise ValueError('outcome requires its own publication clock')
    e['listing_available_date']=None if not e.get('listing_publication_date') else next_session(e['listing_publication_date'],cal)
    for name in ['ex_date','tradable_from','refund_date']:
        e[name]=None if not e.get(name) else day(e[name])
        if e[name] is not None and e[name] not in cal:raise ValueError('rights settlement date outside original exchange calendar')
    if e['outcome']=='success':
        if e['ex_date'] is None or e['ex_date']<=e['subscription_end']:raise ValueError('successful issue requires actual ex-date after payment window')
        # Exchange ratio is the actual aggregate issue count, NOT the offered
        # subscription ratio available to a fully participating ordinary holder.
        before=e['registered_ordinary_shares'];issued=e['issued_ordinary_shares']
        if any(isinstance(x,bool) or not isinstance(x,int) or x<=0 for x in [before,issued]) or Decimal(issued)>Decimal(before)*e['offer_ratio']:raise ValueError('invalid ordinary-share issue counts')
        eligible=e.get('eligible_ordinary_shares');treasury=e.get('treasury_ordinary_shares')
        if eligible is not None or treasury is not None:
            if any(isinstance(x,bool) or not isinstance(x,int) for x in [eligible,treasury]) or eligible<=0 or treasury<0 or eligible+treasury!=before:raise ValueError('eligible and treasury ordinary shares must reconcile to pre-issue total')
            if Decimal(issued)>Decimal(eligible)*e['offer_ratio']:raise ValueError('issued shares exceed eligible offer capacity')
        if e.get('reference_ratio_basis') not in [None,'issued_shares_over_preissue_total']:raise ValueError('explicit pre-issue capital basis required')
        with localcontext() as ctx:
            ctx.prec=50;e['exchange_ratio']=Decimal(issued)/Decimal(before)
            if e.get('quoted_exchange_ratio_per_10') is not None:
                literal=positive_decimal(e['quoted_exchange_ratio_per_10'])
                if abs(e['exchange_ratio']*10-literal)>Decimal('0.000001'):raise ValueError('literal exchange ratio conflicts with ordinary issue counts')
            elif e.get('reference_ratio_basis')!='issued_shares_over_preissue_total':raise ValueError('unquoted ratio requires declared capital-count basis; never fabricate a source literal')
        if e.get('post_issue_ordinary_shares') is not None and e['post_issue_ordinary_shares']!=before+issued:raise ValueError('ordinary post-issue capital does not reconcile')
        if e.get('aggregate_subscription_cash') is not None and Decimal(str(e['aggregate_subscription_cash']))!=Decimal(issued)*e['subscription_price']:raise ValueError('aggregate subscription cash conflicts with issuer price/counts')
        if e['tradable_from'] is not None and (e['listing_available_date'] is None or e['tradable_from']<max(e['listing_available_date'],e['ex_date'])):raise ValueError('new shares cannot trade before listing notice and listing date')
    else:e['exchange_ratio']=None
    if e['outcome']=='success' and day(e['result_publication_date'])<=e['subscription_end']:raise ValueError('successful result precedes payment completion')
    if e['refund_date'] is not None and e['refund_date']<e['subscription_start']:raise ValueError('refund cannot precede subscription window')
    if e['outcome']=='success' and e['refund_date'] is not None:raise ValueError('successful allocation cannot also refund principal')
    return e


def serialize_case(event):
    return {k:str(v) if isinstance(v,Decimal) else v.strftime('%Y-%m-%d') if isinstance(v,pd.Timestamp) else v for k,v in event.items()}


def rights_reference(previous_close,observed_preclose,event,tick='.01'):
    if event['outcome']!='success':raise ValueError('only successful issues have a rights ex-reference')
    previous=positive_decimal(previous_close);observed=positive_decimal(observed_preclose);precision=positive_decimal(tick)
    if precision!=Decimal('.01'):raise ValueError('fixed one-cent research quote precision required')
    with localcontext() as ctx:
        ctx.prec=50;r=event['exchange_ratio'];expected=(previous+event['subscription_price']*r)/(1+r);rounded=(expected/precision).quantize(Decimal('1'),rounding=ROUND_HALF_UP)*precision
    matches=abs(observed-expected)<=Decimal('0.00000001') or abs(observed-rounded)<=Decimal('0.00000001')
    return dict(matches=bool(matches),expected_unrounded=str(expected),expected_half_up=str(rounded),observed_preclose=str(observed),exact_exchange_ratio=str(r),reference_role='retrospective quote reconciliation only; not an input to offer subscription or earlier ranking')


def load_rights_evidence(config,project,evidence_root,calendar):
    root=Path(project).resolve();evidence=Path(evidence_root).resolve()
    def under(base,name):
        p=Path(name)
        if p.is_absolute() or '..' in p.parts or ':' in str(p):raise ValueError('relative primary-evidence paths required')
        p=(base/p).resolve()
        if not p.is_relative_to(base):raise ValueError('rights evidence escapes root')
        return p
    file=under(root,config);m=json.loads(file.read_text(encoding='utf8'))
    if m.get('schema')!='ordinary_rights_evidence_v1' or m.get('original_vintages_verified') is not False or m.get('result_preopen_timestamp_verified') is not False:raise ValueError('explicit unverified-vintage and unverified pre-open ordinary rights evidence required')
    projects={file.relative_to(root).as_posix():sha256(file)};sources={};events=[];seen=set()
    roles={'offer':'配股发行公告','result':'配股发行结果公告','listing':'配股股份变动及获配股票上市公告书'}
    for record in m.get('events',[]):
        e=normalize_rights_case(record['terms'],calendar)
        if e['event_id'] in seen:raise ValueError('duplicate rights case')
        seen.add(e['event_id'])
        for role,expected_title in roles.items():
            item=record['sources'].get(role)
            if item is None:
                if role=='offer' or role=='result' and e['outcome'] is not None or role=='listing' and e['tradable_from'] is not None:raise ValueError('missing rights source role')
                continue
            if item.get('reviewed') is not True or not item.get('reviewed_pages') or any(isinstance(x,bool) or not isinstance(x,int) or x<1 for x in item['reviewed_pages']):raise ValueError('explicit visually reviewed source pages required')
            if not item.get('parameter_pages') or not set(item['parameter_pages'].values()).issubset(set(item['reviewed_pages'])):raise ValueError('rights parameter pages not among reviewed pages')
            required_pages={'offer':{'record_date','subscription_start','subscription_end','offer_ratio','subscription_price','expected_result_date'},'result':{'outcome','ex_date','registered_ordinary_shares','issued_ordinary_shares','aggregate_subscription_cash'} if e['outcome']=='success' else {'outcome'},'listing':{'tradable_from','post_issue_ordinary_shares'}}[role]
            if role=='offer' and e.get('subscription_fee_bps') is not None:required_pages.add('subscription_fee_bps')
            if role=='result' and e['outcome']=='success' and e.get('eligible_ordinary_shares') is not None:required_pages.update({'eligible_ordinary_shares','treasury_ordinary_shares'})
            if not required_pages.issubset(item['parameter_pages']):raise ValueError('missing rights parameter-to-source page mapping')
            pdf=under(evidence,item['source_file']);capture=under(evidence,item['capture_file'])
            if sha256(pdf)!=item['source_sha256'] or sha256(capture)!=item['capture_sha256']:raise ValueError('rights original PDF/capture changed')
            with pdf.open('rb') as stream:
                if stream.read(5)!=b'%PDF-':raise ValueError('original rights attachment must be PDF')
            observed=utc(item['observed_at_utc']);reviewed=utc(item['reviewed_at_utc'])
            if observed>reviewed or reviewed>pd.Timestamp.now(tz='UTC'):raise ValueError('rights observation/review clock in future or reversed')
            url=item['source_url'];matched=re.fullmatch(r'https://static\.cninfo\.com\.cn/finalpage/(\d{4}-\d{2}-\d{2})/\d+\.PDF',url,re.IGNORECASE)
            publication=record['terms'].get(role+'_publication_date')
            if matched is None or publication is None or day(matched[1])!=day(publication):raise ValueError('primary URL/publication identity differs')
            c=json.loads(capture.read_text(encoding='utf8'))
            if c.get('code')!=e['symbol'].split('.')[1]:raise ValueError('rights capture issuer differs from ordinary issuer')
            notices=[n for n in c.get('notices',[]) if n.get('url')==url and n.get('sha256')==item['source_sha256'] and utc(n['observed_at_utc'])==observed]
            if len(notices)!=1 or expected_title not in notices[0].get('title',''):raise ValueError('ordinary rights source role or observation identity differs')
            sources[pdf.relative_to(evidence).as_posix()]=item['source_sha256'];sources[capture.relative_to(evidence).as_posix()]=item['capture_sha256']
        events.append(e)
    if not events:raise ValueError('nonempty ordinary rights evidence required')
    for symbol in {e['symbol'] for e in events}:
        ordered=sorted((e for e in events if e['symbol']==symbol),key=lambda e:e['record_date'])
        for a,b in zip(ordered,ordered[1:]):
            if a['tradable_from'] is None or a['tradable_from']>=b['record_date']:raise ValueError('overlapping same-issuer rights require separate entitlement handling')
    return events,dict(project_source_hashes=projects,evidence_source_hashes=sources,original_vintages_verified=False,reference_only_not_ranking_or_training=True)

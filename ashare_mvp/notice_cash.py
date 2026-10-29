"""Official cash notices can document events omitted by both vendor tables.

Empty vendor responses are source gaps, never evidence of no distribution.
This path requires exact empty-response receipts and reviewed primary notices.
"""
from pathlib import Path
import argparse,json
import numpy as np
import pandas as pd
from .phase2_protocol import sha256
from .public_data import is_ashare


def append_notice_cash(data,provider,plans,calendar,review,notice_root,out):
    data,provider,plans,calendar,review,notice_root,out=map(Path,[data,provider,plans,calendar,review,notice_root,out])
    if out.exists():raise ValueError('new official notice cash output required')
    for source in [data,provider,plans,calendar,review]:
        if out.resolve()==source.resolve() or out.resolve() in source.resolve().parents or source.resolve() in out.resolve().parents:raise ValueError('notice output must be separate from sources')
    hashes={}
    def seal(path,expected=None):
        digest=sha256(path)
        if expected is not None and digest!=expected:raise ValueError('official cash source hash differs')
        hashes[path]=digest
    for path in [data/'summary.json',provider/'manifest.json',plans/'manifest.json',calendar,review]:seal(path)
    parent=json.loads((data/'summary.json').read_text(encoding='utf8'))
    if parent.get('schema')!='phase2_reference_reviewed_payments_v1':raise ValueError('reference-reviewed event input required')
    seal(data/'actions.csv',parent['output_hashes']['actions.csv'])
    a=pd.read_csv(data/'actions.csv',float_precision='round_trip',low_memory=False)
    a['ex_date']=pd.to_datetime(a.ex_date,format='mixed')
    if a.duplicated(['symbol','ex_date']).any():raise ValueError('unique parent event keys required')
    vendor=json.loads((provider/'manifest.json').read_text(encoding='utf8'))
    if vendor.get('schema')!='cninfo_dividend_history_v1' or vendor.get('status')!='downloaded_raw':raise ValueError('completed provider receipts required')
    plan_meta=json.loads((plans/'manifest.json').read_text(encoding='utf8'))
    if plan_meta.get('schema')!='market_dividend_plans_v1' or plan_meta.get('status')!='downloaded_raw':raise ValueError('complete plan capture required')
    plan_rows=[]
    for period,info in plan_meta['periods'].items():
        if info.get('status')!='ok':raise ValueError('complete plan periods required')
        date=pd.Timestamp(period);folder=plans/f'{date.year}Q{date.quarter}'
        for name,digest in info['hashes'].items():
            if Path(name).name!=name:raise ValueError('plan basename required')
            seal(folder/name,digest)
        frame=pd.read_csv(folder/'records.csv',dtype={'SECUCODE':str})
        if len(frame)!=info['rows']:raise ValueError('plan row count differs')
        plan_rows.append(frame)
    raw_plans=pd.concat(plan_rows,ignore_index=True)
    config=json.loads(review.read_text(encoding='utf8'))
    if config.get('schema')!='notice_cash_for_empty_sources_v1':raise ValueError('explicit official-only cash review required')
    e=pd.DataFrame(config['events'])
    required={'symbol','ex_date','announcement_date','available_date','pay_date','cash_per_share','share_multiplier','payout_scope','source_url','source_sha256','source_page','payment_source_page','reviewed','expected_vendor_json_sha256','expected_vendor_csv_sha256'}
    if e.empty or not required.issubset(e) or not e.reviewed.eq(True).all():raise ValueError('reviewed primary cash fields required')
    if not e.symbol.map(is_ashare).all() or not e.payout_scope.eq('ordinary_a_share_holders').all() or not e.share_multiplier.eq(1.).all():raise ValueError('ordinary A-share cash-only notices required')
    e['cash_per_share']=pd.to_numeric(e.cash_per_share,errors='raise')
    if not np.isfinite(e.cash_per_share).all() or e.cash_per_share.le(0).any():raise ValueError('positive primary cash required')
    for field in ['ex_date','announcement_date','available_date','pay_date']:
        e[field]=pd.to_datetime(e[field],format='mixed')
        if e[field].isna().any() or e[field].dt.tz is not None or not e[field].eq(e[field].dt.normalize()).all():raise ValueError('normalized primary notice clocks required')
    if e.duplicated(['symbol','ex_date']).any():raise ValueError('duplicate primary notices')
    if not e.source_url.astype(str).str.startswith('https://').all() or not e.source_sha256.astype(str).str.fullmatch(r'[0-9a-f]{64}').all():raise ValueError('primary source URL/hash required')
    for field in ['source_page','payment_source_page']:
        pages=pd.to_numeric(e[field],errors='raise')
        if not np.isfinite(pages).all() or pages.lt(1).any() or pages.mod(1).ne(0).any():raise ValueError('primary source pages required')
    known=set()
    for item in config['notices']:
        relative=Path(item['file']);file=(notice_root/relative).resolve()
        if relative.is_absolute() or not file.is_relative_to(notice_root.resolve()) or out.resolve() in file.parents:raise ValueError('primary notice path outside source root')
        seal(file,item['sha256']);known.add((item['url'],item['sha256']))
    cal=pd.read_csv(calendar,dtype=str);dates=cal.loc[cal.is_trading_day.eq('1'),'calendar_date'] if {'calendar_date','is_trading_day'}.issubset(cal) else cal.date
    cal=pd.DatetimeIndex(pd.to_datetime(dates))
    if cal.empty or cal.hasnans or cal.has_duplicates or cal.tz is not None or not cal.is_monotonic_increasing or not cal.equals(cal.normalize()):raise ValueError('normalized exchange calendar required')
    added=[]
    for row in e.itertuples():
        if (row.source_url,row.source_sha256) not in known:raise ValueError('primary event lacks sealed notice')
        if ((a.symbol==row.symbol)&a.ex_date.eq(row.ex_date)).any():raise ValueError('primary cash cannot replace an existing event')
        if row.symbol not in vendor['symbols']:raise ValueError('issuer was not requested from provider')
        info=vendor['securities'][row.symbol]
        if info.get('status')!='ok' or info.get('rows')!=0:raise ValueError('explicit empty vendor receipt required; conflicts need another review path')
        for suffix,expected in [('json',row.expected_vendor_json_sha256),('csv',row.expected_vendor_csv_sha256)]:
            name=row.symbol+'.'+suffix
            if info['hashes'].get(name)!=expected:raise ValueError('empty vendor receipt anchor differs')
            seal(provider/name,expected)
        receipt=json.loads((provider/(row.symbol+'.json')).read_text(encoding='utf8'))
        if receipt.get('resultcode')!=200 or receipt.get('count')!=0 or receipt.get('total')!=0 or receipt.get('records')!=[] or not pd.read_csv(provider/(row.symbol+'.csv')).empty:raise ValueError('provider response is not explicitly empty')
        security=row.symbol.split('.')[1]+'.'+row.symbol.split('.')[0].upper()
        if raw_plans.SECUCODE.eq(security).any():raise ValueError('quarterly source has issuer records; anchored reconciliation required')
        pos=cal.searchsorted(row.announcement_date,side='right')
        if pos>=len(cal) or row.available_date not in cal or row.ex_date not in cal or row.available_date<cal[pos] or row.available_date>row.ex_date or row.pay_date<row.ex_date:raise ValueError('primary notice timing invalid')
        added.append(dict(symbol=row.symbol,provider_symbol=row.symbol,payment_provider_symbol=row.symbol,ex_date=row.ex_date,available_date=row.available_date,pay_date=row.pay_date,cash_per_share=row.cash_per_share,share_multiplier=1.,reference_cash_per_share=row.cash_per_share,reference_share_multiplier=1.,source_period=None,event_vintage_certified=False,event_evidence_type='official_notice_cash_vendor_sources_empty',source_notice_url=row.source_url,source_notice_sha256=row.source_sha256,source_notice_page=row.source_page,source_payment_page=row.payment_source_page,independent_payment_confirmation=False))
    for path,digest in hashes.items():
        if sha256(path)!=digest:raise ValueError('primary source changed during review')
    result=pd.concat([a,pd.DataFrame(added)],ignore_index=True)
    pd.testing.assert_frame_equal(a,result.loc[:len(a)-1,a.columns],check_dtype=False,check_exact=True)
    out.mkdir(parents=True);result.to_csv(out/'actions.csv',index=False);pd.DataFrame(added).to_csv(out/'notice_cash_audit.csv',index=False)
    (out/'review.json').write_bytes(review.read_bytes())
    proof=dict(schema='reviewed_cash_supplemented_payments_v1',status='official_notice_cash_supplemented',events=len(result),supplements=len(added),existing_entitlements_unchanged=True,input_hashes={str(p):v for p,v in hashes.items()},output_hashes={p.name:sha256(p) for p in out.glob('*.csv')},independent_payment_confirmation=False,certified=False,model_ready=False,limitations=['Primary notices document these new events; vendor empty replies are omissions, not confirmation of absence.','Missing quarterly labels remain unknown; no synthetic vendor records are created.','Remaining raw-reference faults, original vintages and execution remain unverified.'])
    (out/'summary.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return proof


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['data','provider','plans','calendar','review','notice-root','out']:p.add_argument('--'+name,required=True)
    r=append_notice_cash(**vars(p.parse_args()));print(json.dumps({k:r[k] for k in ['events','supplements','independent_payment_confirmation','model_ready']},indent=2))


if __name__=='__main__':main()

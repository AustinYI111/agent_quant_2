"""Apply notice-anchored reference terms without changing shareholder assets."""
from pathlib import Path
import argparse,json
import pandas as pd
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.reference_overrides import apply_reference_overrides


def run(data,calendar,review,notice_root,out):
    data,calendar,review,notice_root,out=map(Path,[data,calendar,review,notice_root,out])
    if out.exists():raise ValueError('new reference review output required')
    for source in [data,calendar,review]:
        if out.resolve()==source.resolve() or out.resolve() in source.resolve().parents or source.resolve() in out.resolve().parents:raise ValueError('reference output must be separate from inputs')
    hashes={p:sha256(p) for p in [data/'summary.json',review,calendar]}
    parent=json.loads((data/'summary.json').read_text(encoding='utf8'))
    if parent.get('schema')!='reviewed_cash_supplemented_payments_v1':raise ValueError('cash-supplemented payment source required')
    action_file=data/'actions.csv';hashes[action_file]=sha256(action_file)
    if hashes[action_file]!=parent['output_hashes']['actions.csv']:raise ValueError('payment source changed')
    config=json.loads(review.read_text(encoding='utf8'))
    if config.get('schema')!='phase2_reviewed_reference_overrides_v1':raise ValueError('reference review schema required')
    known=set()
    for notice in config['notices']:
        relative=Path(notice['file']);file=(notice_root/relative).resolve()
        if relative.is_absolute() or not file.is_relative_to(notice_root.resolve()):raise ValueError('notice must remain within source root')
        if out.resolve()==file or out.resolve() in file.parents:raise ValueError('output must not contain a notice source')
        hashes[file]=sha256(file)
        if hashes[file]!=notice['sha256']:raise ValueError('reference PDF changed')
        known.add((notice['url'],notice['sha256']))
    if not config['overrides']:raise ValueError('explicit reviewed references required')
    for item in config['overrides']:
        if (item['source_url'],item['source_sha256']) not in known:raise ValueError('reference lacks sealed notice')
    a=pd.read_csv(action_file,float_precision='round_trip',low_memory=False)
    for field in ['ex_date','available_date']:a[field]=pd.to_datetime(a[field],format='mixed')
    cal=pd.read_csv(calendar,dtype=str)
    dates=cal.loc[cal.is_trading_day.eq('1'),'calendar_date'] if {'calendar_date','is_trading_day'}.issubset(cal) else cal.date
    result=apply_reference_overrides(a,pd.DataFrame(config['overrides']),pd.DatetimeIndex(pd.to_datetime(dates)))
    # Reference maintenance must preserve all economic entitlements and event keys.
    for column in ['symbol','cash_per_share','share_multiplier','pay_date','share_credit_date','stock_market_date']:
        if column in a:pd.testing.assert_series_equal(a[column],result[column],check_dtype=False,check_exact=True)
    pd.testing.assert_series_equal(pd.to_datetime(a.ex_date,format='mixed'),result.ex_date,check_names=False)
    for path,digest in hashes.items():
        if sha256(path)!=digest:raise ValueError('reference input changed during processing')
    out.mkdir(parents=True);result.to_csv(out/'actions.csv',index=False)
    (out/'review.json').write_bytes(review.read_bytes())
    proof=dict(schema='phase2_reference_reviewed_payments_v1',status='reference_subset_reviewed',events=len(result),reference_overrides=len(config['overrides']),
        input_hashes={str(p):digest for p,digest in hashes.items()},output_hashes={'actions.csv':sha256(out/'actions.csv')},
        shareholder_entitlements_unchanged=True,certified=False,model_ready=False,
        limitations=['Explicit reviewed reference terms only; remaining source conflicts and quote faults are not certified.','Full-market raw quote continuity must be rechecked before ranking.'])
    (out/'summary.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return proof


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['data','calendar','review','notice-root','out']:p.add_argument('--'+name,required=True)
    r=run(**vars(p.parse_args()))
    print(json.dumps({name:r[name] for name in ['events','reference_overrides','shareholder_entitlements_unchanged','model_ready']},indent=2))


if __name__=='__main__':main()

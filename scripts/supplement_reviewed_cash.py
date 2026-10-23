"""Apply sealed, reviewed cash supplements to an existing payment audit."""
from pathlib import Path
import argparse
import json
import pandas as pd
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.supplemental_payments import supplement_cash_payments


def run(data,calendar,review,notices,out):
    data,calendar,review,notices,out=map(Path,[data,calendar,review,notices,out])
    if out.exists():raise ValueError('new cash supplement output required')
    for source in [data,calendar,review,notices]:
        if out.resolve()==source.resolve() or out.resolve() in source.resolve().parents or source.resolve() in out.resolve().parents:raise ValueError('supplement output must be separate from inputs')
    config=json.loads(review.read_text(encoding='utf8'))
    if config.get('schema')!='reviewed_cash_supplements_v1':raise ValueError('cash supplement schema differs')
    inputs={path:sha256(path) for path in [data/'summary.json',calendar,review]}
    parent=json.loads((data/'summary.json').read_text(encoding='utf8'))
    if parent.get('schema')!='phase2_reviewed_payments_v1':raise ValueError('reviewed payment source required')
    for name in ['actions.csv','coverage.csv','unmatched_accepted_payment_events.csv']:
        path=data/name;inputs[path]=sha256(path)
        if inputs[path]!=parent['output_hashes'][name]:raise ValueError('reviewed payment input hash differs')
    known=set()
    for item in config['notices']:
        if Path(item['file']).name!=item['file']:raise ValueError('cash notice basename required')
        path=notices/item['file'];inputs[path]=sha256(path)
        if inputs[path]!=item['sha256']:raise ValueError('cash notice hash differs')
        known.add((item['url'],item['sha256']))
    for event in config['events']:
        if (event['source_url'],event['source_sha256']) not in known:raise ValueError('cash supplement missing official PDF')
    cal=pd.read_csv(calendar,dtype=str)
    dates=cal.loc[cal.is_trading_day.eq('1'),'calendar_date'] if {'calendar_date','is_trading_day'}.issubset(cal) else cal.date
    a=pd.read_csv(data/'actions.csv',float_precision='round_trip');u=pd.read_csv(data/'unmatched_accepted_payment_events.csv',float_precision='round_trip')
    a,u,audit=supplement_cash_payments(a,u,pd.DataFrame(config['events']),pd.DatetimeIndex(pd.to_datetime(dates)))
    for path,digest in inputs.items():
        if sha256(path)!=digest:raise ValueError('cash supplement input changed')
    out.mkdir(parents=True)
    for name,frame in [('actions',a),('remaining_unmatched_payments',u),('cash_supplement_audit',audit)]:frame.to_csv(out/f'{name}.csv',index=False)
    (out/'input_review.json').write_bytes(review.read_bytes());(out/'base_coverage.csv').write_bytes((data/'coverage.csv').read_bytes())
    result=dict(schema='reviewed_cash_supplemented_payments_v1',status='reviewed_cash_subset',events=len(a),supplements=len(audit),remaining_unmatched_payments=len(u),
        input_hashes={str(p):v for p,v in inputs.items()},output_hashes={p.name:sha256(p) for p in out.glob('*.csv')},certified=False,model_ready=False,
        limitations=['Quarterly-plan coverage is retained separately and does not count supplemental cash as a matched plan.','Rejected provider events and quote-reference faults remain unresolved; no full-market certification.','Original vintages, investor-specific taxes and account cent rounding remain unverified.'])
    (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['data','calendar','review','notices','out']:p.add_argument('--'+name,required=True)
    r=run(**vars(p.parse_args()))
    print(json.dumps({name:r[name] for name in ['events','supplements','remaining_unmatched_payments','model_ready']},indent=2))


if __name__=='__main__':main()

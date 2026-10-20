"""Portable, candidate-only horizon model inference without retraining."""
from pathlib import Path
import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import hashlib,json,platform,shutil
import numpy as np
import pandas as pd
from .horizon_research import load_horizon_model
from .factors import default_library,technical_panel
from .public_conversion import public_snapshot,load_public_data
from .data import Dataset


def _metadata_digest(metadata):
    return hashlib.sha256(json.dumps(metadata,sort_keys=True).encode()).hexdigest()


def check_bundle(directory):
    root=Path(directory)
    meta=json.loads((root/'metadata.json').read_text(encoding='utf8'))
    policy=json.loads((root/'feature_input_policy.json').read_text(encoding='utf8'))
    training=json.loads((root/'training_dataset_metadata.json').read_text(encoding='utf8'))
    if _metadata_digest(training)!=policy.get('source_dataset_metadata_sha256'):
        raise ValueError('training dataset metadata hash differs')
    if training.get('schema')!='public_ratios_v1' or not isinstance(training.get('synthetic'),bool):
        raise ValueError('explicit public dataset kind required')
    if training.get('price_basis')!=policy.get('price_basis') or training.get('quality_policy')!=policy.get('quality_policy'):
        raise ValueError('training feature input policy differs')
    for package in ['lightgbm','numpy','pandas']:
        if version(package)!=meta.get('dependencies',{}).get(package):
            raise ValueError(package+' runtime differs; restore recorded dependencies')
    expected_python=meta.get('dependencies',{}).get('python','')
    if platform.python_version().split('.')[:2]!=expected_python.split('.')[:2]:
        raise ValueError('Python major/minor differs')
    for name in ['factors.py','public_conversion.py','causal_quality.py']:
        raw=Path(__file__).with_name(name).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()
        if hashlib.sha256(raw).hexdigest()!=policy.get('code_sha256',{}).get(name):
            raise ValueError('feature implementation differs: '+name)
    booster=load_horizon_model(root,meta['horizon']['key'],meta['features'])
    if booster.feature_name()!=meta['features']:
        raise ValueError('booster feature order differs')
    return booster,meta,policy,training


def export_bundle(model_directory,training_dataset,out):
    source,target=Path(model_directory),Path(out)
    if target.exists() and any(target.iterdir()):raise ValueError('new empty bundle output required')
    training=json.loads((Path(training_dataset)/'metadata.json').read_text(encoding='utf8'))
    policy=json.loads((source/'feature_input_policy.json').read_text(encoding='utf8'))
    if _metadata_digest(training)!=policy.get('source_dataset_metadata_sha256'):
        raise ValueError('model was bound to different training dataset metadata')
    # Validate a staged bundle before exposing an apparently usable output.
    import tempfile
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='horizon-bundle-',dir=target.parent) as temporary:
        staging=Path(temporary)
        for name in ['model.txt','metadata.json','feature_input_policy.json']:
            shutil.copyfile(source/name,staging/name)
        (staging/'training_dataset_metadata.json').write_text(json.dumps(training,ensure_ascii=False,indent=2),encoding='utf8')
        _,meta,_,_=check_bundle(staging)
        target.mkdir(parents=True,exist_ok=True)
        for file in staging.iterdir():shutil.copyfile(file,target/file.name)
    return dict(horizon=meta['horizon']['key'],trained_asof=meta['trained_asof'],
        model_sha256=meta['model_sha256'],role='research_candidate',retrained=False,certified=False)


def predict_data(directory,data,out,asof=None,top_k=20,exploratory=False):
    if not exploratory:raise ValueError('explicit exploratory prediction required')
    if isinstance(top_k,bool) or not isinstance(top_k,int) or top_k<1:raise ValueError('positive integer top_k required')
    booster,meta,policy,training=check_bundle(directory)
    for key in ['schema','synthetic','price_basis']:
        if data.metadata.get(key)!=training.get(key):raise ValueError('prediction dataset differs: '+key)
    expected_quality=training.get('quality_policy',{}).get('schema')
    if data.metadata.get('quality_policy',{}).get('schema')!=expected_quality:
        raise ValueError('prediction quality schema differs')
    if data.metadata.get('security_identity_policy')!=training.get('security_identity_policy'):
        raise ValueError('prediction identity policy differs')
    date=pd.Timestamp(asof) if asof is not None else data.calendar[-1]
    if date not in data.calendar or date<pd.Timestamp(meta['trained_asof']):
        raise ValueError('prediction must be observed at or after model creation')
    # Later rows must not affect indicators or contemporaneous normalization.
    current=Dataset(data.prices[data.prices.date<=date].copy(),
        data.fundamentals[data.fundamentals.available_date<=date].copy(),
        data.calendar[data.calendar<=date],data.metadata)
    snap=public_snapshot(current,technical_panel(current),date)
    if len(snap)<top_k:raise ValueError('insufficient eligible candidates')
    values=default_library().compute(snap,policy['oneil'])
    if not set(meta['features']).issubset(values):raise ValueError('prediction features unavailable')
    scores=booster.predict(values[meta['features']])
    if not np.isfinite(scores).all():raise ValueError('nonfinite prediction scores')
    result=pd.DataFrame(dict(date=date,symbol=values.index,score=scores))
    if 'trading_symbol' in snap:result['trading_symbol']=result.symbol.map(snap.trading_symbol)
    result=result.sort_values(['score','symbol'],ascending=[False,True]).head(top_k).reset_index(drop=True)
    result['rank']=np.arange(1,len(result)+1)
    target=Path(out)
    if target.exists() and any(target.iterdir()):raise ValueError('new empty prediction output required')
    target.mkdir(parents=True,exist_ok=True)
    result.to_csv(target/'top20.csv',index=False)
    evidence=dict(schema='horizon_candidate_prediction_v1',created_at_utc=datetime.now(timezone.utc).isoformat(),
        asof=str(date.date()),horizon=meta['horizon'],trained_asof=meta['trained_asof'],features=meta['features'],
        top_k=top_k,model_sha256=meta['model_sha256'],dataset_metadata_sha256=_metadata_digest(data.metadata),
        predictions_sha256=hashlib.sha256((target/'top20.csv').read_bytes()).hexdigest(),retrained=False,
        role='research_candidate',certified=False,model_ready=False,
        limitation='Ranking scores are not expected returns; no claim of profitability or certified source vintages.')
    (target/'prediction.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf8')
    return result,evidence


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    check=sub.add_parser('check');check.add_argument('--model',required=True)
    export=sub.add_parser('export')
    export.add_argument('--model',required=True);export.add_argument('--training-data',required=True);export.add_argument('--out',required=True)
    predict=sub.add_parser('predict')
    for name in ['model','data','out']:predict.add_argument('--'+name,required=True)
    predict.add_argument('--asof');predict.add_argument('--top-k',type=int,default=20)
    predict.add_argument('--exploratory',action='store_true')
    args=parser.parse_args()
    if args.command=='export':result=export_bundle(args.model,args.training_data,args.out)
    elif args.command=='check':
        _,meta,_,_=check_bundle(args.model)
        result=dict(compatible=True,horizon=meta['horizon']['key'],model_sha256=meta['model_sha256'],role='research_candidate')
    else:
        _,result=predict_data(args.model,load_public_data(args.data,args.asof),args.out,args.asof,args.top_k,args.exploratory)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()

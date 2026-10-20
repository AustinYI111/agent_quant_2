"""Run a predeclared real-data experiment from an immutable code snapshot."""
import argparse
from datetime import datetime,timezone
import hashlib,json,shutil,sys
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    for name in ['protocol','data','benchmark','out','code-snapshot']:parser.add_argument('--'+name,required=True)
    args=parser.parse_args();repo=Path(__file__).resolve().parents[1]
    protocol_raw=Path(args.protocol).read_bytes();protocol=json.loads(protocol_raw)
    data=Path(args.data);out=Path(args.out);snapshot=Path(args.code_snapshot)
    if out.exists() and any(out.iterdir()):raise ValueError('new empty experiment output required')
    meta_raw=(data/'metadata.json').read_bytes();meta=json.loads(meta_raw)
    if meta.get('synthetic') is not False or meta.get('quality_policy',{}).get('schema')!='causal_quality_research_v1':raise ValueError('explicit real causal-quality input required')
    expected=protocol['source_metadata_sha256']
    for public,source in [('market_manifest','market_manifest'),('financial_summary','financial_summary'),('event_summary','action_summary'),('master_manifest','master_manifest')]:
        if meta['input_hashes'][source]!=expected[public]:raise ValueError('protocol source version differs: '+public)
    if hashlib.sha256((Path(args.benchmark)/'summary.json').read_bytes()).hexdigest()!=expected['benchmark_summary']:raise ValueError('protocol benchmark version differs')
    if snapshot.exists() and any(snapshot.iterdir()):raise ValueError('new empty code snapshot required')
    snapshot.mkdir(parents=True,exist_ok=True);manifest={}
    for source in (repo/'ashare_mvp').rglob('*.py'):
        relative=source.relative_to(repo);target=snapshot/relative;target.parent.mkdir(parents=True,exist_ok=True)
        raw=source.read_bytes();target.write_bytes(raw);manifest[relative.as_posix()]=hashlib.sha256(raw).hexdigest()
    snapshot_meta=dict(frozen_at_utc=datetime.now(timezone.utc).isoformat(),files=manifest,
        protocol_sha256=hashlib.sha256(protocol_raw).hexdigest(),dataset_metadata_sha256=hashlib.sha256(meta_raw).hexdigest())
    (snapshot/'manifest.json').write_text(json.dumps(snapshot_meta,indent=2),encoding='utf8')
    (snapshot/'protocol.json').write_bytes(protocol_raw)
    sys.path.insert(0,str(snapshot.resolve()))
    from ashare_mvp.horizon_research import run_three_horizon_research,HorizonResearchConfig,load_horizon_model
    from ashare_mvp.causal_quality import IncompleteHorizonBacktests
    cfg=HorizonResearchConfig(**{name:protocol[name] for name in ['top_k','seed','min_train_dates','min_group_size','embargo_sessions','retrain_months','train_months']},candidate_estimators=tuple(protocol['candidate_estimators']))
    failure=None
    try:
        run_three_horizon_research(data,args.benchmark,out,protocol['validation_start'],protocol['test_start'],protocol['test_end'],cfg,
            asof=protocol['test_end'],exploratory=True,commission_bps=protocol['commission_bps'],slippage_bps=protocol['slippage_bps'],
            progress=lambda message:print(message,flush=True))
    except IncompleteHorizonBacktests as exc:
        failure=[{key:row[key] for key in ['horizon','symbol','date','reason']} for row in exc.errors]
        print(json.dumps(dict(status='incomplete_actual_holdings',errors=failure)),flush=True)
        exc.__traceback__=None
    finally:
        if out.exists():
            (out/'protocol.json').write_bytes(protocol_raw)
            (out/'code_snapshot_manifest.json').write_text(json.dumps(snapshot_meta,indent=2),encoding='utf8')
    # Independent restoration check uses only saved models and stored features.
    import numpy as np
    import pandas as pd
    table=out/'models/models.csv'
    if table.exists():
        models=pd.read_csv(table,parse_dates=['fold'])
        folds=set(models.fold);features=json.loads((out/'models/feature_input_policy.json').read_text(encoding='utf8'))['features']
        rows=[]
        for chunk in pd.read_csv(out/'factor_samples.csv',usecols=['date','symbol',*features],parse_dates=['date'],float_precision='round_trip',chunksize=100000):
            rows.append(chunk[chunk.date.isin(folds)])
        x=pd.concat(rows,ignore_index=True);rankparts=[]
        for chunk in pd.read_csv(out/'models/rankings.csv',usecols=['date','symbol','horizon','score'],parse_dates=['date'],float_precision='round_trip',chunksize=100000):
            rankparts.append(chunk[chunk.date.isin(folds)])
        ranks=pd.concat(rankparts,ignore_index=True);count=0;error=0.
        for row in models.itertuples():
            folder=(out/'models'/row.relative_model_path).parent
            current=x[x.date.eq(row.fold)].set_index('symbol').sort_index()
            expected_scores=ranks[ranks.date.eq(row.fold)&ranks.horizon.eq(row.horizon)].set_index('symbol').reindex(current.index).score
            if expected_scores.isna().any():raise ValueError('restoration predictions missing original keys')
            restored=load_horizon_model(folder,row.horizon,features).predict(current[features])
            if not np.allclose(restored,expected_scores,rtol=0,atol=1e-12):raise ValueError('restored real model predictions differ')
            count+=len(current);error=max(error,float(np.max(np.abs(restored-expected_scores))))
        report=dict(models_checked=len(models),predictions_checked=count,max_absolute_error=error,retrained=False)
        (out/'models/restoration_check.json').write_text(json.dumps(report,indent=2),encoding='utf8')
        print(json.dumps(dict(restoration=report)),flush=True)
    if failure is not None:raise SystemExit(2)


if __name__=='__main__':main()

"""Bounded-memory research-only market/box inputs; no outcome fitting."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json,sqlite3
import numpy as np
import pandas as pd
from .phase2_protocol import sha256,load_seal,day
from .streaming_snapshot import iter_seed_bars
from .technical_candidates import market_state_features,technical_candidate_rows,BOX_FEATURES,MARKET_FEATURES


FEATURES=BOX_FEATURES+MARKET_FEATURES
NORMALIZED=BOX_FEATURES+['momentum_63_times_market_on']


def normalize_candidate_snapshots(raw):
    """Original same-date 1/99 winsor/population z-score for stock features.

    Market-common state and volatility retain their levels, never demean to 0.
    All eligibility rows remain, including native missing values.
    """
    if set(raw)!={'date','symbol',*FEATURES}:raise ValueError('only declared dated candidate features accepted; no outcomes')
    result=raw.copy();result['date']=pd.to_datetime(result.date)
    dates=pd.DatetimeIndex(result.date)
    if dates.hasnans or dates.tz is not None or not dates.equals(dates.normalize()) or result.symbol.isna().any() or result.duplicated(['date','symbol']).any():raise ValueError('unique normalized candidate keys required')
    for name in FEATURES:
        result[name]=pd.to_numeric(result[name],errors='raise').astype(float)
        if np.isinf(result[name]).any():raise ValueError('candidate infinity rejected')
    if not result.index_above_ma200.dropna().isin([0.,1.]).all():raise ValueError('known market state must be binary')
    for name in ['index_above_ma200','index_volatility_63']:
        if result.groupby('date',observed=True)[name].nunique(dropna=False).gt(1).any():raise ValueError('market-common feature differs within date')
    # Explicit positions avoid pandas' custom-transform concatenation path for
    # the issuer-major, interleaved-date production file. Never mix dates.
    result=result.reset_index(drop=True)
    positions=result.groupby('date',sort=False,observed=True).indices
    for name in NORMALIZED:
        values=result[name].to_numpy(copy=True)
        for indices in positions.values():
            raw=pd.Series(values[indices]);low,high=raw.quantile([.01,.99])
            clipped=raw.clip(low,high);std=clipped.std(ddof=0)
            values[indices]=((clipped-clipped.mean())/std if std>0 else clipped*0).to_numpy()
        result[name]=values
    return result.sort_values(['date','symbol']).reset_index(drop=True)


def research_symbol_rows(history,states,calendar,signal_dates,research_end):
    if history.date.max()>day(research_end):raise ValueError('candidate source contains independent/future bars')
    rows=technical_candidate_rows(history,states,calendar)
    selected=rows.loc[rows.date.isin(signal_dates),['date','symbol']+FEATURES]
    if len(selected)!=len(signal_dates) or selected.duplicated(['date','symbol']).any():raise ValueError('candidate source does not match original eligible signal keys')
    return rows,selected


def build_research_candidates(project,dataset,experiment,benchmark,seal_directory,out):
    root=Path(project).resolve()
    def local(value):
        p=Path(value)
        if p.is_absolute() or '..' in p.parts or ':' in str(p):raise ValueError('project-relative candidate paths required')
        result=(root/p).resolve()
        if not result.is_relative_to(root):raise ValueError('candidate path escapes project')
        return result
    data,old,index,sealed,target=map(local,[dataset,experiment,benchmark,seal_directory,out])
    if target.exists():raise ValueError('new research candidate output required')
    for directory in [data,old,index,sealed]:
        if target.is_relative_to(directory) or directory.is_relative_to(target):raise ValueError('candidate output overlaps immutable inputs')
    protocol,seal=load_seal(sealed,root);end=day(protocol['research_end'])
    meta=json.loads((data/'metadata.json').read_text(encoding='utf8'));original=json.loads((old/'code_snapshot_manifest.json').read_text(encoding='utf8'));model=json.loads((old/'models/summary.json').read_text(encoding='utf8'))
    if meta.get('synthetic') is not False or meta.get('missing_policy')!='native_nan' or meta.get('quality_policy',{}).get('schema')!='causal_quality_research_v1':raise ValueError('sealed real causal native-NaN research data required')
    sources={}
    def bind(file,expected=None):
        digest=sha256(file)
        if expected is not None and digest!=expected:raise ValueError('research candidate source changed: '+file.name)
        sources[file.relative_to(root).as_posix()]=digest
    bind(data/'metadata.json',original['dataset_metadata_sha256']);bind(old/'code_snapshot_manifest.json');bind(old/'models/summary.json')
    bind(data/'prices.csv',meta['converted_hashes']['prices.csv']);bind(data/'calendar.csv',meta['converted_hashes']['calendar.csv'])
    bind(old/'factor_samples.csv',model['input_frame_sha256']['samples']);bind(old/'labels.csv',model['input_frame_sha256']['labels'])
    index_meta=json.loads((index/'summary.json').read_text(encoding='utf8'))
    if index_meta.get('return_kind')!='price_index':raise ValueError('explicit price index required')
    bind(index/'summary.json');bind(index/'raw_prices.csv',index_meta['raw_sha256'])
    calendar=pd.DatetimeIndex(pd.to_datetime(pd.read_csv(data/'calendar.csv').date))
    if calendar.max()>end:raise ValueError('research candidate calendar includes independent data')
    benchmark_frame=pd.read_csv(index/'raw_prices.csv',parse_dates=['date'])
    if not pd.DatetimeIndex(benchmark_frame.date).equals(calendar):raise ValueError('research benchmark calendar incomplete')
    states=market_state_features(benchmark_frame,calendar)
    keys=pd.read_csv(old/'factor_samples.csv',usecols=['symbol','date','available_date'],parse_dates=['date','available_date'])
    if keys[['symbol','date','available_date']].isna().any().any() or keys.duplicated(['date','symbol']).any() or keys.date.gt(end).any() or keys.available_date.gt(keys.date).any() or not keys.date.isin(calendar).all():raise ValueError('original research eligibility/time keys invalid')
    dates_by_symbol={symbol:pd.DatetimeIndex(group.date) for symbol,group in keys.groupby('symbol',sort=False)}
    declared={g['id']:g['features'] for g in protocol['candidate_groups']}
    if declared['market_state']!=MARKET_FEATURES or declared['oneil_box']!=BOX_FEATURES:raise ValueError('candidate definitions differ from frozen protocol')
    code_files=['candidate_dataset.py','technical_candidates.py','streaming_snapshot.py','factors.py']
    code_hashes={name:hashlib.sha256((root/'ashare_mvp'/name).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest() for name in code_files}
    target.mkdir(parents=True)
    definition=dict(schema='phase2_candidate_inputs_v1',protocol_sha256=seal['protocol_sha256'],research_end=str(end.date()),base_features=model['features'],groups={'market_state':MARKET_FEATURES,'oneil_box':BOX_FEATURES},preprocessing={'stock_features':'same-date original factor rule:1/99 winsor,population z-score; no label filtering','market_common':'unscaled binary state and index volatility','missing':'native NaN; never drop a candidate by future label availability'},pending_groups={'industry':'historical visible membership not established','combined':'requires dated industry evidence'},source_hashes=sources,code_hashes=code_hashes,model_selection_performed=False,independent_data_used=False)
    (target/'definition.json').write_text(json.dumps(definition,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    proof=dict(schema='phase2_research_candidate_cache_v1',status='building',started_at_utc=datetime.now(timezone.utc).isoformat(),protocol_sha256=seal['protocol_sha256'],issuers=0,bars=0,eligible_feature_rows=0,expected_feature_rows=len(keys),source_hashes=sources,code_hashes=code_hashes,certified=False,model_ready=False,independent_data_used=False,models_trained=0)
    def save():(target/'manifest.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    save();connection=sqlite3.connect(target/'daily_timing.sqlite');connection.execute('CREATE TABLE timing (symbol TEXT,date TEXT,box_entry INTEGER NOT NULL,box_exit INTEGER,box_signal_known INTEGER NOT NULL,PRIMARY KEY(symbol,date)) WITHOUT ROWID')
    raw_file=target/'raw_candidate_features.csv';header=True
    try:
        for symbol,history in iter_seed_bars(data/'prices.csv',chunksize=100000):
            rows,selected=research_symbol_rows(history,states,calendar,dates_by_symbol.get(symbol,pd.DatetimeIndex([])),end)
            if not selected.empty:selected.to_csv(raw_file,mode='a',header=header,index=False);header=False
            flags=zip([symbol]*len(rows),rows.date.dt.strftime('%Y-%m-%d').tolist(),rows.box_entry.astype(int).tolist(),[None if pd.isna(x) else int(x) for x in rows.box_exit],rows.box_signal_known.astype(int).tolist())
            connection.executemany('INSERT INTO timing VALUES (?,?,?,?,?)',flags)
            proof['issuers']+=1;proof['bars']+=len(history);proof['eligible_feature_rows']+=len(selected)
            if proof['issuers']%250==0:
                connection.commit();save();print(json.dumps({k:proof[k] for k in ['status','issuers','bars','eligible_feature_rows']}),flush=True)
        connection.commit()
        if connection.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError('daily timing cache integrity failed')
        connection.close();connection=None
        if proof['eligible_feature_rows']!=len(keys):raise ValueError('candidate cache lost original eligibility rows')
        raw=pd.read_csv(raw_file,parse_dates=['date'],dtype={'symbol':'category'},float_precision='round_trip')
        expected=pd.MultiIndex.from_frame(keys[['date','symbol']]);observed=pd.MultiIndex.from_frame(raw[['date','symbol']])
        if len(observed)!=len(expected) or not expected.difference(observed).empty or not observed.difference(expected).empty:raise ValueError('candidate key sets differ from original factors')
        normalized=normalize_candidate_snapshots(raw);normalized.to_csv(target/'candidate_features.csv',index=False);states.to_csv(target/'market_states.csv',index=False)
        for name,digest in sources.items():
            if sha256(root/name)!=digest:raise ValueError('candidate input changed during build')
        for name,digest in code_hashes.items():
            if hashlib.sha256((root/'ashare_mvp'/name).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest()!=digest:raise ValueError('candidate code changed during build')
        proof.update(status='research_candidates_built',completed_at_utc=datetime.now(timezone.utc).isoformat(),signals=int(normalized.date.nunique()),missing_counts={c:int(normalized[c].isna().sum()) for c in FEATURES},definition_sha256=sha256(target/'definition.json'),output_hashes={p.name:sha256(p) for p in target.iterdir() if p.name!='manifest.json'})
        save();print(json.dumps({k:proof[k] for k in ['status','issuers','bars','eligible_feature_rows','signals']}),flush=True);return proof
    except BaseException as error:proof.update(status='failed',error=str(error));save();raise
    finally:
        if connection is not None:connection.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',default='.')
    for name in ['dataset','experiment','benchmark','seal-directory','out']:p.add_argument('--'+name,required=True)
    build_research_candidates(**vars(p.parse_args()))


if __name__=='__main__':main()

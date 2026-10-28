"""Dated new-buy risk view; original frozen scores and old holdings survive.

Supplier ST flags alone do not cover delisting-period names. This prospective
view additionally rejects current names containing ST or 退, without refitting,
renormalizing or replacing the parent forecast. It is a later operational rule,
not an originally prespecified candidate-model result.
"""
from pathlib import Path
from datetime import datetime,timezone
import json
import numpy as np
import pandas as pd
from .workspace_paths import WorkspacePaths
from .phase2_protocol import sha256,load_seal,utc,day,forecast_role
from .paper_tracking import _verified_batch
from .disk_market import code_hashes,write_manifest


def _names(paths,capture,signal):
    directory=paths.resolve(capture)
    receipt=json.loads((directory/'capture.json').read_text(encoding='utf8'))
    key='stock_all_'+day(signal).strftime('%Y%m%d')
    item=receipt['queries'].get(key,{})
    if item.get('status')!='raw_acquired':raise ValueError('same-signal complete security-name capture required')
    file=directory/(key+'.csv')
    if sha256(file)!=item['sha256']:raise ValueError('security-name source changed')
    observed=utc(item['observed_at_utc'])
    if observed.tz_convert('Asia/Shanghai').tz_localize(None).normalize()!=day(signal):raise ValueError('security-name observation must be on the signal date')
    names=pd.read_csv(file,dtype=str)
    if len(names)!=item['rows'] or names.code.isna().any() or names.code.duplicated().any():raise ValueError('unique complete security names required')
    return names.set_index('code').code_name,observed


def _filter(scores,names):
    if not scores['rank'].tolist()==list(range(1,len(scores)+1)):raise ValueError('complete original ranked pool required')
    current=names.reindex(scores.trading_symbol)
    if current.isna().any() or current.str.strip().eq('').any():raise ValueError('unknown new-buy security name')
    frame=scores.rename(columns={'rank':'original_rank'}).copy()
    frame['code_name']=current.to_numpy()
    blocked=frame.code_name.str.contains('ST|退',case=False,regex=True)
    rejected=frame[blocked].copy();rejected['reason']='current_ST_or_delisting_name'
    selected=frame[~blocked].copy();selected['rank']=np.arange(1,len(selected)+1)
    if len(selected)<20:raise ValueError('fewer than20 candidates after new-buy risk gate')
    return selected.reset_index(drop=True),rejected.reset_index(drop=True)


def build_risk_selection(project,work_root,snapshot,expected_snapshot_sha256,reference_capture,out):
    paths=WorkspacePaths(project,work_root);source=paths.resolve(snapshot);target=paths.resolve(out)
    if target.exists() or not target.is_relative_to(paths.work) or target.is_relative_to(source) or source.is_relative_to(target):raise ValueError('fresh separate work-drive risk view required')
    status_source=paths.resolve(reference_capture)
    if target.is_relative_to(status_source) or status_source.is_relative_to(target):raise ValueError('risk view must be separate from source capture')
    if sha256(source/'manifest.json')!=expected_snapshot_sha256:raise ValueError('original snapshot changed')
    original=json.loads((source/'manifest.json').read_text(encoding='utf8'))
    if original.get('status')!='research_snapshot_saved' or original.get('model_selection_performed') is not False:raise ValueError('completed unselected frozen snapshot required')
    signal=original['signal_date'];names,observed=_names(paths,reference_capture,signal)
    created=utc(datetime.now(timezone.utc));calendar=pd.to_datetime(pd.read_csv(source/'forecast_calendar.csv').date)
    protocol,seal=load_seal(paths.project/'artifacts/phase2/protocol_20261020',paths.project)
    if observed>created or original['protocol_sha256']!=seal['protocol_sha256']:raise ValueError('unknown or future risk source clock')
    prepared=[]
    for parent in original['receipts']:
        receipt,scores=_verified_batch(source/'predictions.sqlite',parent['batch_id'],protocol,seal,calendar,False)
        if receipt!=parent or utc(parent['created_at_utc'])>created:raise ValueError('invalid parent prediction clock')
        timing=forecast_role(signal,created,max(observed,utc(parent['created_at_utc'])),calendar,protocol)
        if not timing['prospective']:raise ValueError('risk view cannot retrospectively authorize an entry')
        selected,rejected=_filter(scores,names)
        prepared.append((parent,selected,rejected,timing))
    if {r[0]['horizon'] for r in prepared}!=set(protocol['models']):raise ValueError('every frozen horizon required')
    target.mkdir(parents=True)
    proof=dict(schema='frozen_risk_filtered_selection_v1',status='selection_saved',signal_date=signal,created_at_utc=created.isoformat(),
               snapshot=snapshot,parent_manifest_sha256=expected_snapshot_sha256,reference_capture=reference_capture,
               reference_manifest_sha256=sha256(paths.resolve(reference_capture)/'capture.json'),
               latest_status_observed_at_utc=observed.isoformat(),protocol_sha256=seal['protocol_sha256'],
               policy='original ranking order; reject current security names containing ST or退; no rescore; new buys only',
               originally_prespecified=False,old_holdings_deleted=False,original_scores_changed=False,model_selection_performed=False,
               code_hashes=code_hashes(['risk_selection.py','workspace_paths.py']),horizons={},output_hashes={})
    for parent,selected,rejected,timing in prepared:
        h=parent['horizon'];selected.to_csv(target/f'selected_{h}.csv',index=False);selected.head(20).to_csv(target/f'top20_{h}.csv',index=False);rejected.to_csv(target/f'excluded_{h}.csv',index=False)
        proof['horizons'][h]=dict(batch_id=parent['batch_id'],parent_content_sha256=parent['content_sha256'],parent_candidates=parent['candidate_count'],
                                  selected_candidates=len(selected),excluded_candidates=len(rejected),excluded_symbols=list(rejected.trading_symbol),**timing)
    if sha256(source/'manifest.json')!=expected_snapshot_sha256 or sha256(paths.resolve(reference_capture)/'capture.json')!=proof['reference_manifest_sha256']:raise ValueError('risk source changed during selection')
    proof['output_hashes']={p.name:sha256(p) for p in target.glob('*.csv')};write_manifest(target/'manifest.json',proof)
    return proof


def load_risk_selection(directory,expected_manifest_sha256,database,batch_id,project,work_root,calendar):
    paths=WorkspacePaths(project,work_root);folder=paths.resolve(directory)
    if sha256(folder/'manifest.json')!=expected_manifest_sha256:raise ValueError('risk-view manifest changed')
    m=json.loads((folder/'manifest.json').read_text(encoding='utf8'))
    if m.get('schema')!='frozen_risk_filtered_selection_v1' or m.get('status')!='selection_saved' or code_hashes(list(m['code_hashes']))!=m['code_hashes']:raise ValueError('complete unchanged risk view required')
    for name,digest in m['output_hashes'].items():
        if Path(name).name!=name or sha256(folder/name)!=digest:raise ValueError('risk-view selections changed')
    if sha256(paths.resolve(m['snapshot'])/'manifest.json')!=m['parent_manifest_sha256'] or sha256(paths.resolve(m['reference_capture'])/'capture.json')!=m['reference_manifest_sha256']:raise ValueError('risk-view parent or status source changed')
    names,observed=_names(paths,m['reference_capture'],m['signal_date'])
    protocol,seal=load_seal(paths.project/'artifacts/phase2/protocol_20261020',paths.project)
    receipt,scores=_verified_batch(database,batch_id,protocol,seal,calendar,False)
    entry=m['horizons'].get(receipt['horizon'],{})
    if entry.get('batch_id')!=batch_id or entry.get('parent_content_sha256')!=receipt['content_sha256'] or receipt['signal_date']!=m['signal_date']:raise ValueError('risk view does not belong to this parent forecast')
    created=utc(m['created_at_utc'])
    if created>utc(datetime.now(timezone.utc)) or created<max(observed,utc(receipt['created_at_utc'])):raise ValueError('invalid risk-view creation clock')
    timing=forecast_role(receipt['signal_date'],created,max(observed,utc(receipt['created_at_utc'])),calendar,protocol)
    if not timing['prospective'] or any(entry.get(k)!=v for k,v in timing.items()):raise ValueError('risk view was not available before entry')
    selected,rejected=_filter(scores,names)
    if entry.get('selected_candidates')!=len(selected) or entry.get('excluded_symbols')!=list(rejected.trading_symbol):raise ValueError('risk-view candidate membership changed')
    stored=pd.read_csv(folder/f"selected_{receipt['horizon']}.csv",float_precision='round_trip')
    pd.testing.assert_frame_equal(stored,selected,check_exact=True)
    return stored


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',default='.')
    for name in ['work-root','snapshot','reference-capture','out']:p.add_argument('--'+name,required=True)
    p.add_argument('--snapshot-sha256',required=True)
    args=vars(p.parse_args());args['expected_snapshot_sha256']=args.pop('snapshot_sha256')
    result=build_risk_selection(**args)
    print(json.dumps({k:result[k] for k in ['status','signal_date','created_at_utc','horizons']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()

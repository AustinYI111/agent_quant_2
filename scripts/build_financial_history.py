from pathlib import Path
from datetime import datetime,timezone
import argparse,json,pandas as pd
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.financial_snapshot_history import merge_dependency_observations
from ashare_mvp.financial_dependencies import combine_financial_files
parser=argparse.ArgumentParser(description='Retain observed financial dependencies and join only same-report-period versions.')
parser.add_argument('--project',default='.')
parser.add_argument('--snapshot',action='append',required=True)
parser.add_argument('--code-changes',default='config/same_issuer_code_changes.json')
parser.add_argument('--out',required=True)
args=parser.parse_args();root=Path(args.project).resolve()
def local(value):
    p=Path(value)
    if p.is_absolute() or '..' in p.parts or ':' in value:raise ValueError('project-relative financial history paths required')
    result=(root/p).resolve()
    if not result.is_relative_to(root):raise ValueError('financial path escapes project')
    return result
out=local(args.out);sources=[local(value) for value in args.snapshot]
if len(sources)!=len(set(sources)):raise ValueError('duplicate financial snapshot')
if out.exists():raise ValueError('new financial history output required')
for source in sources:
    if out.is_relative_to(source) or source.is_relative_to(out):raise ValueError('financial output must be separate from immutable snapshots')
out.mkdir(parents=True)
for family,file in [('earnings','financial_panel.csv'),('balances','balance_panel.csv')]:
    frames=[];hashes={};partial=False
    for source in sources:
        directory=source/family;summary=json.loads((directory/'summary.json').read_text(encoding='utf8'))
        if sha256(directory/file)!=summary['panel_sha256']:raise ValueError('source dependency panel hash differs')
        partial=partial or bool(summary.get('partial_source',True))
        for item in [directory/file,directory/'summary.json']:hashes[item.relative_to(root).as_posix()]=sha256(item)
        frames.append(pd.read_csv(directory/file,low_memory=False,float_precision='round_trip'))
    merged=merge_dependency_observations(frames);directory=out/family;directory.mkdir();merged.to_csv(directory/file,index=False)
    receipt=dict(schema='observed_dependency_history_v1',built_at_utc=datetime.now(timezone.utc).isoformat(),partial_source=partial,input_hashes=hashes,panel_sha256=sha256(directory/file),records=len(merged),source_records=[len(f) for f in frames],certified=False)
    (directory/'summary.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf8')
    print(json.dumps(dict(stage=family,records=len(merged),source_records=receipt['source_records'])),flush=True)
combined=combine_financial_files(out/'earnings',out/'balances',out/'combined',code_changes=pd.read_json(local(args.code_changes),convert_dates=False))
print(json.dumps({key:combined[key] for key in ['records','symbols','debt_records','panel_sha256']}),flush=True)

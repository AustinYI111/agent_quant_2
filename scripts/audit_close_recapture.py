"""Compare later observations with original raw sources without replacing them."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,json
import pandas as pd
from ashare_mvp.workspace_paths import WorkspacePaths
from ashare_mvp.phase2_protocol import sha256,day,utc


def compare_csv(old,new,keys):
    left=pd.read_csv(old,dtype=str,keep_default_na=False);right=pd.read_csv(new,dtype=str,keep_default_na=False)
    for frame in [left,right]:
        if not set(keys).issubset(frame) or frame[keys].eq('').any().any() or frame.duplicated(keys).any():raise ValueError('unique complete source comparison keys required')
    a=left.set_index(keys);b=right.set_index(keys);shared=a.index.intersection(b.index)
    columns=sorted(set(a.columns)&set(b.columns));changes={c:int(a.loc[shared,c].ne(b.loc[shared,c]).sum()) for c in columns}
    added=b.index.difference(a.index).tolist();removed=a.index.difference(b.index).tolist()
    return dict(original_rows=len(a),new_rows=len(b),shared_rows=len(shared),added_keys=added,removed_keys=removed,
                added_columns=sorted(set(b.columns)-set(a.columns)),removed_columns=sorted(set(a.columns)-set(b.columns)),
                changed_cells={c:n for c,n in changes.items() if n},all_common_values_exact=not any(changes.values()),
                identical=not added and not removed and set(a.columns)==set(b.columns) and not any(changes.values()),
                original_sha256=sha256(old),new_sha256=sha256(new))


def audit(project,work_root,old_market,old_reference,recapture,signal,out):
    paths=WorkspacePaths(project,work_root)
    market,reference,new,target=map(paths.resolve,[old_market,old_reference,recapture,out]);signal=day(signal)
    if target.exists() or not target.is_relative_to(paths.work) or any(target.is_relative_to(p) or p.is_relative_to(target) for p in [market,reference,new]):raise ValueError('fresh separate external audit required')
    m=json.loads((new/'capture.json').read_text(encoding='utf8'))
    if m.get('status')!='raw_acquired' or day(m['signal_date'])!=signal or m.get('prediction_ready') is not False:raise ValueError('complete dated raw recapture required')
    sources={}
    def bind(p,expected=None):
        digest=sha256(p)
        if expected is not None and digest!=expected:raise ValueError('source hash changed: '+p.name)
        sources[paths.name(p)]=digest
    bind(new/'capture.json')
    for name,digest in m['output_hashes'].items():bind(paths.resolve(paths.name(new)+'/'+name),digest)
    original_market=json.loads((market/'latest/manifest.json').read_text(encoding='utf8'))
    original_reference=json.loads((reference/'capture.json').read_text(encoding='utf8'))
    if original_market.get('status')!='downloaded_raw' or original_reference.get('status')!='raw_acquired':raise ValueError('complete original sources required')
    bind(market/'latest/manifest.json');bind(reference/'capture.json')
    raw='daily_'+signal.strftime('%Y%m%d')+'.csv';oldraw=market/'latest'/raw;newraw=new/'market/latest'/raw
    bind(oldraw,original_market['files'][raw]['sha256'])
    comparisons={'market':compare_csv(oldraw,newraw,['date','code'])}
    names={}
    for kind in ['csi300','stock_all','industry']:
        oldkeys=[k for k in original_reference['queries'] if k.startswith(kind+'_')]
        if len(oldkeys)!=1:raise ValueError('one unambiguous original reference per kind required')
        key=oldkeys[0];item=original_reference['queries'][key]
        if item['status']!='raw_acquired':raise ValueError('original reference query incomplete')
        old=reference/(key+'.csv');bind(old,item['sha256']);fresh=new/'reference'/(key+'.csv')
        if not fresh.is_file():raise ValueError('same reference window and identity required')
        comparisons[kind]=compare_csv(old,fresh,['date','code'] if kind=='csi300' else ['code'])
        names[kind]=dict(original_observed_at_utc=item['observed_at_utc'],new_observed_at_utc=json.loads((new/'reference/capture.json').read_text(encoding='utf8'))['queries'][key]['observed_at_utc'])
    original_clock=original_market['files'][raw]['retrieved_at']
    new_clock=json.loads((new/'market/latest/manifest.json').read_text(encoding='utf8'))['files'][raw]['retrieved_at']
    if utc(new_clock)<utc(original_clock):raise ValueError('recapture observation precedes original')
    for name,digest in sources.items():
        if sha256(paths.resolve(name))!=digest:raise ValueError('source changed during comparison')
    proof=dict(schema='independent_raw_close_recapture_comparison_v1',status='comparison_complete',signal_date=str(signal.date()),
               audited_at_utc=datetime.now(timezone.utc).isoformat(),source_hashes=sources,comparisons=comparisons,reference_observations=names,
               original_market_observed_at_utc=original_clock,new_market_observed_at_utc=new_clock,
               any_revision_observed=any(not r['identical'] for r in comparisons.values()),
               raw_market_exact=comparisons['market']['identical'],original_sources_changed=False,original_predictions_changed=False,
               recapture_may_replace_original_observation=False,verification_code_sha256=sha256(Path(__file__)),
               limitations=['Exact raw CSV values and key coverage compared; not certification of financial PIT or corporate-action economics.',
                            'Later supplier observations retain their own dates. A historical query can change; never backdate availability.',
                            'No forecast generated or traded, no parameters selected, no prior source replaced.'])
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x',encoding='utf8') as stream:json.dump(proof,stream,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps(dict(status=proof['status'],raw_market_exact=proof['raw_market_exact'],any_revision_observed=proof['any_revision_observed']),ensure_ascii=False))
    return proof


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['project','work-root','old-market','old-reference','recapture','signal','out']:parser.add_argument('--'+name,required=True)
    audit(**vars(parser.parse_args()))

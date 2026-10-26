from pathlib import Path
from datetime import datetime,timezone
import argparse,json,hashlib
import pandas as pd
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.valuation_payments import supplement_missing_valuation_cash
from ashare_mvp.valuation_cash_completion import complete_research_cash
from ashare_mvp.valuation_overlay import reviewed_valuation_references
from ashare_mvp.primary_reference_derivation import verify_primary_reference_derivations
root=Path.cwd();parent='artifacts/phase2/candidate_ranking_accounts_20261023_v4/effective_valuation_actions.csv'
parser=argparse.ArgumentParser(description='Rebuild the reviewed 2026-10-26 research valuation checkpoint from restored original notices.'); parser.add_argument('--out',required=True)
parser.add_argument('--reference-evidence',default='config/reviewed_industry_valuation_refs_20261026_v1.json')
parser.add_argument('--reference-corroboration')
args=parser.parse_args()
relative=Path(args.out)
if relative.is_absolute() or '..' in relative.parts or ':' in str(relative):raise ValueError('fresh project-relative output required')
out=(root/relative).resolve()
if not out.is_relative_to(root):raise ValueError('output escapes project')
if out.exists():raise ValueError('new valuation-update directory required')
calendar=pd.DatetimeIndex(pd.to_datetime(pd.read_csv(root/'artifacts/market_ratios_causal_quality1/calendar.csv').date))
actions,missing=supplement_missing_valuation_cash(parent,'config/reviewed_industry_missing_cash_20261026_v1.json',root,calendar,'2025-12-31')
actions,completion=complete_research_cash(actions,parent,'config/reviewed_industry_cash_components_20261026_v1.json',root,calendar,'2025-12-31')
ref_path=Path(args.reference_evidence)
if ref_path.is_absolute() or '..' in ref_path.parts or ':' in str(ref_path) or not (root/ref_path).resolve().is_relative_to(root):raise ValueError('project-relative reference evidence required')
records=json.loads((root/ref_path).read_text(encoding='utf8'))
derived=verify_primary_reference_derivations(records,root,calendar,'2025-12-31')
actions,references=reviewed_valuation_references(actions,args.reference_evidence,root,calendar,'2025-12-31')
sources={}
for proof in [missing,completion,references,derived]:sources.update(proof['source_hashes'])
corroboration=None
if args.reference_corroboration:
    p=Path(args.reference_corroboration)
    if p.is_absolute() or '..' in p.parts or ':' in str(p) or not (root/p).resolve().is_relative_to(root):raise ValueError('project-relative corroboration required')
    corroboration=json.loads((root/p).read_text(encoding='utf8'))
    if corroboration.get('schema')!='reviewed_000157_primary_reference_derivation_v1' or corroboration.get('config_sha256')!=sha256(root/ref_path) or corroboration.get('derivation')!=derived or corroboration['corroboration'].get('admitted_as_pre_event_information') is not False:raise ValueError('separate retrospective corroboration contract differs')
    sources[p.as_posix()]=sha256(root/p)
    c=corroboration['corroboration']
    for file,digest in [(c['source_file'],c['source_sha256']),(c['parsed_file'],c['parsed_sha256'])]:
        path=Path(file)
        if path.is_absolute() or '..' in path.parts or ':' in str(path) or not (root/path).resolve().is_relative_to(root):raise ValueError('corroboration source escapes project')
        if sha256(root/path)!=digest:raise ValueError('corroboration source changed')
        sources[path.as_posix()]=digest
elif derived['events']:
    raise ValueError('this checkpoint requires independently bound retrospective corroboration for derived references')
for name,digest in sources.items():
    if sha256(root/name)!=digest:raise ValueError('valuation update source changed')
out.mkdir(parents=True);actions.to_csv(out/'effective_valuation_actions.csv',index=False)
names=['valuation_cash_completion.py','valuation_payments.py','valuation_overlay.py','reference_overrides.py','phase2_protocol.py','primary_reference_derivation.py']
proof=dict(schema='phase2_reviewed_valuation_updates_v1',status='reviewed_valuation_updates_ready',created_at_utc=datetime.now(timezone.utc).isoformat(),parent_file=parent,parent_sha256=sha256(root/parent),source_hashes=sources,code_hashes={n:hashlib.sha256((root/'ashare_mvp'/n).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest() for n in names},output_sha256=sha256(out/'effective_valuation_actions.csv'),rows=len(actions),missing_cash_supplement=missing,cash_completion=completion,reference_overlay=references,reference_derivations=derived,reference_corroboration=corroboration,actual_holder_economics_changed=True,baseline_must_be_replayed=True,training_features_or_labels_changed=False,independent_data_used=False,formal_model_promoted=False,unresolved=[] if corroboration is not None and derived['events']==1 else [dict(symbol='sz.000157',ex_date='2023-07-21',reason='no explicit reference parameters in implementation notice; not inferred from observed prices')])
(out/'manifest.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
print(json.dumps(dict(status=proof['status'],rows=len(actions),missing_events=missing['added_events'],cash_completions=completion['events'],reference_only_events=references['events'],actual_economics_changed=True,baseline_must_be_replayed=True)))

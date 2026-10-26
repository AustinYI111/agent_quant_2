from pathlib import Path
from datetime import datetime,timezone
import argparse,json,hashlib
import pandas as pd
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.valuation_payments import supplement_missing_valuation_cash
from ashare_mvp.valuation_cash_completion import complete_research_cash
from ashare_mvp.valuation_overlay import reviewed_valuation_references
root=Path.cwd();parent='artifacts/phase2/candidate_ranking_accounts_20261023_v4/effective_valuation_actions.csv'
parser=argparse.ArgumentParser(description='Rebuild the reviewed 2026-10-26 research valuation checkpoint from restored original notices.'); parser.add_argument('--out',required=True); args=parser.parse_args()
relative=Path(args.out)
if relative.is_absolute() or '..' in relative.parts or ':' in str(relative):raise ValueError('fresh project-relative output required')
out=(root/relative).resolve()
if not out.is_relative_to(root):raise ValueError('output escapes project')
if out.exists():raise ValueError('new valuation-update directory required')
calendar=pd.DatetimeIndex(pd.to_datetime(pd.read_csv(root/'artifacts/market_ratios_causal_quality1/calendar.csv').date))
actions,missing=supplement_missing_valuation_cash(parent,'config/reviewed_industry_missing_cash_20261026_v1.json',root,calendar,'2025-12-31')
actions,completion=complete_research_cash(actions,parent,'config/reviewed_industry_cash_components_20261026_v1.json',root,calendar,'2025-12-31')
actions,references=reviewed_valuation_references(actions,'config/reviewed_industry_valuation_refs_20261026_v1.json',root,calendar,'2025-12-31')
sources={}
for proof in [missing,completion,references]:sources.update(proof['source_hashes'])
for name,digest in sources.items():
    if sha256(root/name)!=digest:raise ValueError('valuation update source changed')
out.mkdir(parents=True);actions.to_csv(out/'effective_valuation_actions.csv',index=False)
names=['valuation_cash_completion.py','valuation_payments.py','valuation_overlay.py','reference_overrides.py','phase2_protocol.py']
proof=dict(schema='phase2_reviewed_valuation_updates_v1',status='reviewed_valuation_updates_ready',created_at_utc=datetime.now(timezone.utc).isoformat(),parent_file=parent,parent_sha256=sha256(root/parent),source_hashes=sources,code_hashes={n:hashlib.sha256((root/'ashare_mvp'/n).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest() for n in names},output_sha256=sha256(out/'effective_valuation_actions.csv'),rows=len(actions),missing_cash_supplement=missing,cash_completion=completion,reference_overlay=references,actual_holder_economics_changed=True,baseline_must_be_replayed=True,training_features_or_labels_changed=False,independent_data_used=False,formal_model_promoted=False,unresolved=[dict(symbol='sz.000157',ex_date='2023-07-21',reason='no explicit reference parameters in implementation notice; not inferred from observed prices')])
(out/'manifest.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
print(json.dumps(dict(status=proof['status'],rows=len(actions),missing_events=missing['added_events'],cash_completions=completion['events'],reference_only_events=references['events'],actual_economics_changed=True,baseline_must_be_replayed=True)))

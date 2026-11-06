from pathlib import Path
from datetime import datetime,timezone
import hashlib,json
import numpy as np
import pandas as pd
root=Path.cwd();folder=root/'artifacts/phase2/industry_account_attribution_20261026_v2'
audit=root/'artifacts/phase2/full_family_accounting_verification_20261026_v4'
def sha(p):return hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
proof=json.loads((folder/'manifest.json').read_text(encoding='utf8'))
assert proof['status']=='available_complete_accounts_industry_attributed'
assert len(proof['accounts'])==30 and all(r['status']=='complete_descriptive_industry_attribution' for r in proof['accounts'])
for field in ['source_hashes','code_hashes']:
    for name,digest in proof[field].items():assert sha(root/name)==digest
mem=root/'artifacts/phase2/archived_industry_memberships_20261023_v2'
members=pd.read_csv(mem/'memberships.csv',dtype=str)
catalog=pd.read_csv(mem/'catalog.csv',parse_dates=['published_date'])
cal=pd.DatetimeIndex(pd.read_csv(root/'artifacts/market_ratios_causal_quality1/calendar.csv',parse_dates=['date']).date)
rows=[]
for row in proof['accounts']:
    for name,digest in row['output_hashes'].items():assert sha(folder/name)==digest
    relative=Path(row['horizon'])/str(row['n_estimators'])/row['group']
    daily=pd.read_csv(audit/relative/'daily_attribution.csv',parse_dates=['date'],float_precision='round_trip').set_index('date')
    grouped=pd.read_csv(folder/relative/'industry_daily.csv',parse_dates=['date'],float_precision='round_trip')
    labels=pd.read_csv(folder/relative/'historical_labels.csv',parse_dates=['date','classification_date','published_date'],dtype={'release_id':str})
    columns=['price_pnl','gross_dividend_entitlement','commission_cost','slippage_cost','stamp_duty_cost']
    totals=grouped.groupby('date')[columns+['net_pnl','net_return_contribution','allocated_market_reference','price_residual_after_market']].sum(min_count=1)
    errors=[]
    for left,right in [(c,c) for c in columns]+[('net_pnl','nav_change'),('net_return_contribution','net_return'),('allocated_market_reference','unit_beta_market_reference'),('price_residual_after_market','price_residual_after_market')]:
        assert totals.index.equals(daily.index)
        np.testing.assert_allclose(totals[left],daily[right],rtol=0,atol=1e-10,equal_nan=True)
        errors.append(float((totals[left]-daily[right]).abs().max()))
    previous=cal[cal.get_indexer(labels.date)-1]
    assert (cal.get_indexer(labels.date)>0).all()
    assert pd.DatetimeIndex(labels.classification_date).equals(previous)
    # Independent full-catalog selection from publication dates, not producer helpers.
    chosen={}
    for date in labels.classification_date.unique():
        available=catalog[catalog.published_date.lt(date)].sort_values('published_date')
        chosen[pd.Timestamp(date)]=None if available.empty else available.iloc[-1].release_id
    expected_release=labels.classification_date.map(chosen)
    assert labels.release_id.fillna('').equals(expected_release.fillna(''))
    expected=labels[['symbol']].copy();expected['release_id']=expected_release
    expected=expected.merge(members[['symbol','release_id','industry']],on=['symbol','release_id'],how='left',validate='many_to_one')
    assert expected.industry.fillna('__UNKNOWN__').tolist()==labels.industry.tolist()
    rows.append(dict(horizon=row['horizon'],n_estimators=row['n_estimators'],group=row['group'],sessions=len(daily),labels=len(labels),maximum_error=max(errors)))
result=dict(schema='independent_industry_attribution_verification_v1',status='all_thirty_industry_accounts_independently_verified',verified_at_utc=datetime.now(timezone.utc).isoformat(),manifest_sha256=sha(folder/'manifest.json'),accounts=rows,maximum_error=max(r['maximum_error'] for r in rows),verification_code_sha256=sha(Path(__file__)),model_selection_performed=False,formal_model_promoted=False)
target=root/'artifacts/phase2/industry_attribution_verification_20261026_v1.json'
with target.open('x',encoding='utf8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
print(json.dumps(dict(status=result['status'],accounts=len(rows),maximum_error=result['maximum_error'])))

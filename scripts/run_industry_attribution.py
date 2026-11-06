"""Write descriptive historical industry contributions for audited accounts."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json
import pandas as pd
from ashare_mvp.industry_attribution import attribute_industries
from ashare_mvp.phase2_protocol import sha256


def run(project,audit,memberships,calendar,out):
    root=Path(project).resolve()
    def local(value):
        p=Path(value)
        if p.is_absolute() or '..' in p.parts or ':' in str(p):raise ValueError('project-relative paths required')
        p=(root/p).resolve()
        if not p.is_relative_to(root):raise ValueError('path escapes project')
        return p
    src,mem,cal,dest=map(local,[audit,memberships,calendar,out])
    for p in [src,mem,cal]:
        if dest.is_relative_to(p) or p.is_relative_to(dest):raise ValueError('output overlaps input')
    if dest.exists():raise ValueError('fresh output required')
    proof=json.loads((src/'manifest.json').read_text(encoding='utf8'))
    catalog=json.loads((mem/'manifest.json').read_text(encoding='utf8'))
    if proof.get('status')!='all_available_complete_accounts_reconciled':raise ValueError('completed independent audit required')
    if catalog.get('status')!='verified_archived_research_memberships_ready':raise ValueError('verified historical industry catalog required')
    sources={}
    def bind(p,digest=None):
        actual=sha256(p)
        if digest is not None and actual!=digest:raise ValueError('input hash changed: '+p.name)
        sources[p.relative_to(root).as_posix()]=actual
    for p in [src/'manifest.json',mem/'manifest.json',cal]:bind(p)
    for name,digest in catalog['output_hashes'].items():bind(local((mem/name).relative_to(root).as_posix()),digest)
    expected={(h,t,g) for h in ['5d','20d','12m'] for t in [30,60] for g in ['baseline','market_state','oneil_box','industry','combined']}
    keys=[(r['horizon'],r['n_estimators'],r['group']) for r in proof['accounts']]
    if len(keys)!=30 or set(keys)!=expected:raise ValueError('fixed thirty-account audit required')
    for name,digest in proof['verification_code_sha256'].items():
        actual=hashlib.sha256((root/'ashare_mvp'/name).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest()
        if actual!=digest:raise ValueError('audit implementation changed')
    for row in proof['accounts']:
        if row['status']=='complete_account_independently_reconciled':
            for name,digest in row['output_hashes'].items():bind(local((src/name).relative_to(root).as_posix()),digest)
        elif row['status']!='incomplete_no_full_account_attribution':raise ValueError('unknown audit status')
    code_paths=[root/'ashare_mvp'/n for n in ['industry_attribution.py','archived_industry_research.py','public_data.py']]+[Path(__file__).resolve()]
    codes={p.relative_to(root).as_posix():sha256(p) for p in code_paths}
    members=pd.read_csv(mem/'memberships.csv',dtype={'symbol':str})
    releases=pd.read_csv(mem/'catalog.csv')
    sessions=pd.to_datetime(pd.read_csv(cal).date)
    dest.mkdir(parents=True)
    result=dict(schema='historical_industry_account_attribution_v1',status='calculating',started_at_utc=datetime.now(timezone.utc).isoformat(),source_hashes=sources,code_hashes=codes,accounts=[],independent_data_used=False,model_selection_performed=False,formal_model_promoted=False,certified=False,limitations=['Descriptive actual price/dividend/cost contribution, not causal alpha or sector-factor returns.','Classification known at previous open; absent issuers remain UNKNOWN.','Market reference is unit-beta CSI300 price reference allocated by starting invested value.','Archived sources were actually observed in 2026; historical publication dates do not certify original byte vintages.'])
    def save():
        temp=dest/'manifest.next.json'
        temp.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8');temp.replace(dest/'manifest.json')
    save()
    for row in proof['accounts']:
        h,t,g=row['horizon'],row['n_estimators'],row['group']
        if row['status']!='complete_account_independently_reconciled':
            result['accounts'].append(dict(horizon=h,n_estimators=t,group=g,status='incomplete_account_not_attributed'));save();continue
        folder=src/h/str(t)/g
        daily,assets,positions=[pd.read_csv(folder/n,parse_dates=['date'],float_precision='round_trip') for n in ['daily_attribution.csv','asset_attribution.csv','positions.csv']]
        grouped,checks,labels=attribute_industries(daily,assets,positions,members,releases,sessions,'2025-12-31')
        summary=grouped.groupby('industry',as_index=False)[['price_pnl','gross_dividend_entitlement','commission_cost','slippage_cost','stamp_duty_cost','net_pnl','allocated_market_reference','price_residual_after_market']].sum(min_count=1)
        target=dest/h/str(t)/g;target.mkdir(parents=True)
        for value,name in [(grouped,'industry_daily.csv'),(checks,'reconciliation.csv'),(labels,'historical_labels.csv'),(summary,'industry_pnl_summary.csv')]:value.to_csv(target/name,index=False)
        age=(labels.classification_date-labels.published_date).dt.days.dropna()
        record=dict(horizon=h,n_estimators=t,group=g,status='complete_descriptive_industry_attribution',sessions=len(daily),maximum_absolute_reconciliation_error=float(checks.drop(columns='date').abs().max().max()),maximum_publication_age_days=int(age.max()) if len(age) else None,unknown_industry_net_pnl=float(summary.loc[summary.industry.eq('__UNKNOWN__'),'net_pnl'].sum()),output_hashes={p.relative_to(dest).as_posix():sha256(p) for p in target.glob('*.csv')})
        result['accounts'].append(record);save();print(json.dumps(dict(attributed=len(result['accounts']),horizon=h,trees=t,group=g,error=record['maximum_absolute_reconciliation_error'])),flush=True)
    for name,digest in sources.items():
        if sha256(root/name)!=digest:raise ValueError('input changed during attribution')
    for name,digest in codes.items():
        if sha256(root/name)!=digest:raise ValueError('implementation changed during attribution')
    result.update(status='available_complete_accounts_industry_attributed',completed_at_utc=datetime.now(timezone.utc).isoformat());save()
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['project','audit','memberships','calendar','out']:p.add_argument('--'+name,required=True)
    a=p.parse_args();run(a.project,a.audit,a.memberships,a.calendar,a.out)

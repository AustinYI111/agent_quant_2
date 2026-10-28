"""Descriptive industry contributions for the eighteen audited timing accounts."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,json
import pandas as pd
from .workspace_paths import WorkspacePaths
from .phase2_protocol import sha256
from .disk_market import code_hashes,write_manifest
from .industry_attribution import attribute_industries,TERMS

EXPECTED={(h,t,g) for h in ['5d','20d','12m'] for t in [30,60] for g in ['market_state','oneil_box','combined']}
LEDGERS=['daily_attribution.csv','asset_attribution.csv','positions.csv']
CODES=['timing_industry_attribution.py','industry_attribution.py','archived_industry_research.py','public_data.py','workspace_paths.py','phase2_protocol.py','disk_market.py']
VERIFY_CODES={'timing_account_audit.py','timing_verification.py','accounting_attribution.py','candidate_portfolios.py'}


def run(project,work_root,audit,source,memberships,calendar,out):
    paths=WorkspacePaths(project,work_root)
    src,producer,mem,cal,dest=map(paths.resolve,[audit,source,memberships,calendar,out])
    if not dest.is_relative_to(paths.work) or dest.exists() or any(dest.is_relative_to(p) or p.is_relative_to(dest) for p in [src,producer,mem,cal]):
        raise ValueError('fresh external output separate from inputs required')
    proof=json.loads((src/'manifest.json').read_text(encoding='utf8'))
    catalog=json.loads((mem/'manifest.json').read_text(encoding='utf8'))
    if proof.get('status')!='available_complete_timing_accounts_verified' or proof.get('completed_accounts')!=18:
        raise ValueError('eighteen completed independently verified timing accounts required')
    keys=[(r['horizon'],r['n_estimators'],r['group']) for r in proof['accounts']]
    if len(keys)!=18 or set(keys)!=EXPECTED or any(r['status']!='complete_account_and_intents_independently_verified' for r in proof['accounts']):
        raise ValueError('complete fixed eighteen-account family required')
    if catalog.get('status')!='verified_archived_research_memberships_ready':raise ValueError('verified archived industry catalog required')
    sources={}
    def bind(path,digest=None):
        actual=sha256(path)
        if digest is not None and actual!=digest:raise ValueError('input hash changed: '+path.name)
        sources[paths.name(path)]=actual
    bind(src/'manifest.json');bind(producer/'manifest.json',proof['source_manifest_sha256']);bind(mem/'manifest.json');bind(cal)
    origin=json.loads((producer/'manifest.json').read_text(encoding='utf8'))
    if origin.get('schema')!='technical_timing_accounts_v1' or origin.get('status')!='technical_timing_attempts_finished_selection_pending':
        raise ValueError('bound finished original timing source required')
    if set(proof['verification_code_sha256'])!=VERIFY_CODES or code_hashes(list(proof['verification_code_sha256']))!=proof['verification_code_sha256']:
        raise ValueError('independent accounting verifier changed')
    expected_files={f'{h}/{t}/{g}/{n}' for h,t,g in EXPECTED for n in LEDGERS}
    if set(proof['output_hashes'])!=expected_files:raise ValueError('exact independently reconciled ledger hashes required')
    for name,digest in proof['output_hashes'].items():bind(paths.resolve(paths.name(src)+'/'+name),digest)
    if not {'memberships.csv','catalog.csv'}.issubset(catalog['output_hashes']):raise ValueError('bound membership and release tables required')
    for name,digest in catalog['output_hashes'].items():bind(paths.resolve(paths.name(mem)+'/'+name),digest)
    codes=code_hashes(CODES)
    members=pd.read_csv(mem/'memberships.csv',dtype={'symbol':str});releases=pd.read_csv(mem/'catalog.csv')
    sessions=pd.to_datetime(pd.read_csv(cal).date)
    result=dict(schema='timing_historical_industry_attribution_v1',status='calculating',started_at_utc=datetime.now(timezone.utc).isoformat(),
                source_hashes=sources,code_hashes=codes,accounts=[],independent_data_used=False,model_selection_performed=False,
                formal_model_promoted=False,certified=False,limitations=[
                    'Descriptive actual price/dividend/cost contributions; not sector-factor returns, causal selection alpha or timing alpha.',
                    'Industry labels use the latest full historical release visible at the preceding open; absent names remain UNKNOWN.',
                    'Unit-beta CSI300 price reference allocated by starting equity value; cash and receivables are separate.',
                    'Archived documents were observed in 2026; original historical byte vintages and actual fills remain unverified.',
                    'No new model fit, parameter choice, family test or independent-validation observation.'])
    dest.mkdir(parents=True)
    def save():write_manifest(dest/'manifest.json',result)
    save()
    try:
        for row in proof['accounts']:
            h,t,g=row['horizon'],row['n_estimators'],row['group'];folder=src/h/str(t)/g
            daily,assets,positions=[pd.read_csv(folder/n,parse_dates=['date'],float_precision='round_trip') for n in LEDGERS]
            if len(daily)!=row['sessions']:raise ValueError('audited session count changed')
            grouped,checks,labels=attribute_industries(daily,assets,positions,members,releases,sessions,'2025-12-31')
            summary=grouped.groupby('industry',as_index=False)[TERMS+['net_pnl','net_return_contribution','allocated_market_reference','price_residual_after_market']].sum(min_count=1)
            target=dest/h/str(t)/g;target.mkdir(parents=True)
            for value,name in [(grouped,'industry_daily.csv'),(checks,'reconciliation.csv'),(labels,'historical_labels.csv'),(summary,'industry_pnl_summary.csv')]:value.to_csv(target/name,index=False)
            age=(labels.classification_date-labels.published_date).dt.days.dropna()
            record=dict(horizon=h,n_estimators=t,group=g,status='complete_descriptive_industry_attribution',sessions=len(daily),labels=len(labels),
                        maximum_absolute_reconciliation_error=float(checks.drop(columns='date').abs().max().max()),
                        maximum_publication_age_days=int(age.max()) if len(age) else None,
                        unknown_industry_net_pnl=float(summary.loc[summary.industry.eq('__UNKNOWN__'),'net_pnl'].sum()),
                        output_hashes={paths.name(p)[len(paths.name(dest))+1:]:sha256(p) for p in target.glob('*.csv')})
            result['accounts'].append(record);save()
            print(json.dumps(dict(attributed=len(result['accounts']),horizon=h,trees=t,group=g,error=record['maximum_absolute_reconciliation_error'])),flush=True)
        for name,digest in sources.items():
            if sha256(paths.resolve(name))!=digest:raise ValueError('input changed during attribution')
        if code_hashes(CODES)!=codes:raise ValueError('implementation changed during attribution')
        result.update(status='all_eighteen_timing_accounts_industry_attributed',completed_at_utc=datetime.now(timezone.utc).isoformat());save();return result
    except BaseException as error:
        result.update(status='failed',error=str(error),failed_at_utc=datetime.now(timezone.utc).isoformat());save();raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['project','work-root','audit','source','memberships','calendar','out']:parser.add_argument('--'+name,required=True)
    run(**vars(parser.parse_args()))

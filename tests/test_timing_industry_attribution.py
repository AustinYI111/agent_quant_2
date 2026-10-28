import json
import importlib.util
from pathlib import Path
import pandas as pd
import pytest
from ashare_mvp.timing_industry_attribution import run,EXPECTED,VERIFY_CODES,LEDGERS,CODES
from ashare_mvp.disk_market import code_hashes
from ashare_mvp.phase2_protocol import sha256


def fixture(tmp_path):
    project=tmp_path/'project';work=tmp_path/'work';project.mkdir();work.mkdir()
    (project/'ashare_mvp').mkdir()
    code=Path(__file__).resolve().parents[1]/'ashare_mvp'
    for name in CODES:(project/'ashare_mvp'/name).write_bytes((code/name).read_bytes())
    mem=project/'members';mem.mkdir();src=work/'audit';src.mkdir();producer=work/'source';producer.mkdir()
    (producer/'manifest.json').write_text(json.dumps(dict(schema='technical_timing_accounts_v1',status='technical_timing_attempts_finished_selection_pending')))
    cal=pd.bdate_range('2023-01-02','2023-01-10');pd.DataFrame(dict(date=cal)).to_csv(project/'calendar.csv',index=False)
    releases=pd.DataFrame([dict(release_id='old',period_end='2022-12-31',published_date='2023-01-02',first_observed_at_utc='2026-10-20T00:00:00+00:00',source_sha256='a'*64,snapshot_kind='official_archived_industry_release',members_count=1)])
    members=pd.DataFrame([dict(symbol='sh.600001',industry='C13',release_id='old')])
    releases.to_csv(mem/'catalog.csv',index=False);members.to_csv(mem/'memberships.csv',index=False)
    (mem/'manifest.json').write_text(json.dumps(dict(status='verified_archived_research_memberships_ready',output_hashes={n:sha256(mem/n) for n in ['catalog.csv','memberships.csv']})))
    dates=pd.to_datetime(['2023-01-04','2023-01-05','2023-01-06'])
    a=pd.DataFrame(dict(date=dates,symbol=['sh.600001']*3,price_pnl=[0.,.05,-.01],gross_dividend_entitlement=[0.,0.,.02],commission_cost=[.00015,0.,.00003],slippage_cost=[.00025,0.,.00005],stamp_duty_cost=[0.,0.,.00002]))
    d=a.drop(columns='symbol').copy();d['opening_nav']=[1.,.9996,1.0496];d['nav_change']=[-.0004,.05,.0099];d['net_return']=d.nav_change/d.opening_nav;d['unit_beta_market_reference']=[0.,.005,-.011];d['price_residual_after_market']=d.price_pnl-d.unit_beta_market_reference
    p=pd.DataFrame(dict(date=dates,symbol=['sh.600001']*3,market_value=[.5,.55,.54]))
    rows=[];hashes={}
    for h,t,g in sorted(EXPECTED):
        folder=src/h/str(t)/g;folder.mkdir(parents=True)
        for frame,name in zip([d,a,p],LEDGERS):frame.to_csv(folder/name,index=False);hashes[(folder/name).relative_to(src).as_posix()]=sha256(folder/name)
        rows.append(dict(horizon=h,n_estimators=t,group=g,status='complete_account_and_intents_independently_verified',sessions=3))
    proof=dict(status='available_complete_timing_accounts_verified',completed_accounts=18,accounts=rows,output_hashes=hashes,source_manifest_sha256=sha256(producer/'manifest.json'),verification_code_sha256=code_hashes(sorted(VERIFY_CODES)))
    (src/'manifest.json').write_text(json.dumps(proof))
    args=dict(project=str(project),work_root=str(work),audit='@work/audit',source='@work/source',memberships='members',calendar='calendar.csv',out='@work/result')
    return args,proof,src,work


def test_all_eighteen_accounts_use_external_outputs_and_preserve_sources(tmp_path):
    args,proof,src,work=fixture(tmp_path);before=sha256(src/'manifest.json')
    result=run(**args)
    assert result['status']=='all_eighteen_timing_accounts_industry_attributed' and len(result['accounts'])==18
    assert sha256(src/'manifest.json')==before and not result['model_selection_performed']
    assert max(r['maximum_absolute_reconciliation_error'] for r in result['accounts'])<1e-12
    assert (work/'result/5d/30/combined/industry_pnl_summary.csv').is_file()
    assert all(not name.startswith(str(tmp_path)) for name in result['source_hashes'])


@pytest.mark.parametrize('fault',['subset','duplicate','incomplete','unbound_ledger','empty_verifier','changed_csv','changed_producer','project_output','escape'])
def test_incomplete_tampered_or_wrongly_routed_inputs_rejected(tmp_path,fault):
    args,proof,src,work=fixture(tmp_path)
    if fault=='subset':proof['accounts'].pop()
    if fault=='duplicate':proof['accounts'][-1]=proof['accounts'][0]
    if fault=='incomplete':proof['accounts'][0]['status']='incomplete_no_full_verification'
    if fault=='unbound_ledger':proof['output_hashes'].pop(next(iter(proof['output_hashes'])))
    if fault=='empty_verifier':proof['verification_code_sha256']={}
    if fault=='changed_csv':(src/next(iter(proof['output_hashes']))).write_text('different')
    if fault=='changed_producer':(work/'source/manifest.json').write_text('{}')
    if fault=='project_output':args['out']='result'
    if fault=='escape':args['out']='@work/../escape'
    (src/'manifest.json').write_text(json.dumps(proof))
    with pytest.raises(ValueError):run(**args)
    assert not (work/'result').exists()


@pytest.mark.parametrize('fault',[None,'sector_money','exposure','future_label'])
def test_independent_rebuild_detects_wrong_sector_allocation_even_with_updated_hash(tmp_path,fault):
    args,_,src,work=fixture(tmp_path);run(**args)
    folder=work/'result';proof=json.loads((folder/'manifest.json').read_text())
    row=proof['accounts'][0];relative=Path(row['horizon'])/str(row['n_estimators'])/row['group']
    if fault is not None:
        name='historical_labels.csv' if fault=='future_label' else 'industry_daily.csv'
        path=folder/relative/name;frame=pd.read_csv(path)
        if fault=='future_label':frame.loc[0,'classification_date']='2023-01-04'
        if fault=='exposure':frame.loc[0,'starting_equity_weight']+=.1
        if fault=='sector_money':frame.loc[0,'price_pnl']+=.01
        frame.to_csv(path,index=False);row['output_hashes'][path.relative_to(folder).as_posix()]=sha256(path)
        (folder/'manifest.json').write_text(json.dumps(proof))
    script=Path(__file__).resolve().parents[1]/'scripts/verify_timing_industry_attribution.py'
    spec=importlib.util.spec_from_file_location('independent_timing_sectors',script);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    check=dict(project=args['project'],work_root=args['work_root'],attribution='@work/result',audit=args['audit'],memberships=args['memberships'],calendar=args['calendar'],out='@work/verified.json')
    if fault is None:
        result=module.verify(**check)
        assert len(result['accounts'])==18 and result['rebuilt_industry_asset_money_and_starting_exposure']
    else:
        with pytest.raises(AssertionError):module.verify(**check)
        assert not (work/'verified.json').exists()

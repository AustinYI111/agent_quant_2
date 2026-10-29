from copy import deepcopy
import json
import pandas as pd
import pytest
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.ranking_account_audit import verify_checkpoint
from ashare_mvp.ranking_account_audit import audit_ranking_accounts


def checkpoint(tmp_path):
    folder=tmp_path/'5d/30/industry';folder.mkdir(parents=True)
    files={}
    for name in ['daily_attribution.csv','asset_attribution.csv','positions.csv']:
        p=folder/name;p.write_text('persisted complete audit\n',encoding='utf8');files[p.relative_to(tmp_path).as_posix()]=sha256(p)
    row=dict(horizon='5d',n_estimators=30,group='industry',status='complete_account_independently_reconciled',output_hashes=files)
    proof=dict(schema='resumable_full_family_account_audit_v1',status='reconstructing',source_manifest_sha256='sealed-source',verification_code_sha256={'audit.py':'sealed-code'},accounts=[row])
    return proof,{'audit.py':'sealed-code'},{('5d',30,'industry')}


def test_completed_outputs_are_reusable_only_after_all_hashes_match(tmp_path):
    proof,code,keys=checkpoint(tmp_path)
    assert verify_checkpoint(proof,'sealed-source',code,tmp_path,keys)==keys


@pytest.mark.parametrize('fault',['output_changed','missing_output','source_changed','code_changed','duplicate_account','unexpected_path','unknown_state'])
def test_resume_does_not_trust_incomplete_or_changed_saved_audits(tmp_path,fault):
    proof,code,keys=checkpoint(tmp_path)
    if fault=='output_changed':(tmp_path/'5d/30/industry/positions.csv').write_text('different shares',encoding='utf8')
    elif fault=='missing_output':proof['accounts'][0]['output_hashes'].pop('5d/30/industry/positions.csv')
    elif fault=='source_changed':proof['source_manifest_sha256']='different source'
    elif fault=='code_changed':code={'audit.py':'new implementation'}
    elif fault=='duplicate_account':proof['accounts'].append(deepcopy(proof['accounts'][0]))
    elif fault=='unexpected_path':proof['accounts'][0]['output_hashes']['../other.csv']='unknown'
    elif fault=='unknown_state':proof['status']='failed_unreviewed'
    with pytest.raises(ValueError):verify_checkpoint(proof,'sealed-source',code,tmp_path,keys)


def test_real_account_reconstruction_reaches_final_code_check_and_can_resume(tmp_path,monkeypatch):
    for name in ['source','dataset','benchmark','ashare_mvp']:(tmp_path/name).mkdir()
    for name in ['ranking_account_audit.py','accounting_attribution.py','candidate_portfolios.py']:
        (tmp_path/'ashare_mvp'/name).write_text('# fixed fixture code\n',encoding='utf8')
    dates=pd.to_datetime(['2023-01-03','2023-01-04'])
    pd.DataFrame(dict(date=dates,symbol=['sh.600001']*2,open=[10.,11.],close=[10.,11.],preclose=[10.,10.],tradestatus=['1']*2,terminal_date=['',''])).to_csv(tmp_path/'dataset/event_raw_prices.csv',index=False)
    pd.DataFrame(dict(date=dates,code=['sh.000300']*2,open=[100.,101.])).to_csv(tmp_path/'benchmark/raw_prices.csv',index=False)
    pd.DataFrame(columns=['symbol','ex_date','available_date','pay_date','cash_per_share','share_multiplier']).to_csv(tmp_path/'source/effective_valuation_actions.csv',index=False)
    folder=tmp_path/'source/5d/30/industry';folder.mkdir(parents=True)
    pd.DataFrame(dict(date=dates,nav=[.9996,1.0496],cash=[.4996,.4996],dividend_receivable=[0.,0.])).to_csv(folder/'daily_nav.csv',index=False)
    pd.DataFrame([dict(date=dates[0],symbol='sh.600001',status='filled',side='buy',notional=.5,cost=.0004,stamp_duty=0.)]).to_csv(folder/'orders.csv',index=False)
    pd.DataFrame([dict(symbol='sh.600001')]).to_csv(folder/'scheduled_top20.csv',index=False)
    accounts=[dict(horizon=h,n_estimators=t,group=g,status='incomplete_held_valuation') for h in ['5d','20d','12m'] for t in [30,60] for g in ['baseline','market_state','oneil_box','industry','combined']]
    selected=next(r for r in accounts if (r['horizon'],r['n_estimators'],r['group'])==('5d',30,'industry'))
    selected.update(status='complete_calendar_mature_ranking_account',first_signal='2023-01-02',requested_end='2023-01-04')
    source_hashes={n:sha256(tmp_path/n) for n in ['dataset/event_raw_prices.csv','benchmark/raw_prices.csv']}
    outputs={p.relative_to(tmp_path/'source').as_posix():sha256(p) for p in (tmp_path/'source').rglob('*.csv')}
    manifest=dict(status='full_family_ranking_accounts_evaluated_selection_pending',accounts=accounts,source_hashes=source_hashes,code_hashes={},output_hashes=outputs,paired_diagnostics=[],independent_data_used=False,model_selection_performed=False,formal_model_promoted=False)
    (tmp_path/'source/manifest.json').write_text(json.dumps(manifest),encoding='utf8')
    result=audit_ranking_accounts(tmp_path,'source','dataset','benchmark','audit')
    assert result['status']=='all_available_complete_accounts_reconciled' and result['completed_accounts']==1 and len(result['accounts'])==30
    done=next(r for r in result['accounts'] if r['status']=='complete_account_independently_reconciled')
    assert done['max_absolute_reconciliation_error']<1e-12 and done['ending_nav']==1.0496
    monkeypatch.setattr('ashare_mvp.ranking_account_audit.account_quotes',lambda *a:pytest.fail('completed resume must verify stored hashes without replaying quotes'))
    resumed=audit_ranking_accounts(tmp_path,'source','dataset','benchmark','audit',resume=True)
    assert resumed==result

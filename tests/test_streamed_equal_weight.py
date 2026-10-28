import json
from pathlib import Path
import pandas as pd
import pytest
import ashare_mvp.streamed_equal_weight as streamed
import ashare_mvp.eligible_equal_weight as original
from ashare_mvp.disk_market import build_quote_store,build_reference_audit
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.horizon_labels import label_interval,HORIZONS


def fixture(tmp_path,monkeypatch,unresolved):
    cal=pd.bdate_range('2023-01-02','2024-02-01');symbols=['sh.'+str(600000+i) for i in range(20)]
    for folder in ['source','data','experiment/models','benchmark_csi','ashare_mvp']:(tmp_path/folder).mkdir(parents=True)
    for name in ['eligible_equal_weight.py','candidate_portfolios.py','pipeline.py','corporate_actions.py','causal_quality.py','costs.py','public_data.py','valuation_overlay.py','reference_overrides.py','phase2_protocol.py']:
        (tmp_path/'ashare_mvp'/name).write_bytes(Path(original.__file__).with_name(name).read_bytes())
    pd.DataFrame(dict(date=cal)).to_csv(tmp_path/'data/calendar.csv',index=False)
    raw=pd.DataFrame([dict(date=d,symbol=s,open=10.,close=10.,preclose=10.,tradestatus='1',terminal_date=pd.NaT) for s in symbols for d in cal])
    if unresolved:raw.loc[raw.date.eq(cal[3])&raw.symbol.eq(symbols[0]),['open','close','preclose']]=1.
    raw.to_csv(tmp_path/'data/event_raw_prices.csv',index=False)
    metadata=dict(synthetic=False,quality_policy={'schema':'causal_quality_research_v1'},converted_hashes={n:sha256(tmp_path/'data'/n) for n in ['calendar.csv','event_raw_prices.csv']})
    (tmp_path/'data/metadata.json').write_text(json.dumps(metadata))
    pd.DataFrame(dict(date=[cal[0]]*20,symbol=symbols,available_date=cal[0])).to_csv(tmp_path/'experiment/factor_samples.csv',index=False)
    (tmp_path/'experiment/models/summary.json').write_text(json.dumps(dict(input_frame_sha256={'samples':sha256(tmp_path/'experiment/factor_samples.csv')})))
    pd.DataFrame(dict(date=cal,code='sh.000300',open=10.,high=10.,low=10.,close=10.)).to_csv(tmp_path/'benchmark_csi/raw_prices.csv',index=False)
    (tmp_path/'benchmark_csi/summary.json').write_text(json.dumps(dict(return_kind='price_index',raw_sha256=sha256(tmp_path/'benchmark_csi/raw_prices.csv'))))
    pd.DataFrame(columns=['symbol','ex_date','available_date','pay_date','cash_per_share','share_multiplier']).to_csv(tmp_path/'source/effective_valuation_actions.csv',index=False)
    accounts=[]
    for h in ['5d','20d','12m']:
        entry,last=label_interval(cal,cal[0],HORIZONS[h])
        for trees in [30,60]:
            dest=tmp_path/'source'/h/str(trees)/'baseline';dest.mkdir(parents=True)
            pd.DataFrame(dict(date=[cal[0]]*20,symbol=symbols,target_weight=.05)).to_csv(dest/'scheduled_top20.csv',index=False)
            pd.DataFrame(dict(date=cal[(cal>=entry)&(cal<=last)],nav=1.)).to_csv(dest/'daily_nav.csv',index=False)
            for g in ['baseline','market_state','oneil_box','industry','combined']:accounts.append(dict(horizon=h,n_estimators=trees,group=g,status='complete_calendar_mature_ranking_account',requested_end=str(last.date())))
    sources={p.relative_to(tmp_path).as_posix():sha256(p) for folder in ['data','experiment','benchmark_csi'] for p in (tmp_path/folder).rglob('*') if p.is_file()}
    family=dict(status='full_family_ranking_accounts_evaluated_selection_pending',protocol_sha256='fixture',accounts=accounts,source_hashes=sources,code_hashes={},independent_data_used=False,model_selection_performed=False,formal_model_promoted=False,output_hashes={p.relative_to(tmp_path/'source').as_posix():sha256(p) for p in (tmp_path/'source').rglob('*.csv')})
    (tmp_path/'source/manifest.json').write_text(json.dumps(family))
    protocol=dict(horizons=['5d','20d','12m'],research_end=str(cal[-1].date()),execution={'top_k':20})
    for module in [original,streamed]:monkeypatch.setattr(module,'load_seal',lambda *args:(protocol,{'protocol_sha256':'fixture'}))
    work=tmp_path/'research_disk';prior=original.evaluate_equal_weight(tmp_path,'source','data','experiment','benchmark_csi','sealed','old',output_root=work)
    build_quote_store(tmp_path/'data/event_raw_prices.csv',cal,work/'quotes',chunksize=127)
    build_reference_audit(work/'quotes',tmp_path/'data/event_raw_prices.csv',work/'old/effective_valuation_actions.csv',work/'references')
    monkeypatch.setattr(streamed,'reviewed_valuation_references',lambda actions,*args:(actions,dict(source_hashes={},events=0,actual_holder_economics_changed=False)))
    return work,prior


@pytest.mark.parametrize('unresolved',[False,True])
def test_full_three_horizon_producer_keeps_original_eligible_keys_and_exact_ledger(tmp_path,monkeypatch,unresolved):
    work,prior=fixture(tmp_path,monkeypatch,unresolved)
    result=streamed.evaluate_streamed_equal(tmp_path,work,'old','quotes','references','source','data','experiment','sealed','reviewed.json','new')
    assert result['status']=='streamed_equal_weight_attempts_finished' and len(result['accounts'])==3
    assert not result['independent_data_used'] and not result['model_selection_performed'] and not result['formal_model_promoted']
    for r in result['accounts']:
        h=r['horizon'];old=work/'old'/h;new=work/'new'/h
        if unresolved:
            assert r['status']=='incomplete_held_valuation' and r['symbol']=='sh.600000' and 'metrics' not in r
            assert r['holdings'][r['symbol']]>0 and not (new/'daily_nav.csv').exists()
            names=['partial_valid_nav.csv','partial_orders.csv']
        else:
            assert r['status']=='complete_calendar_mature_equal_weight_control' and r['execution']['history_retained_in_memory'] is False
            assert r['requested_end']==next(x['requested_end'] for x in prior['accounts'] if x['horizon']==h)
            names=['daily_nav.csv','orders.csv']
            assert r['metrics']==next(x['metrics'] for x in prior['accounts'] if x['horizon']==h)
        for name in names:pd.testing.assert_frame_equal(pd.read_csv(new/name,float_precision='round_trip'),pd.read_csv(old/name,float_precision='round_trip'),check_exact=True)
    for name,digest in result['output_hashes'].items():assert sha256(work/'new'/name)==digest


@pytest.mark.parametrize('bad',['duplicate','future','unequal','non_a'])
def test_eligible_pool_hashes_reject_changed_membership_visibility_or_weight_rule(tmp_path,bad):
    path=tmp_path/'picks.csv';cal=pd.bdate_range('2023-01-02',periods=3)
    picks=pd.DataFrame(dict(date=[cal[0]]*2,symbol=['sh.600001','sh.600002'],available_date=[cal[0]]*2,target_weight=.5))
    if bad=='duplicate':picks.loc[1,'symbol']=picks.loc[0,'symbol']
    elif bad=='future':picks.loc[0,'available_date']=cal[-1]
    elif bad=='unequal':picks.target_weight=[.6,.4]
    else:picks.loc[0,'symbol']='bj.830001'
    picks.to_csv(path,index=False)
    with pytest.raises(ValueError):streamed.eligible_key_hashes(path,cal,2,True)

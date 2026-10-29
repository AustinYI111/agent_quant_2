import json
from pathlib import Path
import sqlite3
import pandas as pd
import pytest
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.horizon_labels import label_interval,HORIZONS
import ashare_mvp.timing_portfolios as module


@pytest.mark.parametrize('unresolved',[False,True])
def test_actual_producer_binds_full_family_and_replays_full_three_horizons(tmp_path,monkeypatch,unresolved):
    cal=pd.bdate_range('2023-01-02','2024-02-01');names=['sh.'+str(600000+i) for i in range(20)]
    for folder in ['source','cache','data','benchmark','ashare_mvp']:(tmp_path/folder).mkdir()
    for name in ['timing_portfolios.py','timing_policy.py','timing_account.py','pipeline.py','candidate_portfolios.py','corporate_actions.py','causal_quality.py','costs.py','technical_candidates.py','valuation_overlay.py','reference_overrides.py','timing_account_audit.py','timing_verification.py','accounting_attribution.py']:
        (tmp_path/'ashare_mvp'/name).write_bytes(Path(module.__file__).with_name(name).read_bytes())
    pd.DataFrame(dict(date=cal)).to_csv(tmp_path/'data/calendar.csv',index=False)
    raw=pd.DataFrame([dict(date=d,symbol=s,open=10.,close=10.,preclose=10.,tradestatus='1',terminal_date=pd.NaT) for d in cal for s in names])
    if unresolved:raw.loc[raw.date.eq(cal[3])&raw.symbol.eq(names[0]),['open','close','preclose']]=1.
    raw.to_csv(tmp_path/'data/event_raw_prices.csv',index=False)
    flags=raw[['date','symbol']].copy();flags['is_st']=False;flags.to_csv(tmp_path/'data/prices.csv',index=False)
    meta=dict(synthetic=False,quality_policy={'schema':'causal_quality_research_v1'},converted_hashes={n:sha256(tmp_path/'data'/n) for n in ['calendar.csv','event_raw_prices.csv','prices.csv']})
    (tmp_path/'data/metadata.json').write_text(json.dumps(meta))
    pd.DataFrame(dict(date=cal,code='sh.000300',open=10.,high=10.,low=10.,close=10.)).to_csv(tmp_path/'benchmark/raw_prices.csv',index=False)
    (tmp_path/'benchmark/summary.json').write_text(json.dumps(dict(return_kind='price_index',raw_sha256=sha256(tmp_path/'benchmark/raw_prices.csv'))))
    pd.DataFrame(dict(date=cal,index_above_ma200=1.)).to_csv(tmp_path/'cache/market_states.csv',index=False)
    con=sqlite3.connect(tmp_path/'cache/daily_timing.sqlite');con.execute('CREATE TABLE timing(symbol TEXT,date TEXT,box_entry INTEGER,box_exit INTEGER,box_signal_known INTEGER,PRIMARY KEY(symbol,date)) WITHOUT ROWID');con.commit();con.close()
    source_hashes={p.relative_to(tmp_path).as_posix():sha256(p) for folder in ['data','benchmark'] for p in (tmp_path/folder).iterdir()}
    cache=dict(status='research_candidates_built',protocol_sha256='seal',independent_data_used=False,source_hashes=source_hashes,code_hashes={},output_hashes={n:sha256(tmp_path/'cache'/n) for n in ['daily_timing.sqlite','market_states.csv']})
    (tmp_path/'cache/manifest.json').write_text(json.dumps(cache))
    pd.DataFrame(columns=['symbol','ex_date','available_date','pay_date','cash_per_share','share_multiplier']).to_csv(tmp_path/'source/effective_valuation_actions.csv',index=False)
    rows=[]
    for h in ['5d','20d','12m']:
        entry,last=label_interval(cal,cal[0],HORIZONS[h]);assert pd.notna(last)
        for trees in [30,60]:
            for group in ['baseline','market_state','oneil_box','industry','combined']:
                dest=tmp_path/'source'/h/str(trees)/group;dest.mkdir(parents=True)
                pd.DataFrame(dict(date=[cal[0]]*20,symbol=names,target_weight=.05,available_date=cal[0])).to_csv(dest/'scheduled_top20.csv',index=False)
                pd.DataFrame(dict(date=cal[(cal>=entry)&(cal<=last)],nav=1.)).to_csv(dest/'daily_nav.csv',index=False)
                rows.append(dict(horizon=h,n_estimators=trees,group=group,status='complete_calendar_mature_ranking_account',requested_end=str(last.date())))
    family=dict(status='full_family_ranking_accounts_evaluated_selection_pending',protocol_sha256='seal',accounts=rows,source_hashes=source_hashes,code_hashes={},independent_data_used=False,model_selection_performed=False,formal_model_promoted=False,output_hashes={p.relative_to(tmp_path/'source').as_posix():sha256(p) for p in (tmp_path/'source').rglob('*.csv')})
    (tmp_path/'source/manifest.json').write_text(json.dumps(family))
    (tmp_path/'rules.json').write_text(json.dumps(dict(schema='phase2_timing_operational_clarification_v1',implementation='close_timing_policy_v1',original_protocol_sha256='seal',originally_prespecified=False,performance_used_for_clarification=False,recorded_at_utc='2026-10-28T00:00:00+00:00')))
    monkeypatch.setattr(module,'load_seal',lambda *args:(dict(horizons=['5d','20d','12m'],research_end=str(cal[-1].date()),execution={'top_k':20}),{'protocol_sha256':'seal'}))
    result=module.evaluate_timing(tmp_path,'source','cache','data','benchmark','seal','rules.json','out',output_root=tmp_path/'external',groups=['market_state'])
    assert result['status']=='technical_timing_attempts_finished_selection_pending' and len(result['accounts'])==6
    assert result['family_comparisons']==24 and result['groups_attempted']==['market_state']
    assert not result['model_selection_performed'] and not result['formal_model_promoted']
    if unresolved:
        assert all(r['status']=='incomplete_held_valuation' and r['holdings'][names[0]]>0 and 'metrics' not in r for r in result['accounts'])
        assert not result['paired_diagnostics']
    else:
        assert all(r['status']=='complete_calendar_mature_timing_account' for r in result['accounts'])
        assert len(result['paired_diagnostics'])==12
        annual=next(r for r in result['accounts'] if r['horizon']=='12m')
        assert pd.Timestamp(annual['requested_end'])>=entry+pd.DateOffset(months=12)
        nav=pd.read_csv(tmp_path/'external/out/12m/30/market_state/daily_nav.csv')
        assert nav.date.iloc[-1]==annual['requested_end']
    for name,digest in result['output_hashes'].items():assert sha256(tmp_path/'external/out'/name)==digest
    from ashare_mvp.timing_account_audit import audit_timing
    audit=audit_timing(tmp_path,'out',tmp_path/'external','source','data','cache','benchmark','verified',tmp_path/'independent')
    assert audit['status']=='available_complete_timing_accounts_verified'
    assert audit['completed_accounts']==(0 if unresolved else 6)
    assert len(audit['paired_checks'])==(0 if unresolved else 12)
    if not unresolved:assert max(r['max_absolute_reconciliation_error'] for r in audit['accounts'])<1e-12


def test_new_buy_permissions_exclude_st_keep_missing_unknown_and_do_not_forward_fill(tmp_path):
    path=tmp_path/'p.csv';cal=pd.bdate_range('2023-01-02',periods=3)
    pd.DataFrame(dict(date=cal,symbol='sh.600001',is_st=['True',None,'False'])).to_csv(path,index=False)
    flags=module.timing_buy_permissions(path,{'sh.600001'},cal[0],cal[-1])
    assert flags.new_buy_allowed.tolist()==[False,False,True]

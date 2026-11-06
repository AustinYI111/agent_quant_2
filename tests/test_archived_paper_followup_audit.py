from pathlib import Path
import importlib.util,json
import numpy as np
import pandas as pd
import pytest
from test_risk_selection import prepared
from test_prediction_ledger import arguments
from ashare_mvp import risk_selection as risk
from ashare_mvp.workspace_paper_tracking import replay_paper_books
from ashare_mvp.phase2_protocol import sha256


def fixture(prepared):
    args,base,snapshot,_,_=prepared;view=risk.build_risk_selection(**args)
    work=args['work_root'];root=args['project'];source=work/'followup';source.mkdir()
    cal=pd.DatetimeIndex(['2025-12-31']).append(base['calendar'])
    raw=pd.DataFrame([dict(date=d,symbol=s,open=10.,close=10.,preclose=10.,tradestatus='1',isST='0') for d in base['calendar'] for s in base['scores'].symbol])
    actions=work/'actions.csv';pd.DataFrame(columns=['symbol','ex_date','available_date','pay_date','cash_per_share','share_multiplier']).to_csv(actions,index=False)
    views={'2026-10-20':dict(directory='@work/view',manifest_sha256=sha256(work/'view/manifest.json'))}
    paper=replay_paper_books(snapshot/'predictions.sqlite',[r['batch_id'] for r in view['horizons'].values()],root,base['seal_directory'],raw,pd.read_csv(actions),cal,'2026-10-22',source/'paper',
        work_root=work,selection_views=views,risk_policy_start='2026-10-20')
    raw.to_csv(source/'raw_window.csv',index=False);database=snapshot/'predictions.sqlite';digest=sha256(database)
    proof=dict(status='observed_market_window_and_paper_replay_finished',new_forecasts_created=False,backfilled_forecasts_executed=False,
               source_hashes={'@work/actions.csv':sha256(actions)},output_hashes={p.relative_to(source).as_posix():sha256(p) for p in source.rglob('*') if p.is_file()},
               prediction_archive_sha256=digest,paper=paper)
    (source/'followup.json').write_text(json.dumps(proof))
    spec=importlib.util.spec_from_file_location('archived_paper_independent',Path(__file__).resolve().parents[1]/'scripts/audit_archived_paper_followup.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    check=dict(project=str(root),work_root=str(work),source='@work/followup',actions='@work/actions.csv',archive='@work/snapshot/predictions.sqlite',expected_archive_sha256=digest,out='@work/verified.json')
    return check,module,source,proof,work


def test_independent_cash_share_replay_of_saved_books(prepared):
    args,module,source,proof,work=fixture(prepared);result=module.audit(**args)
    assert result['status']=='saved_paper_followup_independently_verified' and len(result['accounts'])==3
    assert all(r['maximum_absolute_cash_share_error']<1e-12 and r['filled_non_st_buy_keys']==20 for r in result['accounts'])


@pytest.mark.parametrize('fault',['cash','cost','missing_batch','late_view','st_buy','unauthorized_buy','held_reference'])
def test_resealed_outputs_cannot_hide_invalid_money_or_forecast_authorization(prepared,fault):
    args,module,source,proof,work=fixture(prepared);file=None
    if fault=='cash':
        file=source/'paper/5d/open_nav.csv';frame=pd.read_csv(file);frame.loc[0,'cash']+=.01
    elif fault=='cost':
        file=source/'paper/5d/orders.csv';frame=pd.read_csv(file);frame.loc[0,'cost']+=.01
    elif fault=='missing_batch':
        file=source/'paper/5d/scheduled_top20.csv';frame=pd.read_csv(file);frame['batch_id']='not_saved'
    elif fault=='late_view':
        manifest=work/'view/manifest.json';m=json.loads(manifest.read_text(encoding='utf8'));m['created_at_utc']='2026-10-21T03:00:00Z';manifest.write_text(json.dumps(m),encoding='utf8')
        proof['paper']['selection_views']['2026-10-20']['manifest_sha256']=sha256(manifest)
    elif fault=='st_buy':
        file=source/'raw_window.csv';frame=pd.read_csv(file,dtype={'isST':str});order=pd.read_csv(source/'paper/5d/orders.csv').iloc[0]
        frame.loc[frame.date.eq(order.date)&frame.symbol.eq(order.symbol),'isST']='1'
    elif fault=='held_reference':
        file=source/'raw_window.csv';frame=pd.read_csv(file);order=pd.read_csv(source/'paper/5d/orders.csv').iloc[0]
        frame.loc[frame.date.eq('2026-10-22')&frame.symbol.eq(order.symbol),'preclose']=9.
    elif fault=='unauthorized_buy':
        file=source/'paper/5d/orders.csv';frame=pd.read_csv(file);old=frame.symbol.iloc[0];frame.loc[frame.symbol.eq(old),'symbol']='sh.600024'
        positions=source/'paper/5d/positions.csv';values=pd.read_csv(positions);values.loc[values.symbol.eq(old),'symbol']='sh.600024';values.to_csv(positions,index=False)
        proof['output_hashes'][positions.relative_to(source).as_posix()]=sha256(positions)
    if file is not None:frame.to_csv(file,index=False);proof['output_hashes'][file.relative_to(source).as_posix()]=sha256(file)
    (source/'followup.json').write_text(json.dumps(proof))
    expected={'unauthorized_buy':'filled buy is absent from authorized forecast','held_reference':'unreconciled held quote reference'}.get(fault)
    with pytest.raises(AssertionError,match=expected):module.audit(**args)
    assert not (work/'verified.json').exists()

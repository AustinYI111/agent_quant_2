from datetime import datetime,timezone
import json,shutil
import pandas as pd
import pytest
from test_prediction_ledger import arguments
from test_paper_tracking import book
from ashare_mvp import prediction_ledger as ledger
from ashare_mvp import risk_selection as risk
from ashare_mvp.phase2_protocol import sha256,seal as make_seal
from ashare_mvp.workspace_paper_tracking import replay_paper_books


@pytest.fixture
def prepared(arguments,monkeypatch):
    root=arguments['project'];work=root.parent/(root.name+'_risk');work.mkdir()
    seal=root/'artifacts/phase2/protocol_20261020';seal.parent.mkdir(parents=True)
    protocol=json.loads((root/'protocol.json').read_text());protocol.update(independent_start='2026-10-20',execution=dict(top_k=20,commission_bps=3,slippage_bps=5))
    (root/'risk_protocol.json').write_text(json.dumps(protocol));make_seal(root/'risk_protocol.json',seal,root)
    arguments['seal_directory']=seal
    source=work/'snapshot';source.mkdir();receipts=[]
    for h in ['5d','20d','12m']:
        receipts.append(ledger.record_predictions(**dict(arguments,database=source/'predictions.sqlite',horizon=h,synthetic=False)))
    m=dict(status='research_snapshot_saved',model_selection_performed=False,signal_date='2026-10-20',protocol_sha256=receipts[0]['protocol_sha256'],receipts=receipts)
    (source/'manifest.json').write_text(json.dumps(m));pd.DataFrame({'date':arguments['calendar']}).to_csv(source/'forecast_calendar.csv',index=False)
    names=pd.DataFrame({'code':arguments['scores'].trading_symbol,'code_name':['普通股'+str(i) for i in range(25)]})
    names.loc[24,'code_name']='某某退';names.loc[23,'code_name']='*ST某某'
    reference=work/'reference';reference.mkdir();file=reference/'stock_all_20261020.csv';names.to_csv(file,index=False)
    capture=dict(queries={'stock_all_20261020':dict(status='raw_acquired',rows=len(names),sha256=sha256(file),observed_at_utc='2026-10-20T09:45:00Z')})
    (reference/'capture.json').write_text(json.dumps(capture))
    class Clock:
        @classmethod
        def now(cls,tz):return datetime(2026,10,20,10,5,tzinfo=timezone.utc)
    monkeypatch.setattr(risk,'datetime',Clock)
    args=dict(project=root,work_root=work,snapshot='@work/snapshot',expected_snapshot_sha256=sha256(source/'manifest.json'),reference_capture='@work/reference',out='@work/view')
    return args,arguments,source,reference,names


def test_risk_view_removes_names_preserves_original_scores_and_fills_top20(prepared):
    args,base,source,reference,names=prepared;before=sha256(source/'predictions.sqlite')
    m=risk.build_risk_selection(**args)
    assert sha256(source/'predictions.sqlite')==before
    for h in ['5d','20d','12m']:
        assert m['horizons'][h]['selected_candidates']==23 and m['horizons'][h]['excluded_candidates']==2
        rows=risk.load_risk_selection('@work/view',sha256(args['work_root']/'view/manifest.json'),source/'predictions.sqlite',m['horizons'][h]['batch_id'],args['project'],args['work_root'],base['calendar'])
        assert rows.original_rank.head(20).tolist()==list(range(3,23))
        assert rows['rank'].tolist()==list(range(1,24))
        assert not rows.code_name.str.contains('ST|退').any()
        original=ledger.read_predictions(source/'predictions.sqlite',m['horizons'][h]['batch_id'])[1].set_index('symbol')
        assert rows.score.tolist()==original.loc[rows.symbol,'score'].tolist()
    assert m['old_holdings_deleted'] is False and m['originally_prespecified'] is False


@pytest.mark.parametrize('fault',['missing_name','future_observation','wrong_signal','changed_source'])
def test_unknown_or_later_status_is_not_used_to_authorize_entry(prepared,fault):
    args,base,source,reference,names=prepared
    capture=json.loads((reference/'capture.json').read_text());key='stock_all_20261020'
    if fault=='missing_name':
        names.loc[0,'code_name']=None;names.to_csv(reference/(key+'.csv'),index=False);capture['queries'][key]['sha256']=sha256(reference/(key+'.csv'))
    if fault=='future_observation':capture['queries'][key]['observed_at_utc']='2026-10-20T10:10:00Z'
    if fault=='wrong_signal':capture['queries']['stock_all_20261021']=capture['queries'].pop(key)
    if fault=='changed_source':(reference/(key+'.csv')).write_text('changed')
    (reference/'capture.json').write_text(json.dumps(capture))
    with pytest.raises(ValueError):risk.build_risk_selection(**args)
    assert not (args['work_root']/'view').exists()


@pytest.mark.parametrize('fault',['score','manifest','parent'])
def test_changed_view_or_parent_is_rejected(prepared,fault):
    args,base,source,reference,names=prepared;m=risk.build_risk_selection(**args)
    view=args['work_root']/'view';expected=sha256(view/'manifest.json')
    if fault=='score':
        rows=pd.read_csv(view/'selected_5d.csv');rows.loc[0,'score']=999;rows.to_csv(view/'selected_5d.csv',index=False)
    if fault=='manifest':(view/'manifest.json').write_text('{}')
    if fault=='parent':(source/'manifest.json').write_text('{}')
    with pytest.raises(ValueError):risk.load_risk_selection('@work/view',expected,source/'predictions.sqlite',m['horizons']['5d']['batch_id'],args['project'],args['work_root'],base['calendar'])


def test_paper_account_requires_status_gate_for_prospective_entries(book):
    args,_,cal=book;work=args['project'].parent/(args['project'].name+'_paper');work.mkdir()
    args.update(out=work/'paper',work_root=work,risk_policy_start=str(cal[0].date()))
    with pytest.raises(ValueError,match='risk selection view required'):replay_paper_books(**args)
    assert not (work/'paper').exists()


def test_prior_policy_paper_math_is_byte_identical(book):
    from ashare_mvp.paper_tracking import replay_paper_books as original
    args,_,cal=book;original(**args)
    work=args['project'].parent/(args['project'].name+'_parity');work.mkdir()
    old=args['out'];args.update(out=work/'paper',work_root=work)
    result=replay_paper_books(**args)
    assert result['books']['5d']['orders']==20
    for file in ['open_nav.csv','orders.csv','positions.csv']:
        assert (old/'5d'/file).read_bytes()==(work/'paper/5d'/file).read_bytes()


def test_risk_view_is_consumed_by_real_paper_order_generator(prepared):
    args,base,source,reference,names=prepared;m=risk.build_risk_selection(**args)
    raw=pd.DataFrame([dict(date=d,symbol=s,open=10.,close=10.,preclose=10.,tradestatus='1') for d in base['calendar'] for s in base['scores'].symbol])
    actions=pd.DataFrame(columns=['symbol','ex_date','available_date','pay_date','cash_per_share','share_multiplier'])
    views={'2026-10-20':dict(directory='@work/view',manifest_sha256=sha256(args['work_root']/'view/manifest.json'))}
    result=replay_paper_books(source/'predictions.sqlite',[r['batch_id'] for r in m['horizons'].values()],args['project'],base['seal_directory'],raw,actions,
        pd.DatetimeIndex(['2025-12-31']).append(base['calendar']),'2026-10-22',args['work_root']/'paper',work_root=args['work_root'],selection_views=views,risk_policy_start='2026-10-20')
    for h in ['5d','20d','12m']:
        assert result['books'][h]['orders']==20
        picks=pd.read_csv(args['work_root']/f'paper/{h}/scheduled_top20.csv')
        assert not set(picks.symbol)&{'issuer_24','issuer_23'}


def test_view_created_after_entry_cannot_authorize_a_historical_fill(prepared,monkeypatch):
    args,base,source,reference,names=prepared
    class Late:
        @classmethod
        def now(cls,tz):return datetime(2026,10,21,3,tzinfo=timezone.utc)
    monkeypatch.setattr(risk,'datetime',Late)
    with pytest.raises(ValueError,match='retrospectively'):risk.build_risk_selection(**args)
    assert not (args['work_root']/'view').exists()

from pathlib import Path
import json
import pandas as pd
import pytest
from test_prediction_ledger import arguments
from ashare_mvp import daily_input_recipe as recipe
from ashare_mvp.phase2_protocol import sha256


def write(file, data):
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(data), encoding='utf8')


@pytest.fixture
def prepared(arguments, monkeypatch):
    project = arguments['project']
    work = project.parent / (project.name + '_work')
    work.mkdir()
    monkeypatch.setattr(recipe, 'now', lambda: pd.Timestamp('2026-10-21T11:00:00Z'))
    cal = pd.DataFrame({'calendar_date': pd.date_range('2026-10-19', '2026-10-23').strftime('%Y-%m-%d'), 'is_trading_day': '1'})
    calendar = work / 'old_calendar.csv'; cal.to_csv(calendar, index=False)
    store = project / 'store'; store.mkdir()
    pd.DataFrame({'date': ['2026-10-19', '2026-10-20']}).to_csv(store / 'calendar.csv', index=False)
    changes = project / 'changes.json'
    pd.DataFrame(columns=['old_symbol', 'new_symbol', 'announcement_date', 'effective_date', 'share_ratio', 'source_url']).to_json(changes)
    protocol = json.loads((project / 'seal/seal.json').read_text())['protocol_sha256']

    def market(directory, date):
        directory.mkdir(parents=True)
        cal.to_csv(directory / 'calendar.csv', index=False)
        frame = pd.DataFrame([dict(date=date, code=s, open=10., high=11., low=9., close=10., preclose=9.9 if date=='2026-10-21' and s=='sh.600001' else 10.,
                                   volume=100., amount=1000., tradestatus='1', isST='0', adjustflag='3') for s in ['sh.600001', 'sh.600002']])
        filename = 'daily_' + date.replace('-', '') + '.csv'; frame.to_csv(directory / filename, index=False)
        write(directory / 'manifest.json', dict(schema='market_daily_raw_v1', status='downloaded_raw', synthetic=False,
              files={'calendar.csv': dict(sha256=sha256(directory / 'calendar.csv')),
                     filename: dict(sha256=sha256(directory / filename), rows=2, retrieved_at=date+'T10:00:00Z')}))
    oldmarket = work / 'oldmarket'; market(oldmarket, '2026-10-20')
    capture = work / 'capture'; market(capture / 'market/latest', '2026-10-21')
    cal.to_csv(capture / 'market/exchange_calendar.csv', index=False)
    names = capture / 'reference/stock_all_20261021.csv'; names.parent.mkdir()
    pd.DataFrame(dict(code=['sh.600001', 'sh.600002'], code_name=['普通A', '普通B'], tradeStatus=['1', '1'])).to_csv(names, index=False)
    write(capture / 'reference/capture.json', dict(status='raw_acquired', protocol_sha256=protocol,
          queries={'stock_all_20261021': dict(status='raw_acquired', sha256=sha256(names), observed_at_utc='2026-10-21T10:00:00Z')}))

    def seal_capture():
        write(capture / 'capture.json', dict(status='raw_acquired', signal_date='2026-10-21', synthetic=False, protocol_sha256=protocol,
              completed_at_utc='2026-10-21T10:05:00Z', output_hashes={p.relative_to(capture).as_posix():sha256(p) for p in capture.rglob('*') if p.is_file() and p!=capture/'capture.json'}))
    seal_capture()
    parent_actions = work / 'oldactions'; parent_actions.mkdir()
    old_events = pd.DataFrame([dict(symbol='sh.600002', ex_date='2026-10-19', available_date='2026-10-19', pay_date='2026-10-19', cash_per_share=.02, share_multiplier=1., reference_cash_per_share=.02, reference_share_multiplier=1.)])
    old_events.to_csv(parent_actions / 'actions.csv', index=False)
    write(parent_actions / 'summary.json', dict(output_hashes={'actions.csv': sha256(parent_actions / 'actions.csv')}))
    stage = work / 'actions'; stage.mkdir()
    new = dict(symbol='sh.600001', ex_date='2026-10-21', available_date='2026-10-21', pay_date='2026-10-21', cash_per_share=.1, share_multiplier=1., reference_cash_per_share=.1, reference_share_multiplier=1.)
    pd.concat([old_events,pd.DataFrame([new])],ignore_index=True).to_csv(stage / 'actions.csv', index=False)

    def seal_actions():
        write(stage / 'summary.json', dict(status='cash_terms_staged_raw_quote_check_pending', created_at_utc='2026-10-21T09:00:00Z', output_hashes={'actions.csv': sha256(stage / 'actions.csv')}))
        write(work / 'actions_audit.json', dict(status='raw_intake_and_cash_extensions_independently_verified', staged_summary_sha256=sha256(stage / 'summary.json'), audited_at_utc='2026-10-21T10:05:00Z'))
    seal_actions()
    oldfinance = work / 'oldfinance'; oldfinance.mkdir()
    pd.DataFrame([dict(symbol='sh.600001', report_date='2026-06-30', available_date='2026-10-20')]).to_csv(oldfinance / 'financial_panel.csv', index=False)
    write(oldfinance / 'summary.json', dict(panel_sha256=sha256(oldfinance / 'financial_panel.csv')))
    refresh = work / 'financial'; combined = refresh / 'history/combined'; combined.mkdir(parents=True)
    pd.DataFrame([dict(symbol='sh.600001', report_date='2026-09-30', available_date='2026-10-21')]).to_csv(combined / 'financial_panel.csv', index=False)
    write(combined / 'summary.json', dict(panel_sha256=sha256(combined / 'financial_panel.csv'), partial_source=False))
    fm = dict(status='dependencies_refreshed_before_join', signal_date='2026-10-21', completed_at_utc='2026-10-21T10:00:00Z', combined=dict(records=1),
              output_hashes={'history/combined/summary.json': sha256(combined / 'summary.json')})
    write(refresh / 'refresh.json', fm)
    write(work / 'financial_audit.json', dict(status='all_combined_versions_independently_selected', refresh_manifest_sha256=sha256(refresh / 'refresh.json'), records=1, audited_at_utc='2026-10-21T10:05:00Z'))
    config = dict(schema='frozen_daily_inputs_v1', seal='seal', baseline='baseline', seed='seed', raw_store='store',
                  markets=['@work/oldmarket'], calendar='@work/old_calendar.csv', financials='@work/oldfinance', actions='@work/oldactions', code_changes='changes.json', masters=[], financial_history=[])
    write(work / 'previous_inputs.json', config)
    snapshot = work / 'snapshot'; snapshot.mkdir()
    parent = dict(schema='frozen_daily_research_snapshot_v1', status='research_snapshot_saved', signal_date='2026-10-20', model_selection_performed=False,
                  completed_at_utc='2026-10-20T10:05:00Z', protocol_sha256=protocol,
                  source_hashes={'@work/previous_inputs.json': sha256(work / 'previous_inputs.json'), '@work/old_calendar.csv': sha256(calendar),
                                 '@work/oldmarket/manifest.json': sha256(oldmarket / 'manifest.json'), 'changes.json': sha256(changes),
                                 '@work/oldactions/summary.json': sha256(parent_actions / 'summary.json'),
                                 'store/calendar.csv': sha256(store / 'calendar.csv')})
    write(snapshot / 'manifest.json', parent)
    call=dict(project=project,work_root=work,previous_snapshot='@work/snapshot',previous_snapshot_sha256=sha256(snapshot/'manifest.json'),
              previous_inputs='@work/previous_inputs.json',capture='@work/capture',financial_refresh='@work/financial',financial_audit='@work/financial_audit.json',
              actions='@work/actions',actions_audit='@work/actions_audit.json',signal='2026-10-21',out='@work/prepared',exploratory=True)
    return call,work,seal_capture,seal_actions


def test_prepares_portable_inputs_and_known_cash_checks_without_predictions(prepared):
    call,work,_,_=prepared; result=recipe.prepare(**call)
    config=json.loads((work/'prepared/frozen_inputs.json').read_text())
    assert config['markets']==['@work/oldmarket','@work/capture/market/latest']
    assert config['financial_history']==['@work/oldfinance'] and config['actions']=='@work/actions'
    assert result['reviewed_cash_reference_checks']==[dict(symbol='sh.600001',ex_date='2026-10-21',reference_matches=True)]
    assert not result['predictions_created'] and not result['holdings_valued'] and not result['model_ready']
    assert not list(work.rglob('*.sqlite'))


@pytest.mark.parametrize('fault,match',[
 ('before_close','17:20'),('partial_capture','complete real capture'),('missing_session','missing exchange sessions'),
 ('changed_parent','input hash differs'),('unknown_name','security scope'),('future_financial','verified visible financial'),
 ('wrong_financial_audit','verified visible financial'),('prior_cash','prior cash values'),('known_cash','cash reference disagrees'),
 ('bad_ohlc','OHLC'),('future_market_clock','observation precedes'),('duplicate_market','duplicate or overlapping'),
 ('prior_calendar','revises known previous'),('nested_output','overlaps an inherited')])
def test_rejects_unverified_or_noncausal_sources_without_creating_output(prepared,monkeypatch,fault,match):
    call,work,seal_capture,seal_actions=prepared
    market=work/'capture/market/latest'; daily=market/'daily_20261021.csv'
    if fault=='before_close':monkeypatch.setattr(recipe,'now',lambda:pd.Timestamp('2026-10-21T09:00:00Z'))
    elif fault=='partial_capture':
        m=json.loads((work/'capture/capture.json').read_text());m['status']='partial';write(work/'capture/capture.json',m)
    elif fault=='missing_session':
        m=json.loads((market/'manifest.json').read_text());m['files'].pop('daily_20261021.csv');write(market/'manifest.json',m);seal_capture()
    elif fault=='changed_parent':write(work/'previous_inputs.json',{})
    elif fault=='unknown_name':
        file=work/'capture/reference/stock_all_20261021.csv';names=pd.read_csv(file);names.loc[0,'code']='sh.699999';names.to_csv(file,index=False)
        ref=work/'capture/reference/capture.json';m=json.loads(ref.read_text());m['queries']['stock_all_20261021']['sha256']=sha256(file);write(ref,m);seal_capture()
    elif fault in ['future_financial','wrong_financial_audit']:
        file=work/'financial/refresh.json';m=json.loads(file.read_text())
        if fault=='future_financial':m['signal_date']='2026-10-22';write(file,m)
        else:
            file=work/'financial_audit.json';m=json.loads(file.read_text());m['refresh_manifest_sha256']='0'*64;write(file,m)
    elif fault in ['prior_cash','known_cash']:
        file=work/'actions/actions.csv';frame=pd.read_csv(file);frame.loc[0 if fault=='prior_cash' else 1,['cash_per_share','reference_cash_per_share']]=.2;frame.to_csv(file,index=False);seal_actions()
    elif fault=='bad_ohlc':
        frame=pd.read_csv(daily);frame.loc[0,'high']=1.;frame.to_csv(daily,index=False)
        m=json.loads((market/'manifest.json').read_text());m['files'][daily.name]['sha256']=sha256(daily);write(market/'manifest.json',m);seal_capture()
    elif fault=='future_market_clock':
        m=json.loads((market/'manifest.json').read_text());m['files'][daily.name]['retrieved_at']='2026-10-21T12:00:00Z';write(market/'manifest.json',m);seal_capture()
    elif fault=='duplicate_market':call['additional_markets']=['@work/capture/market/latest']
    elif fault=='prior_calendar':
        file=work/'capture/market/exchange_calendar.csv';frame=pd.read_csv(file,dtype=str);frame.loc[frame.calendar_date.eq('2026-10-20'),'is_trading_day']='0';frame.to_csv(file,index=False);seal_capture()
    elif fault=='nested_output':call['out']='@work/oldmarket/nested'
    with pytest.raises(ValueError,match=match):recipe.prepare(**call)
    assert not (work/'prepared').exists()


def test_unknown_adjustment_is_retained_without_inventing_cash(prepared):
    call,work,seal_capture,_=prepared;file=work/'capture/market/latest/daily_20261021.csv';frame=pd.read_csv(file);frame.loc[1,'preclose']=9.7;frame.to_csv(file,index=False)
    manifest=file.parent/'manifest.json';m=json.loads(manifest.read_text());m['files'][file.name]['sha256']=sha256(file);write(manifest,m);seal_capture()
    before=sha256(work/'actions/actions.csv');result=recipe.prepare(**call)
    assert result['quality']['issue_counts']=={'unreconciled_adjustment':1} and not result['model_ready']
    assert sha256(work/'actions/actions.csv')==before and 'sh.600002' in (work/'prepared/quality_issues.csv').read_text()


def test_prior_capture_can_start_later_when_sealed_baseline_covers_earlier_sessions(prepared):
    call,work,_,_=prepared;file=work/'old_calendar.csv';frame=pd.read_csv(file,dtype=str)
    frame[frame.calendar_date.ge('2026-10-20')].to_csv(file,index=False)
    snapshot=work/'snapshot/manifest.json';m=json.loads(snapshot.read_text());m['source_hashes']['@work/old_calendar.csv']=sha256(file);write(snapshot,m)
    call['previous_snapshot_sha256']=sha256(snapshot)
    assert recipe.prepare(**call)['new_sessions']==['2026-10-21']


def test_existing_recipe_is_never_overwritten(prepared):
    call,work,_,_=prepared;recipe.prepare(**call);file=work/'prepared/frozen_inputs.json';before=sha256(file)
    with pytest.raises(ValueError,match='fresh separate'):recipe.prepare(**call)
    assert sha256(file)==before

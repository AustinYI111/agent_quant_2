import json,sqlite3
import numpy as np
import pandas as pd
import pytest
from ashare_mvp import prediction_ledger as ledger
from ashare_mvp.phase2_protocol import seal,sha256
from ashare_mvp.paper_tracking import replay_paper_books,scheduled_signals


@pytest.fixture
def book(tmp_path,monkeypatch):
    models={}
    for h in ['5d','20d','12m']:
        folder=tmp_path/'models'/h;folder.mkdir(parents=True)
        (folder/'model.txt').write_text('synthetic fixture only')
        (folder/'metadata.json').write_text(json.dumps(dict(horizon={'key':h},trained_asof='2025-01-02')))
        for name in ['feature_input_policy.json','training_dataset_metadata.json']:(folder/name).write_text('{}')
        models[h]=dict(relative_path='models/'+h,trained_asof='2025-01-02',files_sha256={p.name:sha256(p) for p in folder.iterdir()})
    protocol=dict(schema='phase2_research_protocol_v1',frozen_at_utc='2026-01-04T02:00:00Z',research_end='2025-12-31',independent_start='2026-01-01',prospective_start='2026-01-05',models=models,candidate_groups=[{'id':x} for x in ['industry','market_state','oneil_box','combined']],validation={'independent_data_may_select_parameters':False,'unknown_outcomes':'unmatured_or_unresolved_never_zero'},execution={'top_k':20,'commission_bps':3,'slippage_bps':5})
    source=tmp_path/'protocol.json';source.write_text(json.dumps(protocol));seal(source,tmp_path/'seal',tmp_path)
    (tmp_path/'features.csv').write_text('explicit synthetic fixture')
    cal=pd.bdate_range('2026-01-05',periods=35)
    monkeypatch.setattr(ledger,'_now',lambda:pd.Timestamp('2026-01-05T10:00:00Z'))
    scores=pd.DataFrame(dict(symbol=[f'sh.{600000+i:06}' for i in range(20)],score=np.arange(20,dtype=float),is_st=False,eligible=True))
    scores['trading_symbol']=scores.symbol
    def save(signal=cal[0],horizon='5d'):
        observed=(signal+pd.Timedelta(hours=17,minutes=30)).tz_localize('Asia/Shanghai').tz_convert('UTC').isoformat()
        return ledger.record_predictions(tmp_path/'predictions.sqlite',project=tmp_path,seal_directory=tmp_path/'seal',horizon=horizon,signal_date=signal,scores=scores,expected_symbols=list(scores.symbol),sources=[dict(path='features.csv',sha256=sha256(tmp_path/'features.csv'),observed_at_utc=observed)],calendar=cal,synthetic=False)
    receipt=save()
    raw=pd.DataFrame([dict(date=d,symbol=s,open=10.,close=10.,preclose=10.,tradestatus='1') for d in cal[:6] for s in scores.symbol])
    actions=pd.DataFrame(columns=['symbol','ex_date','available_date','pay_date','cash_per_share','share_multiplier'])
    args=dict(database=tmp_path/'predictions.sqlite',batch_ids=[receipt['batch_id']],project=tmp_path,seal_directory=tmp_path/'seal',raw_prices=raw,actions=actions,calendar=pd.DatetimeIndex(['2025-12-31']).append(cal),asof=cal[5],out=tmp_path/'book',benchmark=pd.DataFrame(dict(date=cal[:6],open=10.)))
    return args,save,cal


def test_existing_share_account_costs_and_same_open_benchmark(book):
    args,_,_=book;result=replay_paper_books(**args)
    h=result['books']['5d'];assert h['status']=='valued_research_prefix' and h['nav_rows']==5 and h['orders']==20
    nav=pd.read_csv(args['out']/'5d/open_nav.csv')
    np.testing.assert_allclose(nav.nav,1/1.0008,rtol=0,atol=1e-14)
    assert h['price_index_comparison']['excess_total_return']<0
    assert result['books']['12m']['nav_rows']==0 and result['input_frame_sha256']


def test_cash_dividend_remains_receivable_until_paid_and_wealth_is_preserved(book):
    args,_,cal=book;raw=args['raw_prices'];mask=raw.date.ge(cal[2]);raw.loc[mask,['open','close','preclose']]=9.
    args['actions']=pd.DataFrame([dict(symbol=s,ex_date=cal[2],available_date=cal[1],pay_date=cal[3],cash_per_share=1.,share_multiplier=1.) for s in raw.symbol.unique()])
    replay_paper_books(**args);nav=pd.read_csv(args['out']/'5d/open_nav.csv')
    np.testing.assert_allclose(nav.nav,1/1.0008,rtol=0,atol=1e-14)
    assert nav.dividend_receivable.iloc[1]>0 and nav.dividend_receivable.iloc[-1]==0
    assert nav.cash.iloc[-1]>0


def test_split_adjusts_actual_shares_in_saved_positions_without_changing_wealth(book):
    args,_,cal=book;raw=args['raw_prices'];raw.loc[raw.date.ge(cal[2]),['open','close','preclose']]=5.
    args['actions']=pd.DataFrame([dict(symbol=s,ex_date=cal[2],available_date=cal[1],pay_date=cal[2],cash_per_share=0.,share_multiplier=2.) for s in raw.symbol.unique()])
    result=replay_paper_books(**args)['books']['5d'];positions=pd.read_csv(args['out']/'5d/positions.csv')
    np.testing.assert_allclose(positions.shares,2/(20*10*1.0008),atol=1e-14)
    assert result['shares_cash_receivables_reconciled'] and len(positions)==20


def test_blocked_new_buy_stays_cash_and_is_not_replaced(book):
    args,_,cal=book;raw=args['raw_prices'];raw.loc[raw.date.eq(cal[1])&raw.symbol.eq('sh.600000'),'tradestatus']='0'
    result=replay_paper_books(**args)['books']['5d'];positions=pd.read_csv(args['out']/'5d/positions.csv');orders=pd.read_csv(args['out']/'5d/orders.csv')
    assert len(positions)==19 and len(orders.loc[orders.status.eq('blocked')])==1
    assert result['shares_cash_receivables_reconciled']


@pytest.mark.parametrize('fault',['reference','missing','terminal'])
def test_unknown_positive_holding_stops_without_deleting_asset(book,fault):
    args,_,cal=book;raw=args['raw_prices'];mask=raw.symbol.eq('sh.600000')&raw.date.eq(cal[2])
    if fault=='reference':raw.loc[mask,['open','close','preclose']]=8.
    if fault=='missing':args['raw_prices']=raw.loc[~mask].copy()
    if fault=='terminal':raw['terminal_date']=pd.NaT;raw.loc[raw.symbol.eq('sh.600000'),'terminal_date']=cal[2]
    result=replay_paper_books(**args)['books']['5d']
    assert result['status']=='incomplete_held_valuation' and result['reason']==fault
    assert result['nav_rows']==1 and result['holdings']['sh.600000']>0


def test_late_forecast_never_creates_fake_trades_or_zero_return(book,monkeypatch):
    args,save,cal=book
    monkeypatch.setattr(ledger,'_now',lambda:pd.Timestamp('2026-01-07T10:00:00Z'))
    receipt=save(cal[0],'20d');args['batch_ids']=[receipt['batch_id']]
    result=replay_paper_books(**args)
    assert len(result['excluded'])==1
    assert all(b['nav_rows']==0 for b in result['books'].values())
    assert not list(args['out'].rglob('*nav.csv'))


def test_schedule_is_not_shifted_to_first_available_forecast(book,monkeypatch):
    args,save,cal=book;monkeypatch.setattr(ledger,'_now',lambda:pd.Timestamp('2026-01-06T10:00:00Z'))
    args['batch_ids']=[save(cal[1])['batch_id']]
    assert replay_paper_books(**args)['books']['5d']['nav_rows']==0
    assert scheduled_signals(args['calendar'],'2026-01-01','5d').equals(cal[::5])


def test_cropped_calendar_cannot_reanchor_the_frozen_grid(book):
    args,_,cal=book;args['calendar']=cal
    with pytest.raises(ValueError,match='boundary'):replay_paper_books(**args)
    with pytest.raises(ValueError,match='reanchor'):scheduled_signals(cal[3:],'2026-01-01','5d')


def test_missing_later_scheduled_forecast_keeps_only_valid_prefix(book):
    args,_,cal=book
    extra=pd.DataFrame([dict(date=d,symbol=s,open=10.,close=10.,preclose=10.,tradestatus='1') for d in cal[6:9] for s in args['raw_prices'].symbol.unique()])
    args['raw_prices']=pd.concat([args['raw_prices'],extra],ignore_index=True);args['asof']=cal[8];args['benchmark']=None
    result=replay_paper_books(**args)['books']['5d']
    assert result['status']=='incomplete_missing_forecast' and result['missing_signal_date']==str(cal[5].date())
    assert result['nav_rows']==5


def test_tampered_actual_score_is_rejected_before_output(book):
    args,_,_=book
    with sqlite3.connect(args['database']) as c:
        c.execute('DROP TRIGGER scores_no_update');c.execute('UPDATE scores SET score=score+1')
    with pytest.raises(ValueError,match='content changed'):replay_paper_books(**args)
    assert not args['out'].exists()


def test_future_raw_bar_or_existing_output_cannot_be_used(book):
    args,_,cal=book;args['asof']=cal[4]
    with pytest.raises(ValueError,match='future bars'):replay_paper_books(**args)
    args['out'].mkdir();(args['out']/'keep').write_text('original')
    with pytest.raises(ValueError,match='new paper'):replay_paper_books(**args)
    assert (args['out']/'keep').read_text()=='original'

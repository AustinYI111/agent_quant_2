import hashlib,json
import numpy as np
import pandas as pd
import pytest
from ashare_mvp.horizon_labels import HORIZONS,resolve_horizons,label_interval,forward_horizon_labels,write_horizon_label_files
from ashare_mvp.corporate_actions import entry_return_path,economic_return_prices


def raw_fixture():
    cal=pd.bdate_range('2020-01-02',periods=430)
    event_specs={40:(2.,1.,120),60:(0.,1.1,60),100:(1.,1.,125),200:(.5,1.,205)}
    actions=pd.DataFrame([dict(symbol='sh.600000',ex_date=cal[i],available_date=cal[i-1],pay_date=cal[pay],cash_per_share=cash,share_multiplier=shares)
        for i,(cash,shares,pay) in event_specs.items()])
    rows=[];previous=100.
    for i,date in enumerate(cal):
        cash,mult,_=event_specs.get(i,(0.,1.,i));reference=(previous-cash)/mult
        tradable=i!=121
        opening=reference if tradable else 0.
        close=reference*1.001 if tradable else reference
        rows.append(dict(date=date,symbol='sh.600000',open=opening,close=close,preclose=reference,tradestatus='1' if tradable else '0'))
        previous=close
    return pd.DataFrame(rows),actions,cal


def test_session_horizons_skip_holidays_and_count_from_next_open():
    cal=pd.bdate_range('2024-02-01','2024-03-31')
    cal=cal[~cal.isin(pd.bdate_range('2024-02-09','2024-02-16'))]
    entry,end=label_interval(cal,'2024-02-02',HORIZONS['5d'])
    assert entry==pd.Timestamp('2024-02-05') and end==pd.Timestamp('2024-02-20')
    assert label_interval(cal,'2024-02-02',HORIZONS['20d'])[1]==pd.Timestamp('2024-03-12')


def test_twelve_months_is_calendar_anniversary_with_next_session():
    cal=pd.bdate_range('2020-02-01','2021-03-05')
    entry,end=label_interval(cal,'2020-02-27',HORIZONS['12m'])
    assert entry==pd.Timestamp('2020-02-28') and end==pd.Timestamp('2021-03-01')


def test_bucket_labels_equal_independent_new_entry_ledgers_for_all_horizons():
    prices,actions,cal=raw_fixture();signals=cal[[0,35,39,40,59,60,99,100,120,121,130,429]]
    labels=forward_horizon_labels(prices,actions,cal,signals)
    assert len(labels)==len(signals)*3
    for row in labels.itertuples(index=False):
        if row.status in ['unmatured','no_next_session','blocked_entry']:
            assert pd.isna(row.target)
            continue
        path=entry_return_path(prices,actions,cal,row.symbol,row.entry_date,row.label_end)
        last=path.iloc[-1]
        assert row.valuation_return==pytest.approx(last.holding_return,abs=1e-12)
        assert row.executable_exit==bool(last.executable_exit)
        if row.executable_exit:assert row.target==pytest.approx(last.holding_return,abs=1e-12)
        else:assert pd.isna(row.target)


def test_new_buyer_does_not_inherit_pre_entry_unpaid_dividend():
    prices,actions,cal=raw_fixture();signal=cal[40]
    labels=forward_horizon_labels(prices,actions,cal,[signal],['5d'])
    row=labels.iloc[0]
    seasoned=economic_return_prices(prices,actions,cal).set_index('date')
    inherited=seasoned.at[row.label_end,'total_return_open']/seasoned.at[row.entry_date,'total_return_open']-1
    assert not np.isclose(row.target,inherited,rtol=0,atol=1e-8)
    assert row.target==pytest.approx(entry_return_path(prices,actions,cal,row.symbol,row.entry_date,row.label_end).holding_return.iloc[-1])


def test_known_future_calendar_never_shortens_unobserved_outcome():
    prices,actions,cal=raw_fixture();future=pd.bdate_range(cal[0],periods=500)
    labels=forward_horizon_labels(prices,actions,future,[cal[420]],['20d','12m'])
    assert labels.status.eq('unmatured').all() and labels.label_end.isna().all() and labels.target.isna().all()


def test_asof_blocks_future_outcomes_and_future_signals():
    prices,actions,cal=raw_fixture()
    labels=forward_horizon_labels(prices,actions,cal,[cal[35]],['5d','20d'],asof=cal[45])
    assert labels.set_index('horizon').at['20d','status']=='unmatured'
    assert labels.set_index('horizon').at['5d','status']=='valued'
    with pytest.raises(ValueError,match='signal'):forward_horizon_labels(prices,actions,cal,[cal[46]],asof=cal[45])


def test_blocked_entry_and_blocked_exit_are_not_training_returns():
    prices,actions,cal=raw_fixture()
    labels=forward_horizon_labels(prices,actions,cal,[cal[120],cal[115]],['5d'])
    status=labels.set_index('date')
    assert status.at[cal[120],'status']=='blocked_entry' and pd.isna(status.at[cal[120],'target'])
    assert status.at[cal[115],'status']=='blocked_exit' and pd.isna(status.at[cal[115],'target'])
    assert np.isfinite(status.at[cal[115],'valuation_return'])


def test_missing_terminal_prices_are_not_silently_dropped():
    prices,actions,cal=raw_fixture();other=prices.copy();other['symbol']='sh.600001'
    other_actions=actions.copy();other_actions['symbol']='sh.600001'
    prices=pd.concat([prices.iloc[:-15],other],ignore_index=True)
    actions=pd.concat([actions,other_actions],ignore_index=True)
    with pytest.raises(ValueError,match='missing sessions|terminal'):
        forward_horizon_labels(prices,actions,cal,[cal[405]],['20d'])


@pytest.mark.parametrize('keys',[[],['5d','5d'],['5m'],[5],['12m','20']])
def test_horizon_unit_must_be_explicit_and_distinct(keys):
    with pytest.raises(ValueError,match='explicit horizon'):resolve_horizons(keys)


def test_file_writer_seals_units_and_hashes_without_certifying_strategy(tmp_path):
    prices,actions,cal=raw_fixture()
    prices.to_csv(tmp_path/'prices.csv',index=False);actions.to_csv(tmp_path/'actions.csv',index=False)
    pd.DataFrame({'date':cal}).to_csv(tmp_path/'calendar.csv',index=False)
    pd.DataFrame({'date':cal[[35,39,40]]}).to_csv(tmp_path/'signals.csv',index=False)
    paths=[tmp_path/name for name in ['prices.csv','actions.csv','calendar.csv','signals.csv']]
    result=write_horizon_label_files(*paths,tmp_path/'out')
    assert result['model_ready'] is result['certified'] is False
    assert {h['key']:h['unit'] for h in result['horizons']}=={'5d':'trading_sessions','20d':'trading_sessions','12m':'calendar_months'}
    assert hashlib.sha256((tmp_path/'out/labels.csv').read_bytes()).hexdigest()==result['labels_sha256']
    for name,path in zip(['prices','actions','calendar','signals'],paths):assert hashlib.sha256(path.read_bytes()).hexdigest()==result['input_sha256'][name]
    with pytest.raises(ValueError,match='empty'):write_horizon_label_files(*paths,tmp_path/'out')

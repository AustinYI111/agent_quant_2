from pathlib import Path
import importlib.util
import pandas as pd
import pytest

spec=importlib.util.spec_from_file_location('valuation_replay',Path(__file__).resolve().parents[1]/'scripts/replay_horizon_valuation.py')
replay=importlib.util.module_from_spec(spec);spec.loader.exec_module(replay)


def inputs():
    cal=pd.bdate_range('2024-01-01',periods=6)
    actions=pd.DataFrame([dict(symbol='sh.600000',ex_date=cal[3],available_date=cal[2],pay_date=cal[3],cash_per_share=.6,share_multiplier=1.)])
    evidence=pd.DataFrame([dict(symbol='sh.600000',ex_date=cal[3],announcement_date=cal[1],available_date=cal[2],
        reference_cash_per_share=.5847288,expected_cash_per_share=.6,reference_share_multiplier=1.,expected_share_multiplier=1.,
        source_url='https://example.org/test-fixture.pdf',source_sha256='a'*64,source_page=1,reviewed=True)])
    return actions,evidence,cal


def test_reference_overlay_preserves_actual_entitlements_and_dates():
    actions,evidence,cal=inputs();before=actions.copy(deep=True)
    updated,changed=replay.reference_only(actions,evidence,cal)
    pd.testing.assert_frame_equal(actions,before)
    pd.testing.assert_frame_equal(updated[actions.columns],before)
    assert updated.reference_cash_per_share.iloc[0]==.5847288
    assert changed[['symbol','ex_date']].equals(actions[['symbol','ex_date']])


def test_reference_overlay_rejects_conflicting_actual_payout_and_later_clock():
    actions,evidence,cal=inputs();wrong=evidence.copy();wrong['expected_cash_per_share']=.5
    with pytest.raises(ValueError,match='actual payout'):replay.reference_only(actions,wrong,cal)
    changed=evidence.copy();changed['available_date']=cal[3]
    with pytest.raises(AssertionError):replay.reference_only(actions,changed,cal)


def test_changed_actual_cash_cannot_hide_inside_reference_operation(monkeypatch):
    actions,evidence,cal=inputs();changed=actions.copy();changed['cash_per_share']=.123
    monkeypatch.setattr(replay,'apply_reference_overrides',lambda *args:changed)
    with pytest.raises(AssertionError):replay.reference_only(actions,evidence,cal)


def test_reference_change_outside_reviewed_keys_rejected(monkeypatch):
    actions,evidence,cal=inputs()
    other=actions.copy();other['symbol']='sh.600004';actions=pd.concat([actions,other],ignore_index=True)
    changed=actions.copy();changed['reference_cash_per_share']=[float('nan'),.1]
    monkeypatch.setattr(replay,'apply_reference_overrides',lambda *args:changed)
    with pytest.raises(ValueError,match='outside reviewed keys'):replay.reference_only(actions,evidence,cal)


def prefixes(tmp_path):
    source=tmp_path/'experiment';target=tmp_path/'replay'
    dates=pd.bdate_range('2024-01-01',periods=4)
    for key in ['5d','20d','12m']:
        old=source/'portfolios'/key;new=target/key;old.mkdir(parents=True);new.mkdir(parents=True)
        nav=pd.DataFrame(dict(date=dates[:3],nav=[1.,1.01,1.02],cash=[0.,0.,0.],positions=1,dividend_receivable=[0,0,0]))
        nav.to_csv(old/'partial_valid_nav.csv',index=False)
        extended=pd.concat([nav,pd.DataFrame([dict(date=dates[3],nav=1.03,cash=.1,positions=1,dividend_receivable=.25)])],ignore_index=True)
        extended.to_csv(new/'partial_valid_nav.csv',index=False)
        orders=pd.DataFrame([dict(date=dates[0],symbol='sh.600000',side='buy',notional=1.,cost=.001,status='filled')])
        orders.to_csv(old/'partial_orders.csv',index=False)
        orders.to_csv(new/'partial_orders.csv',index=False)
    return source,target


def test_csv_zero_dtype_difference_does_not_change_exact_economic_prefix(tmp_path):
    source,target=prefixes(tmp_path)
    checked=replay.compare_prefixes(source,target)
    assert len(checked)==3 and all(row['exact_match'] and row['nav_rows']==3 for row in checked)


def test_any_numeric_prefix_change_still_rejected_without_tolerance(tmp_path):
    source,target=prefixes(tmp_path)
    path=target/'5d/partial_valid_nav.csv';nav=pd.read_csv(path)
    nav.loc[1,'nav']+=1e-10;nav.to_csv(path,index=False)
    with pytest.raises(AssertionError):replay.compare_prefixes(source,target)


def test_completed_horizon_can_add_only_untouched_leading_cash(tmp_path):
    source,target=prefixes(tmp_path)
    path=target/'5d/partial_valid_nav.csv';nav=pd.read_csv(path,parse_dates=['date'])
    leading=pd.DataFrame([dict(date=nav.date.min()-pd.Timedelta(days=1),nav=1.,cash=1.,positions=0,dividend_receivable=0.)])
    pd.concat([leading,nav],ignore_index=True).to_csv(path,index=False)
    checks=replay.compare_prefixes(source,target)
    assert checks[0]['leading_cash_sessions']==1 and checks[0]['exact_match']


def test_leading_cash_exception_cannot_hide_an_actual_return(tmp_path):
    source,target=prefixes(tmp_path)
    path=target/'5d/partial_valid_nav.csv';nav=pd.read_csv(path,parse_dates=['date'])
    leading=pd.DataFrame([dict(date=nav.date.min()-pd.Timedelta(days=1),nav=1.001,cash=1.,positions=0,dividend_receivable=0.)])
    pd.concat([leading,nav],ignore_index=True).to_csv(path,index=False)
    with pytest.raises(ValueError,match='untouched initial cash'):replay.compare_prefixes(source,target)

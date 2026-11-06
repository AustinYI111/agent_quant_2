import sqlite3
import pandas as pd
import pytest
from ashare_mvp.data import Dataset
from ashare_mvp.model import Config
from ashare_mvp.timing_account import simulate_timing
from ashare_mvp.timing_policy import CachedTimingSignals
from ashare_mvp.timing_verification import verify_timing_intents
from ashare_mvp.timing_account_audit import verify_st_fills
from ashare_mvp.accounting_attribution import attribute_open_account


def run(tmp_path,group):
    cal=pd.bdate_range('2023-01-02',periods=8);symbol='sh.600001'
    p=pd.DataFrame(dict(date=cal,symbol=symbol,open=10.,close=10.,preclose=10.,tradestatus='1'))
    prices=p[['date','symbol']].copy();prices['can_buy']=True;prices['can_sell']=True;prices['total_return_open']=10.
    picks=pd.DataFrame(dict(date=[cal[0]],symbol=[symbol],target_weight=1.,available_date=cal[0]))
    actions=pd.DataFrame(columns=['symbol','ex_date','available_date','pay_date','cash_per_share','share_multiplier'])
    states=pd.DataFrame(dict(date=cal,index_above_ma200=[0.,1.,1.,1.,0.,1.,1.,1.]))
    file=tmp_path/'box.sqlite';con=sqlite3.connect(file);con.execute('CREATE TABLE timing(symbol TEXT,date TEXT,box_entry INTEGER,box_exit INTEGER,box_signal_known INTEGER,PRIMARY KEY(symbol,date)) WITHOUT ROWID')
    con.executemany('INSERT INTO timing VALUES(?,?,?,?,?)',[(symbol,str(d.date()),1,0,1) for d in cal]);con.commit()
    reader=CachedTimingSignals(file,states)
    try:nav,orders,decisions=simulate_timing(Dataset(prices,pd.DataFrame(),cal,{}),picks,Config(top_k=1),reader,group,p,actions)
    finally:reader.close()
    daily,positions,assets=attribute_open_account(nav,orders,p,actions)
    return cal,picks,decisions,orders,positions,states,con


@pytest.mark.parametrize('group',['market_state','oneil_box','combined'])
def test_independent_raw_flag_replay_matches_producer_without_policy_helper(tmp_path,group):
    args=run(tmp_path,group)
    try:proof=verify_timing_intents(*args,group)
    finally:args[-1].close()
    assert proof['decision_sessions']==7 and proof['independent_prior_close_intents_exact']


@pytest.mark.parametrize('tamper',['clock','pending','daily_trade','original_signal'])
def test_independent_check_rejects_lookahead_intent_drift_daily_churn_and_signal_changes(tmp_path,tamper):
    args=list(run(tmp_path,'market_state'));cal,picks,decisions,orders,positions,states,con=args
    if tamper=='clock':decisions.loc[0,'decision_date']=decisions.loc[0,'execution_date']
    elif tamper=='pending':decisions.at[1,'pending_entries']=[]
    elif tamper=='daily_trade':
        fake=orders.iloc[[0]].copy();fake['date']=cal[3];args[3]=pd.concat([orders,fake],ignore_index=True)
    else:states.loc[states.date.eq(cal[1]),'index_above_ma200']=0.
    try:
        with pytest.raises(ValueError):verify_timing_intents(*args,'market_state')
    finally:con.close()


@pytest.mark.parametrize('status',['True',None])
def test_independent_st_check_rejects_filled_st_or_missing_status(tmp_path,status):
    file=tmp_path/'flags.csv';date=pd.Timestamp('2023-01-02')
    pd.DataFrame(dict(date=[date],symbol=['sh.600001'],is_st=[status])).to_csv(file,index=False)
    orders=pd.DataFrame(dict(date=[date],symbol=['sh.600001'],side=['buy'],status=['filled'],notional=[.5]))
    with pytest.raises(ValueError):verify_st_fills(orders,file)

import ast
import hashlib
import inspect
import sqlite3
import numpy as np
import pandas as pd
import pytest
from ashare_mvp.data import Dataset
from ashare_mvp.model import Config
from ashare_mvp.pipeline import simulate_monthly
from ashare_mvp.timing_account import simulate_timing,_simulate_raw_timing,RANKING_ENGINE_FUNCTION_SHA256
from ashare_mvp.timing_policy import CloseTimingPolicy,CachedTimingSignals
from ashare_mvp.causal_quality import HeldValuationError


class Reader:
    def __init__(self,flags=None):self.flags=flags or {};self.reads=[]
    def read(self,date,symbols,group):
        self.reads.append((date,set(symbols)))
        return pd.DataFrame([dict(date=date,symbol=s,entry=self.flags.get((date,s),(True,False))[0],exit=self.flags.get((date,s),(True,False))[1]) for s in sorted(symbols)])


def fixture(kind='normal'):
    cal=pd.bdate_range('2023-01-02',periods=8);names=['sh.600001','sh.600002']
    raw=pd.DataFrame([dict(date=d,symbol=s,open=10.,close=10.,preclose=10.,tradestatus='1',terminal_date=pd.NaT) for d in cal for s in names])
    actions=pd.DataFrame(columns=['symbol','ex_date','available_date','pay_date','cash_per_share','share_multiplier'])
    if kind=='dividend':
        raw.loc[raw.date.ge(cal[3]),['open','close','preclose']]=9.
        actions=pd.DataFrame([dict(symbol=s,ex_date=cal[3],available_date=cal[0],pay_date=cal[4],cash_per_share=1.,share_multiplier=1.) for s in names])
    elif kind=='blocked':raw.loc[raw.date.eq(cal[1])&raw.symbol.eq(names[1]),'tradestatus']='0'
    elif kind=='reference':raw.loc[raw.date.eq(cal[3])&raw.symbol.eq(names[1]),['open','close','preclose']]=1.
    elif kind=='terminal':raw.loc[raw.date.eq(cal[3])&raw.symbol.eq(names[1]),'terminal_date']=cal[3]
    prices=raw[['date','symbol']].copy();prices['total_return_open']=raw.open;prices['can_buy']=raw.tradestatus.eq('1');prices['can_sell']=prices.can_buy
    picks=pd.DataFrame(dict(date=[cal[0]]*2,symbol=names,target_weight=.5,available_date=cal[0]))
    return Dataset(prices,pd.DataFrame(),cal,{}),picks,raw,actions,Config(top_k=2)


def test_fork_accounting_body_is_exactly_the_original_except_target_callback():
    original=inspect.getsource(simulate_monthly)
    assert hashlib.sha256(original.encode()).hexdigest()==RANKING_ENGINE_FUNCTION_SHA256
    fork=inspect.getsource(_simulate_raw_timing).replace('def _simulate_raw_timing(','def simulate_monthly(').replace('causal_quality_research=False, controller=None):','causal_quality_research=False):')
    fork=fork.replace('        targets = orders.get(date) if controller is None else controller.targets(date, holdings, prices, equity)\n        if targets is not None:\n','        if date in orders:\n            targets = orders[date]\n')
    assert ast.dump(ast.parse(fork))==ast.dump(ast.parse(original))


@pytest.mark.parametrize('kind',['normal','blocked','dividend'])
def test_no_policy_fork_matches_sealed_ranking_nav_cash_orders_and_costs_exactly(kind):
    data,picks,raw,actions,c=fixture(kind)
    a,b=simulate_monthly(data,picks,c,raw,actions,.01,True)
    x,y=_simulate_raw_timing(data,picks,c,raw,actions,.01,True)
    pd.testing.assert_frame_equal(a,x);pd.testing.assert_frame_equal(b,y)


@pytest.mark.parametrize('kind',['reference','terminal'])
def test_faulting_positive_holdings_preserve_partial_ledger_and_timing_decisions(kind):
    data,picks,raw,actions,c=fixture(kind)
    with pytest.raises(HeldValuationError) as caught:simulate_timing(data,picks,c,Reader(),'market_state',raw,actions)
    e=caught.value;assert e.reason==kind and e.symbol=='sh.600002' and e.holdings[e.symbol]>0
    assert len(e.nav)==2 and len(e.timing_decisions)==2


def test_signal_at_close_executes_only_next_open_and_unchanged_holdings_do_not_rebalance_daily():
    data,picks,raw,actions,c=fixture();cal=data.calendar
    flags={(cal[0],s):(False,True) for s in picks.symbol}
    flags.update({(cal[4],s):(False,True) for s in picks.symbol})
    nav,orders,trace=simulate_timing(data,picks,c,Reader(flags),'market_state',raw,actions)
    assert orders.loc[orders.side.eq('buy'),'date'].iloc[0]==cal[2]
    assert set(orders.loc[orders.side.eq('sell'),'date'])=={cal[5]}
    assert len(orders)==6 # reentry on cal[6], after a new on-close signal
    assert nav.loc[nav.date.eq(cal[1]),'nav'].iloc[0]==1
    assert (pd.to_datetime(trace.execution_date)>pd.to_datetime(trace.decision_date)).all()


def test_persisted_blocked_buy_and_sell_retry_and_do_not_rebalance_other_holdings():
    data,picks,raw,actions,c=fixture('blocked');cal=data.calendar;s='sh.600002'
    # First buy blocked, next open fills from the original valid intent.
    flags={(cal[1],s):(False,False),(cal[2],s):(False,True)}
    mask=data.prices.date.eq(cal[3])&data.prices.symbol.eq(s);data.prices.loc[mask,'can_sell']=False
    nav,orders,trace=simulate_timing(data,picks,c,Reader(flags),'oneil_box',raw,actions)
    sb=orders[orders.symbol.eq(s)]
    assert sb.iloc[0].status=='blocked' and sb.iloc[1].date==cal[2] and sb.iloc[1].side=='buy'
    assert sb[sb.side.eq('sell')].iloc[0].status=='blocked'
    assert sb[sb.side.eq('sell')].iloc[1].date==cal[4]
    assert len(orders[orders.symbol.eq('sh.600001')])==1


def test_unknown_entry_stays_cash_unknown_exit_preserves_old_hold_and_no_final_sale():
    data,picks,raw,actions,c=fixture();cal=data.calendar
    flags={(d,'sh.600002'):(pd.NA,pd.NA) for d in cal}
    flags.update({(d,'sh.600001'):(False,pd.NA) for d in cal[1:]})
    nav,orders,trace=simulate_timing(data,picks,c,Reader(flags),'combined',raw,actions)
    assert len(orders)==1 and orders.side.iloc[0]=='buy' and nav.positions.eq(1).all()
    assert nav.cash.iloc[-1]==pytest.approx(.4996)


def test_timing_keeps_raw_cash_receivable_dividend_accounting():
    data,picks,raw,actions,c=fixture('dividend')
    nav,orders,trace=simulate_timing(data,picks,c,Reader(),'market_state',raw,actions)
    np.testing.assert_allclose(nav.nav,1/1.0008,atol=1e-12)
    assert nav.dividend_receivable.gt(0).sum()==2 and len(orders)==2


def test_scheduled_top20_refresh_sells_removed_name_at_following_open():
    data,picks,raw,actions,c=fixture();cal=data.calendar
    # One-name policy, a second scheduled ranking replaces the intended issuer.
    picks=picks.iloc[:1].copy();picks.target_weight=1.
    later=picks.copy();later.date=cal[3];later.symbol='sh.600002'
    picks=pd.concat([picks,later],ignore_index=True)
    nav,orders,trace=simulate_timing(data,picks,Config(top_k=1),Reader(),'market_state',raw,actions)
    assert orders[orders.side.eq('sell')].date.tolist()==[cal[4]]
    assert orders[orders.side.eq('buy')].symbol.tolist()==['sh.600001','sh.600002']


@pytest.mark.parametrize('bad',['future_signal','missing_issuer','integer_signal','industry','future_rank'])
def test_invisible_or_ambiguous_policy_inputs_rejected(bad):
    data,picks,raw,actions,c=fixture();r=Reader();group='market_state'
    if bad=='industry':group='industry'
    elif bad=='future_rank':picks.available_date=data.calendar[-1]
    else:
        orig=r.read
        def read(date,symbols,group):
            t=orig(date,symbols,group)
            if bad=='future_signal':t.date+=pd.Timedelta(days=1)
            elif bad=='missing_issuer':t=t.iloc[:1]
            else:t['entry']=1
            return t
        r.read=read
    with pytest.raises(ValueError):simulate_timing(data,picks,c,r,group,raw,actions)


def test_cached_reader_is_read_only_and_combines_unknowns_without_future_rows(tmp_path):
    path=tmp_path/'timing.sqlite';con=sqlite3.connect(path)
    con.execute('CREATE TABLE timing(symbol TEXT,date TEXT,box_entry INTEGER,box_exit INTEGER,box_signal_known INTEGER,PRIMARY KEY(symbol,date)) WITHOUT ROWID')
    con.executemany('INSERT INTO timing VALUES(?,?,?,?,?)',[('sh.600001','2023-01-02',1,0,1),('sh.600001','2023-01-03',0,1,1)])
    con.commit();con.close()
    market=pd.DataFrame(dict(date=pd.to_datetime(['2023-01-02','2023-01-03']),index_above_ma200=[np.nan,1.]))
    r=CachedTimingSignals(path,market)
    try:
        first=r.read('2023-01-02',{'sh.600001'},'combined')
        assert not first.entry.iloc[0] and pd.isna(first.exit.iloc[0])
        later=r.read('2023-01-03',{'sh.600001'},'combined');assert later.exit.iloc[0]
        with pytest.raises(sqlite3.OperationalError):r.connection.execute('DELETE FROM timing')
    finally:r.close()

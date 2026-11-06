import json,sqlite3
import numpy as np
import pandas as pd
import pytest
from ashare_mvp.disk_market import build_quote_store,build_reference_audit,QuoteStore,ReferenceAudit
from ashare_mvp.disk_account import simulate_disk_account,DiskHeldValuationError,disk_account_metrics
from ashare_mvp.pipeline import simulate_monthly
from ashare_mvp.horizon_research import compare_index
from ashare_mvp.data import Dataset
from ashare_mvp.model import Config
from ashare_mvp.causal_quality import HeldValuationError,quality_inputs


def inputs(tmp_path,kind='normal',long=False):
    cal=pd.bdate_range('2023-01-02','2024-02-01') if long else pd.bdate_range('2023-08-21',periods=16)
    names=['sh.600001','sh.600002','sh.600003'];rng=np.random.default_rng(42)
    records=[];events=[]
    for s in names:
        previous=10.
        for i,d in enumerate(cal):
            pre=previous
            if kind=='dividend' and i==5:
                pre-=.7;events.append(dict(symbol=s,ex_date=d,available_date=cal[0],pay_date=cal[8],cash_per_share=.7,share_multiplier=1.))
            if kind=='split' and i==5:
                pre/=2;events.append(dict(symbol=s,ex_date=d,available_date=cal[0],pay_date=d,cash_per_share=0.,share_multiplier=2.))
            op=pre*(1+rng.normal(0,.008));close=op*(1+rng.normal(0,.007));status='1';can_buy=can_sell=True
            if kind=='suspension' and s==names[0] and i in [1,5,6]:op=0.;close=pre;status='0'
            if kind=='blocked_sale' and s==names[0] and i in [5,6]:can_sell=False
            terminal=cal[6] if kind=='terminal' and s==names[0] and i==6 else pd.NaT
            if kind=='reference' and s==names[0] and i==6:pre+=1.
            if not (kind=='missing' and s==names[0] and i==6):records.append(dict(date=d,symbol=s,open=op,close=close,preclose=pre,tradestatus=status,can_buy=can_buy,can_sell=can_sell,terminal_date=terminal,reference_fault=False))
            previous=close
    raw=pd.DataFrame(records);actions=pd.DataFrame(events,columns=['symbol','ex_date','available_date','pay_date','cash_per_share','share_multiplier'])
    picks=pd.DataFrame([dict(date=d,symbol=s,target_weight=.5,available_date=cal[0]) for d,pool in [(cal[0],names[:2]),(cal[4],names[1:]),(cal[10],names[:2])] for s in pool])
    if long:picks=picks[picks.date.eq(cal[0])]
    if kind=='missing_buy':picks.loc[picks.date.eq(cal[4])&picks.symbol.eq(names[2]),'symbol']='sh.600099'
    raw.to_csv(tmp_path/'raw.csv',index=False);actions.to_csv(tmp_path/'actions.csv',index=False);picks.to_csv(tmp_path/'picks.csv',index=False)
    # Reference implementation reads the exact same round-tripped source bytes.
    raw=pd.read_csv(tmp_path/'raw.csv',parse_dates=['date','terminal_date'],dtype={'tradestatus':str},float_precision='round_trip')
    actions=pd.read_csv(tmp_path/'actions.csv',parse_dates=['ex_date','available_date','pay_date'],float_precision='round_trip')
    picks=pd.read_csv(tmp_path/'picks.csv',parse_dates=['date','available_date'],float_precision='round_trip')
    prices=raw[['date','symbol','can_buy','can_sell']].copy();prices['total_return_open']=raw.open
    return Dataset(prices,pd.DataFrame(),cal,{}),picks,raw,actions,Config(top_k=2)


def build(tmp_path,data):
    m=build_quote_store(tmp_path/'raw.csv',data.calendar,tmp_path/'quotes',chunksize=7)
    a=build_reference_audit(tmp_path/'quotes',tmp_path/'raw.csv',tmp_path/'actions.csv',tmp_path/'references')
    store=QuoteStore(tmp_path/'quotes');reference=ReferenceAudit(tmp_path/'references',store)
    return store,reference,m,a


def read_results(folder):
    n=pd.read_csv(folder/'daily_nav.csv',parse_dates=['date'],float_precision='round_trip')
    t=pd.read_csv(folder/'orders.csv',parse_dates=['date'],float_precision='round_trip')
    return n,t


@pytest.mark.parametrize('kind',['normal','dividend','split','suspension','blocked_sale','missing_buy'])
def test_streamed_account_matches_original_shares_cash_costs_and_every_order(kind,tmp_path):
    data,picks,raw,actions,c=inputs(tmp_path,kind);store,reference,m,a=build(tmp_path,data)
    try:proof=simulate_disk_account(store,reference,tmp_path/'picks.csv',data.calendar,c,tmp_path/'account')
    finally:reference.close();store.close()
    expected_nav,expected_orders=simulate_monthly(data,picks,c,raw,actions,.01,True);nav,orders=read_results(tmp_path/'account')
    pd.testing.assert_frame_equal(nav,expected_nav,check_exact=True)
    pd.testing.assert_frame_equal(orders,expected_orders,check_exact=True)
    assert proof['sessions']==len(data.calendar)-1 and proof['max_day_quotes']<=3 and proof['max_day_orders']<=6 and not proof['history_retained_in_memory']
    benchmark=pd.DataFrame(dict(date=data.calendar,code='sh.000300',open=np.linspace(10.,11.,len(data.calendar))))
    expected_comparison,expected_metrics=compare_index(expected_nav,expected_orders,benchmark,'price_index')
    comparison,metrics=disk_account_metrics(tmp_path/'account',benchmark)
    pd.testing.assert_frame_equal(comparison,expected_comparison,check_exact=True)
    assert metrics==expected_metrics


@pytest.mark.parametrize('kind',['reference','missing','terminal'])
def test_unknown_positive_holding_stops_with_identical_flushed_prefix_and_held_shares(kind,tmp_path):
    data,picks,raw,actions,c=inputs(tmp_path,kind)
    # Keep first issuer held across the failure instead of replacing it.
    picks=picks[picks.date.eq(data.calendar[0])];picks.to_csv(tmp_path/'picks.csv',index=False)
    store,reference,m,a=build(tmp_path,data)
    try:
        with pytest.raises(DiskHeldValuationError) as actual:simulate_disk_account(store,reference,tmp_path/'picks.csv',data.calendar,c,tmp_path/'account')
    finally:reference.close();store.close()
    with pytest.raises(HeldValuationError) as expected:simulate_monthly(data,picks,c,raw,actions,.01,True)
    x,y=actual.value,expected.value;assert (x.symbol,x.date,x.reason)==(y.symbol,y.date,y.reason) and x.holdings==y.holdings
    nav,orders=read_results(tmp_path/'account');pd.testing.assert_frame_equal(nav,y.nav,check_exact=True);pd.testing.assert_frame_equal(orders,y.trades,check_exact=True)
    assert x.nav_rows==len(nav) and x.order_rows==len(orders) and x.holdings[x.symbol]>0


def test_full_twelve_month_calendar_is_not_replaced_with_a_smaller_prefix(tmp_path):
    data,picks,raw,actions,c=inputs(tmp_path,long=True);store,reference,m,a=build(tmp_path,data)
    try:proof=simulate_disk_account(store,reference,tmp_path/'picks.csv',data.calendar,c,tmp_path/'account')
    finally:reference.close();store.close()
    nav,orders=read_results(tmp_path/'account');assert pd.Timestamp(proof['requested_end'])>=nav.date.iloc[0]+pd.DateOffset(months=12)
    assert nav.date.iloc[-1]==data.calendar[-1] and not orders.side.eq('sell').any()


def test_recomputed_reference_audit_ignores_stale_raw_quality_flags(tmp_path):
    data,picks,raw,actions,c=inputs(tmp_path,'reference');store,reference,m,a=build(tmp_path,data)
    try:
        expected,_,_=quality_inputs(raw,actions,data.calendar,.01)
        keys={(str(d.date()),s) for d,s in zip(expected.loc[expected.reference_fault,'date'],expected.loc[expected.reference_fault,'symbol'])}
        actual=set(reference.connection.execute('SELECT date,symbol FROM faults'))
        assert actual==keys and len(actual)==1 and not raw.reference_fault.any()
        with pytest.raises(sqlite3.OperationalError):store.connection.execute('DELETE FROM quotes')
    finally:reference.close();store.close()


@pytest.mark.parametrize('bad',['duplicate','unsorted_issuer','invalid_price','unknown_status','invalid_terminal'])
def test_invalid_quotes_never_produce_a_ready_store(tmp_path,bad):
    data,picks,raw,actions,c=inputs(tmp_path)
    if bad=='duplicate':raw=pd.concat([raw.iloc[:1],raw],ignore_index=True)
    elif bad=='unsorted_issuer':raw=raw.iloc[::-1]
    elif bad=='invalid_price':raw.loc[0,'close']=np.nan
    elif bad=='unknown_status':raw.loc[0,'tradestatus']='unknown'
    else:raw.loc[0,'terminal_date']=pd.Timestamp('2023-08-22 13:00')
    raw.to_csv(tmp_path/'raw.csv',index=False)
    with pytest.raises((ValueError,sqlite3.IntegrityError)):build_quote_store(tmp_path/'raw.csv',data.calendar,tmp_path/'quotes',chunksize=7)
    manifest=json.loads((tmp_path/'quotes/manifest.json').read_text(encoding='utf8'));assert manifest['status']=='failed'
    assert not (tmp_path/'quotes/quotes.sqlite').exists()


def test_changed_quote_database_is_rejected_instead_of_reusing_ready_manifest(tmp_path):
    data,picks,raw,actions,c=inputs(tmp_path);store,reference,m,a=build(tmp_path,data);reference.close();store.close()
    con=sqlite3.connect(tmp_path/'quotes/quotes.sqlite');con.execute('UPDATE quotes SET open=open+1');con.commit();con.close()
    with pytest.raises(ValueError,match='unchanged'):QuoteStore(tmp_path/'quotes')


@pytest.mark.parametrize('bad',['future_availability','duplicate_target','leverage','future_calendar'])
def test_invisible_or_ambiguous_schedule_is_rejected_before_execution(tmp_path,bad):
    data,picks,raw,actions,c=inputs(tmp_path);store,reference,m,a=build(tmp_path,data);cal=data.calendar
    if bad=='future_availability':picks.loc[0,'available_date']=cal[-1]
    elif bad=='duplicate_target':picks=pd.concat([picks.iloc[:1],picks],ignore_index=True)
    elif bad=='leverage':picks.target_weight=1.
    else:cal=cal.append(pd.DatetimeIndex([cal[-1]+pd.offsets.BDay()]))
    picks.to_csv(tmp_path/'picks.csv',index=False)
    try:
        with pytest.raises(ValueError):simulate_disk_account(store,reference,tmp_path/'picks.csv',cal,c,tmp_path/'account')
    finally:reference.close();store.close()


@pytest.mark.parametrize('kind',['normal','dividend','split','suspension','blocked_sale','missing_buy'])
def test_independent_disk_audit_reconstructs_cash_shares_receivables_and_dated_costs(tmp_path,kind):
    from ashare_mvp.disk_account_audit import reconstruct_disk_account
    data,picks,raw,actions,c=inputs(tmp_path,kind);store,reference,m,a=build(tmp_path,data)
    try:
        simulate_disk_account(store,reference,tmp_path/'picks.csv',data.calendar,c,tmp_path/'account')
        proof=reconstruct_disk_account(store,reference,tmp_path/'account',data.calendar[1],data.calendar[-1],tmp_path/'audit')
    finally:reference.close();store.close()
    assert proof['full_period_performance_verified'] and proof['sessions']==len(data.calendar)-1 and proof['max_absolute_reconciliation_error']<1e-12


@pytest.mark.parametrize('kind',['reference','missing','terminal'])
def test_independent_disk_audit_verifies_every_partial_row_and_actual_held_shares(tmp_path,kind):
    from ashare_mvp.disk_account_audit import reconstruct_disk_account
    data,picks,raw,actions,c=inputs(tmp_path,kind);picks=picks[picks.date.eq(data.calendar[0])];picks.to_csv(tmp_path/'picks.csv',index=False)
    store,reference,m,a=build(tmp_path,data)
    try:
        with pytest.raises(DiskHeldValuationError) as caught:simulate_disk_account(store,reference,tmp_path/'picks.csv',data.calendar,c,tmp_path/'account')
        for before,after in [('daily_nav.csv','partial_valid_nav.csv'),('orders.csv','partial_orders.csv')]: (tmp_path/'account'/before).rename(tmp_path/'account'/after)
        e=caught.value;proof=reconstruct_disk_account(store,reference,tmp_path/'account',data.calendar[1],data.calendar[-1],tmp_path/'audit',e.date,e.holdings)
    finally:reference.close();store.close()
    assert not proof['full_period_performance_verified'] and proof['holdings_at_stop_checked'] and proof['sessions']==5


@pytest.mark.parametrize('tamper',['order','nav','missing_day','stamp','totals'])
def test_independent_disk_audit_rejects_altered_money_or_dropped_sessions(tmp_path,tamper):
    from ashare_mvp.disk_account_audit import reconstruct_disk_account
    data,picks,raw,actions,c=inputs(tmp_path);store,reference,m,a=build(tmp_path,data)
    try:
        simulate_disk_account(store,reference,tmp_path/'picks.csv',data.calendar,c,tmp_path/'account')
        if tamper in ['order','stamp']:
            file=tmp_path/'account/orders.csv';t=pd.read_csv(file,float_precision='round_trip')
            if tamper=='order':t.loc[0,'notional']*=1.1
            else:t.loc[0,'stamp_duty']=.001
        elif tamper=='totals':
            file=tmp_path/'account/daily_execution_totals.csv';t=pd.read_csv(file,float_precision='round_trip');t.loc[0,'notional']+=.01
        else:
            file=tmp_path/'account/daily_nav.csv';t=pd.read_csv(file,float_precision='round_trip')
            if tamper=='nav':t.loc[0,'cash']+=.01
            else:t=t.iloc[1:]
        t.to_csv(file,index=False)
        with pytest.raises(ValueError):reconstruct_disk_account(store,reference,tmp_path/'account',data.calendar[1],data.calendar[-1],tmp_path/'audit')
    finally:reference.close();store.close()


@pytest.mark.parametrize('tamper',[False,True])
def test_old_prefix_check_also_rejects_an_extra_trade_after_last_old_trade_before_prefix_end(tmp_path,tamper):
    from ashare_mvp.disk_account_audit import prefix_matches
    old=tmp_path/'old';new=tmp_path/'new';old.mkdir();new.mkdir()
    nav=pd.DataFrame(dict(date=['2023-01-03','2023-01-04'],nav=[.9992,1.],cash=[0.,0.],positions=[1,1],dividend_receivable=[0.,0.],return_basis=['raw']*2))
    trade=pd.DataFrame(dict(date=['2023-01-03'],symbol=['sh.600001'],side=['buy'],notional=[.9992],stamp_duty_bps=[0.],stamp_duty=[0.],cost=[.0008],status=['filled']))
    for p in [old,new]:nav.to_csv(p/'partial_valid_nav.csv',index=False);trade.to_csv(p/'partial_orders.csv',index=False)
    assert prefix_matches(old,new)['old_valid_prefix_exact']
    if tamper:
        extra=trade.copy();extra.date='2023-01-04';extra.side='sell';pd.concat([trade,extra]).to_csv(new/'partial_orders.csv',index=False)
        with pytest.raises(ValueError,match='extra new trade'):prefix_matches(old,new)

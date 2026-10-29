"""Independent timing-intent audit, not the production policy or signal reader."""
import numpy as np
import pandas as pd


def verify_timing_intents(calendar,picks,decisions,orders,positions,market_states,box_connection,group):
    if group not in ['market_state','oneil_box','combined']:raise ValueError('unspecified timing group')
    cal=pd.DatetimeIndex(calendar);d=decisions.copy();d['execution_date']=pd.to_datetime(d.execution_date);d['decision_date']=pd.to_datetime(d.decision_date)
    if d.empty or d.execution_date.duplicated().any() or not d.execution_date.is_monotonic_increasing:raise ValueError('complete ordered timing decisions required')
    desired={};pending_buy=set();pending_exit=set();held=set();last_open=None
    daily_positions={day:set(g.loc[g.shares.gt(1e-12),'symbol']) for day,g in positions.groupby('date')}
    schedules={day:set(g.symbol) for day,g in picks.groupby('date')}
    states=market_states.set_index('date').index_above_ma200
    count=0
    for r in d.itertuples(index=False):
        ix=cal.get_indexer([r.execution_date])[0]
        if ix<=0 or cal[ix-1]!=r.decision_date:raise ValueError('signal was not the previous exchange close')
        if last_open is not None and cal.get_loc(r.execution_date)!=cal.get_loc(last_open)+1:raise ValueError('missing timing decision session')
        refresh=r.decision_date in schedules
        if refresh:desired=schedules[r.decision_date]
        pending_buy=pending_buy.intersection(desired).difference(held)
        pending_exit=pending_exit.intersection(held).union(held.difference(desired))
        keys=set(desired).union(held);market=states.get(r.decision_date,np.nan)
        for s in keys:
            entry=False;exit_=False
            if group!='market_state':
                rows=box_connection.execute('SELECT box_entry,box_exit,box_signal_known FROM timing WHERE symbol=? AND date=?',(s,str(r.decision_date.date()))).fetchall()
                if len(rows)>1:raise ValueError('ambiguous original box key')
                be,bx,known=rows[0] if rows else (0,None,0)
                if be not in [0,1] or bx not in [0,1,None] or known not in [0,1] or be and not known:raise ValueError('invalid original box flag')
                entry=be==1 and known==1;exit_=bx==1
            if group=='market_state':entry=pd.notna(market) and market==1;exit_=pd.notna(market) and market==0
            elif group=='combined':entry=entry and pd.notna(market) and market==1;exit_=exit_ or pd.notna(market) and market==0
            if exit_:
                pending_buy.discard(s)
                if s in held:pending_exit.add(s)
            elif s in desired and s not in held and entry:pending_buy.add(s)
        if bool(r.scheduled_refresh)!=refresh or r.intended_names!=len(desired) or r.positive_holdings!=len(held) or sorted(r.pending_entries)!=sorted(pending_buy) or sorted(r.pending_exits)!=sorted(pending_exit) or bool(r.order_requested)!=(refresh or bool(pending_buy) or bool(pending_exit)):raise ValueError('stored timing intent differs from independent replay')
        today=orders[orders.date.eq(r.execution_date)] if not orders.empty else orders
        for o in today.itertuples(index=False):
            if not r.order_requested:raise ValueError('order on no-request day')
            if o.side=='buy' and o.notional>1e-12 and o.symbol not in pending_buy and not (refresh and o.symbol in held.intersection(desired).difference(pending_exit)):raise ValueError('buy not authorized by close intent or scheduled resizing')
            if o.side=='sell' and o.notional>1e-12 and o.symbol not in pending_exit and not (refresh and o.symbol in held.intersection(desired)):raise ValueError('sale not authorized by close exit or scheduled resizing')
        held=daily_positions.get(r.execution_date,set());last_open=r.execution_date;count+=1
    return dict(decision_sessions=count,independent_prior_close_intents_exact=True)

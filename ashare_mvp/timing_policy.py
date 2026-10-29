"""Causal close-decision policy, separate from raw-share accounting.

Operational clarification dated 2026-10-28, not a rewrite of the original seal:
between fixed Top20 refreshes a flat intended name may enter on a fresh signal;
blocked orders persist until filled, explicit exit or universe removal cancels
pending buys; exits persist until flat. Holdings resize only at scheduled refresh.
Industry has no prespecified standalone timing rule and is deliberately rejected.
"""
from pathlib import Path
import sqlite3
import numpy as np
import pandas as pd
from .public_data import is_ashare


def _calendar(values):
    c=pd.DatetimeIndex(pd.to_datetime(values))
    if c.empty or c.hasnans or c.has_duplicates or not c.is_monotonic_increasing or c.tz is not None or not c.equals(c.normalize()):
        raise ValueError('ordered unique normalized exchange calendar required')
    return c


class CachedTimingSignals:
    """Read only the requested decision close and requested issuers, never a join
    to future outcomes. The account producer must also bind original file hashes.
    """
    def __init__(self,database,market_states):
        self.market=market_states[['date','index_above_ma200']].copy()
        self.market['date']=pd.to_datetime(self.market.date)
        _calendar(self.market.date)
        if not self.market.index_above_ma200.dropna().isin([0.,1.]).all():raise ValueError('binary or unknown market state required')
        self.market=self.market.set_index('date').index_above_ma200
        self.connection=sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)
        self.connection.execute('PRAGMA query_only=ON')

    def close(self):self.connection.close()

    def read(self,date,symbols,group):
        if group not in ['market_state','oneil_box','combined']:raise ValueError('prespecified technical timing group required')
        date=pd.Timestamp(date);symbols=sorted(symbols)
        market=self.market.get(date,np.nan)
        rows={}
        if symbols and group!='market_state':
            query='SELECT symbol,box_entry,box_exit,box_signal_known FROM timing WHERE date=? AND symbol IN ('+','.join('?' for _ in symbols)+')'
            rows={s:(entry,exit_,known) for s,entry,exit_,known in self.connection.execute(query,[str(date.date())]+symbols)}
        result=[]
        for s in symbols:
            entry,exit_,known=rows.get(s,(0,None,0))
            if entry not in [0,1] or exit_ not in [0,1,None] or known not in [0,1]:raise ValueError('invalid cached box signal')
            if entry and not known:raise ValueError('unknown box inputs cannot create entry')
            box_entry=bool(entry and known);box_exit=pd.NA if exit_ is None else bool(exit_)
            market_entry=bool(pd.notna(market) and market==1)
            market_exit=pd.NA if pd.isna(market) else bool(market==0)
            if group=='market_state':en,ex=market_entry,market_exit
            elif group=='oneil_box':en,ex=box_entry,box_exit
            else:
                en=market_entry and box_entry
                ex=True if market_exit is True or box_exit is True else (pd.NA if pd.isna(market_exit) or pd.isna(box_exit) else False)
            result.append(dict(date=date,symbol=s,entry=en,exit=ex))
        return pd.DataFrame(result,columns=['date','symbol','entry','exit'])


class CloseTimingPolicy:
    def __init__(self,calendar,picks,reader,group,top_k=20):
        self.calendar=_calendar(calendar)
        if group not in ['market_state','oneil_box','combined']:raise ValueError('industry timing was not specified; do not invent a rule')
        if isinstance(top_k,bool) or not isinstance(top_k,int) or top_k<1:raise ValueError('positive top_k required')
        p=picks[['date','symbol','target_weight']].copy();p['date']=pd.to_datetime(p.date)
        if p.empty or p[['date','symbol','target_weight']].isna().any().any() or p.duplicated(['date','symbol']).any() or not p.date.isin(self.calendar).all() or not p.symbol.map(is_ashare).all():raise ValueError('unique dated original A-share Top20 required')
        if not p.groupby('date').size().eq(top_k).all() or not np.isfinite(p.target_weight).all() or not np.allclose(p.target_weight,1/top_k,rtol=0,atol=1e-15):raise ValueError('fixed original equal Top20 weights required')
        if 'available_date' in picks:
            visible=pd.to_datetime(picks.available_date)
            if visible.isna().any() or visible.gt(pd.to_datetime(picks.date)).any():raise ValueError('unknown or future ranking availability')
        self.schedules={d:g.set_index('symbol').target_weight.to_dict() for d,g in p.groupby('date')}
        self.reader=reader;self.group=group;self.intended={};self.pending_buys=set();self.pending_exits=set();self.last_open=None;self.decisions=[]

    def targets(self,open_date,holdings,prices,equity):
        open_date=pd.Timestamp(open_date);i=self.calendar.get_indexer([open_date])[0]
        if i<=0 or self.last_open is not None and open_date<=self.last_open:raise ValueError('strictly ordered next exchange opens required')
        self.last_open=open_date;decision=self.calendar[i-1]
        refresh=decision in self.schedules
        if refresh:self.intended=self.schedules[decision].copy()
        held={s:q for s,q in holdings.items() if q>1e-12}
        self.pending_buys &= set(self.intended)-set(held)
        self.pending_exits &= set(held)
        self.pending_exits |= set(held)-set(self.intended)
        symbols=set(self.intended)|set(held)
        signals=self.reader.read(decision,symbols,self.group)
        required={'date','symbol','entry','exit'}
        if not required.issubset(signals) or signals.duplicated(['date','symbol']).any() or set(signals.symbol)!=symbols or not pd.to_datetime(signals.date).eq(decision).all():raise ValueError('signals must exactly match requested prior close and issuer keys')
        for r in signals.itertuples(index=False):
            for value in [r.entry,r.exit]:
                if not pd.isna(value) and not isinstance(value,(bool,np.bool_)):raise ValueError('boolean or unknown signal required')
            exit_=pd.notna(r.exit) and bool(r.exit)
            if exit_:
                self.pending_buys.discard(r.symbol)
                if r.symbol in held:self.pending_exits.add(r.symbol)
            elif r.symbol in self.intended and r.symbol not in held and pd.notna(r.entry) and bool(r.entry):self.pending_buys.add(r.symbol)
        if not np.isfinite(equity) or equity<=0:raise ValueError('positive finite timing equity required')
        targets={s:q*float(prices[s])/equity for s,q in held.items()}
        if refresh:
            for s in set(held)&set(self.intended)-self.pending_exits:targets[s]=self.intended[s]
        for s in self.pending_exits:targets[s]=0.
        for s in self.pending_buys:targets[s]=self.intended[s]
        issue=refresh or bool(self.pending_exits) or bool(self.pending_buys)
        self.decisions.append(dict(decision_date=str(decision.date()),execution_date=str(open_date.date()),scheduled_refresh=refresh,intended_names=len(self.intended),positive_holdings=len(held),pending_entries=sorted(self.pending_buys),pending_exits=sorted(self.pending_exits),order_requested=issue))
        return targets if issue else None

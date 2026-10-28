"""Cash-constrained rights subscriptions, isolated from stock order generation.

Registry uses settled shares after that session's open orders. Remaining rights
are funded pro rata from existing free cash at each subscription-window open,
before stock rebalance orders. No external capital, borrowing or free shares.
Deposits stay at cost until the result is visible and the stock is ex-rights;
successful locked claims then use the underlying raw mark until actually listed.
"""
import math
import numpy as np
import pandas as pd
from .phase2_protocol import day


RIGHTS_LEDGER_COLUMNS=['date','phase','event_id','symbol','kind','quantity','principal','fee','cash_delta']


class UnresolvedRightsOutcome(ValueError):
    def __init__(self,event_id,symbol,date):
        self.event_id=event_id;self.symbol=symbol;self.date=day(date)
        super().__init__(f'rights result/refund not visible: {event_id} {self.date.date()}')


class RightsWallet:
    def __init__(self,events,policy,subscription_commission_bps=3.):
        if policy not in ['cash_limited_subscribe','decline']:raise ValueError('explicit rights participation policy required')
        if not np.isfinite(subscription_commission_bps) or subscription_commission_bps<0:raise ValueError('known nonnegative subscription commission required')
        self.events=sorted(events,key=lambda e:e['event_id']);self.by_id={e['event_id']:e for e in self.events}
        if len(self.by_id)!=len(self.events):raise ValueError('duplicate rights event')
        self.policy=policy;self.fee_rate=subscription_commission_bps/10000
        self.entitlements={};self.subscribed={};self.claims={};self.lapsed=set();self.confirmed=set();self.last_open=None

    def subscription_rate(self,event):
        return float(event['subscription_fee_bps'])/10000 if event.get('subscription_fee_bps') is not None else self.fee_rate

    def _row(self,date,event,kind,quantity=0.,principal=0.,fee=0.,cash_delta=0.,phase='open'):
        return dict(date=str(date.date()),phase=phase,event_id=event['event_id'],symbol=event['symbol'],kind=kind,quantity=float(quantity),principal=float(principal),fee=float(fee),cash_delta=float(cash_delta))

    def _result_known(self,e,date):return e['result_available_date'] is not None and e['result_available_date']<=date

    def check_outcomes(self,date):
        date=day(date)
        for identifier,claim in self.claims.items():
            e=self.by_id[identifier]
            if date>=e['expected_result_available_date'] and (not self._result_known(e,date) or e['outcome'] is None):raise UnresolvedRightsOutcome(identifier,e['symbol'],date)
            if self._result_known(e,date) and e['outcome'] in ['failed','canceled'] and e['refund_date'] is None:raise UnresolvedRightsOutcome(identifier,e['symbol'],date)

    def valuation_symbols(self,date):
        date=day(date)
        return {self.by_id[i]['symbol'] for i,c in self.claims.items() if self.by_id[i]['outcome']=='success' and self._result_known(self.by_id[i],date) and date>=self.by_id[i]['ex_date'] and c['quantity']>0}

    def value(self,date,prices):
        date=day(date);values=[]
        for identifier,claim in self.claims.items():
            e=self.by_id[identifier]
            if e['outcome']=='success' and self._result_known(e,date) and date>=e['ex_date']:
                if e['symbol'] not in prices or not np.isfinite(prices[e['symbol']]) or prices[e['symbol']]<=0:raise ValueError('unknown positive locked-rights valuation')
                values.append(claim['quantity']*prices[e['symbol']])
            else:values.append(claim['principal'])
        return sum(values)

    def open(self,date,cash,settled_holdings,prices):
        date=day(date)
        if self.last_open is not None and date<=self.last_open:raise ValueError('strictly ordered rights-wallet opens required')
        if not np.isfinite(cash) or cash<0:raise ValueError('known nonnegative cash required')
        self.check_outcomes(date)
        # Validate every positive claim before any confirmation, unlock or cash
        # mutation. Missing prices must leave a replayable, unchanged prefix.
        self.value(date,prices)
        for symbol,quantity in settled_holdings.items():
            if not np.isfinite(quantity) or quantity<0:raise ValueError('known nonnegative settled shares required')
        self.last_open=date;rows=[];fees=0.
        for identifier,claim in list(self.claims.items()):
            e=self.by_id[identifier]
            if self._result_known(e,date) and e['outcome'] in ['failed','canceled']:
                if date>e['refund_date']:
                    cash+=claim['principal'];rows.append(self._row(date,e,'refund',claim['quantity'],claim['principal'],cash_delta=claim['principal']));del self.claims[identifier]
                continue
            if e['outcome']=='success' and self._result_known(e,date) and date>=e['ex_date']:
                if identifier not in self.confirmed:rows.append(self._row(date,e,'allocation_confirmed',claim['quantity'],claim['principal']));self.confirmed.add(identifier)
                if e['tradable_from'] is not None and e['listing_available_date'] is not None and date>=max(e['tradable_from'],e['listing_available_date']):
                    settled_holdings[e['symbol']]=settled_holdings.get(e['symbol'],0.)+claim['quantity'];rows.append(self._row(date,e,'shares_unlocked',claim['quantity'],claim['principal']));del self.claims[identifier]
        requests=[]
        for e in self.events:
            identifier=e['event_id']
            if e['offer_available_date']<=date and e['subscription_start']<=date<=e['subscription_end'] and self.policy=='cash_limited_subscribe':
                if self._result_known(e,date) and e['outcome'] in ['failed','canceled']:continue
                remaining=max(0.,self.entitlements.get(identifier,0.)-self.subscribed.get(identifier,0.))
                if remaining>0:requests.append((e,remaining,remaining*float(e['subscription_price'])*(1+self.subscription_rate(e))))
        required=math.fsum(cost for _,_,cost in requests);factor=min(1.,float(cash)/required) if required>0 else 0.
        for e,remaining,_ in requests:
            quantity=remaining*factor
            if quantity<=0:continue
            principal=quantity*float(e['subscription_price']);fee=principal*self.subscription_rate(e);identifier=e['event_id']
            claim=self.claims.setdefault(identifier,dict(quantity=0.,principal=0.));claim['quantity']+=quantity;claim['principal']+=principal;self.subscribed[identifier]=self.subscribed.get(identifier,0.)+quantity
            cash-=principal+fee;fees+=fee;rows.append(self._row(date,e,'subscribe',quantity,principal,fee,-principal-fee))
        if cash<-1e-9:raise AssertionError('rights subscription borrowed cash')
        cash=max(cash,0.)
        for e in self.events:
            identifier=e['event_id']
            if date>e['subscription_end'] and identifier not in self.lapsed:
                quantity=max(0.,self.entitlements.get(identifier,0.)-self.subscribed.get(identifier,0.))
                if quantity:rows.append(self._row(date,e,'rights_lapsed',quantity))
                self.lapsed.add(identifier)
        return cash,self.value(date,prices),fees,rows

    def close(self,date,settled_holdings):
        date=day(date)
        if self.last_open!=date:raise ValueError('registry close must follow the same account open')
        rows=[]
        for e in self.events:
            if e['record_date']==date:
                if e['offer_available_date']>date:raise ValueError('unavailable offer at registry')
                identifier=e['event_id']
                if identifier in self.entitlements:raise ValueError('registry already recorded')
                shares=settled_holdings.get(e['symbol'],0.)
                if not np.isfinite(shares) or shares<0:raise ValueError('known settled registry shares required')
                quantity=float(shares)*float(e['offer_ratio']);self.entitlements[identifier]=quantity;rows.append(self._row(date,e,'registry_entitlement',quantity,phase='close'))
        return rows

    def snapshot(self):
        return dict(policy=self.policy,subscription_commission_bps=self.fee_rate*10000,event_subscription_fee_bps={e['event_id']:self.subscription_rate(e)*10000 for e in self.events},registered_rights={k:float(v) for k,v in self.entitlements.items()},subscribed_rights={k:float(v) for k,v in self.subscribed.items()},locked_claims={k:dict(symbol=self.by_id[k]['symbol'],quantity=float(v['quantity']),principal=float(v['principal'])) for k,v in self.claims.items()})

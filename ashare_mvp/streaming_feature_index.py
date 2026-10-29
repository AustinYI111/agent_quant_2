"""Serializable causal feature-index state, never a portfolio valuation account.

Reference faults/gaps restart a feature-only segment exactly as causal_quality.
They must never reset or remove an actual holding, cash or receivable ledger.
"""
import hashlib
import math
import numpy as np
import pandas as pd

from .phase2_protocol import day
from .price_reference import reference_matches, validate_reference_tick


class ExchangeCalendar:
    def __init__(self, dates):
        self.dates = pd.DatetimeIndex(pd.to_datetime(dates))
        if (self.dates.empty or self.dates.tz is not None or self.dates.hasnans or self.dates.has_duplicates
                or not self.dates.is_monotonic_increasing or not self.dates.equals(self.dates.normalize())):
            raise ValueError('ordered normalized exchange calendar required')
        self.positions = {date: index for index,date in enumerate(self.dates)}
        self.prefixes = {}
        previous = b''
        for date in self.dates:
            previous = hashlib.sha256(previous+str(date.date()).encode('ascii')).digest()
            self.prefixes[str(date.date())] = previous.hex()


class StreamingFeatureIndex:
    def __init__(self, symbol, calendar, reference_tick_size=None, snapshot=None):
        if not isinstance(symbol,str) or not symbol.strip():
            raise ValueError('nonempty stable issuer required')
        validate_reference_tick(reference_tick_size)
        self.symbol = symbol
        self.calendar = calendar if isinstance(calendar,ExchangeCalendar) else ExchangeCalendar(calendar)
        self.tick = reference_tick_size
        self.state = dict(schema='streaming_feature_index_v1', purpose='feature_only_never_a_portfolio',
                          symbol=symbol, reference_tick_size=reference_tick_size,last_date=None,
                          calendar_prefix_sha256=None,segment=0,started=False,shares=1.,cash=0.,receivables=[],previous_close=None)
        if snapshot is not None:
            self._restore(snapshot)

    def _restore(self, value):
        if (set(value)!=set(self.state) or value['schema']!=self.state['schema']
                or value['purpose']!=self.state['purpose'] or value['symbol']!=self.symbol
                or value['reference_tick_size']!=self.tick or not isinstance(value['started'],bool)
                or isinstance(value['segment'],bool) or not isinstance(value['segment'],int) or value['segment']<0):
            raise ValueError('incompatible feature-index state')
        if value['last_date'] is not None:
            date=day(value['last_date'])
            if date not in self.calendar.positions or self.calendar.prefixes[str(date.date())]!=value['calendar_prefix_sha256']:
                raise ValueError('historical exchange calendar changed')
            if value['segment']<1 or not math.isfinite(value['previous_close']) or value['previous_close']<=0:
                raise ValueError('invalid prior feature price')
        elif value['segment']!=0 or value['started'] or value['previous_close'] is not None:
            raise ValueError('invalid uninitialized feature-index state')
        if not value['started'] and (value['shares']!=1 or value['cash']!=0 or value['receivables']):
            raise ValueError('unstarted feature segment cannot own entitlements')
        if (not math.isfinite(value['shares']) or value['shares']<=0
                or not math.isfinite(value['cash']) or value['cash']<0):
            raise ValueError('invalid feature cash or shares')
        receivables=[]
        for payment,amount in value['receivables']:
            date=day(payment)
            if not math.isfinite(amount) or amount<=0:
                raise ValueError('invalid feature receivable')
            if value['last_date'] is None or date<day(value['last_date']):
                raise ValueError('overdue feature receivable in state')
            receivables.append((str(date.date()),float(amount)))
        self.state=dict(value,receivables=receivables)

    def snapshot(self):
        return dict(self.state,receivables=[list(item) for item in self.state['receivables']])

    def advance(self, row, event=None):
        """Consume a single observed raw bar. Invalid inputs leave state unchanged."""
        date=day(row['date'])
        if date not in self.calendar.positions or row['symbol']!=self.symbol:
            raise ValueError('price outside issuer/calendar')
        opening,closing,preclose=[float(row[name]) for name in ['open','close','preclose']]
        trading=str(row['tradestatus'])
        if not np.isfinite([opening,closing,preclose]).all() or opening<0 or closing<=0 or preclose<=0 or trading not in ['0','1']:
            raise ValueError('invalid raw feature valuation/state')
        cash_per_share, multiplier, reference_cash, reference_multiplier, pay_date = 0.,1.,0.,1.,None
        if event is not None:
            if event['symbol']!=self.symbol or day(event['ex_date'])!=date or day(event['available_date'])>date:
                raise ValueError('event is not visible for this issuer/ex-date')
            cash_per_share=float(event['cash_per_share']);multiplier=float(event['share_multiplier'])
            reference_cash=event.get('reference_cash_per_share',cash_per_share)
            reference_multiplier=event.get('reference_share_multiplier',multiplier)
            reference_cash=cash_per_share if pd.isna(reference_cash) else float(reference_cash)
            reference_multiplier=multiplier if pd.isna(reference_multiplier) else float(reference_multiplier)
            if (not np.isfinite([cash_per_share,multiplier,reference_cash,reference_multiplier]).all()
                    or cash_per_share<0 or reference_cash<0 or multiplier<=0 or reference_multiplier<=0
                    or pd.notna(event.get('rights_cash_required')) and event.get('rights_cash_required',0)!=0):
                raise ValueError('invalid or externally funded feature event')
            if cash_per_share>0 or pd.notna(event.get('pay_date')):
                pay_date=day(event['pay_date'])
                if pay_date<date:raise ValueError('cash payment precedes entitlement')
        previous=self.state
        first=previous['last_date'] is None
        gap=False;fault=False
        if not first:
            last=day(previous['last_date'])
            if date<=last:raise ValueError('feature prices must advance strictly')
            gap=self.calendar.positions[date]!=self.calendar.positions[last]+1
            if not gap:
                fault=not reference_matches(preclose,previous['previous_close'],reference_cash,reference_multiplier,self.tick)
        broken=gap or fault
        segment=previous['segment']+int(first or broken)
        if first or broken:
            shares,cash,receivables,started=1.,0.,[],False
        else:
            shares,cash,receivables,started=previous['shares'],previous['cash'],list(previous['receivables']),previous['started']
        tradable=trading=='1' and opening>0
        feature_close=feature_open=float('nan')
        if not started and tradable:
            started=True
            # A newly started segment owns no earlier or same-day entitlement.
            feature_close,feature_open=closing,opening
        elif started:
            if event is not None:
                entitlement=shares*cash_per_share
                if entitlement:receivables.append((str(pay_date.date()),entitlement))
                shares*=multiplier
            current=str(date.date())
            cash+=sum(amount for payment,amount in receivables if payment<current)
            receivables=[(payment,amount) for payment,amount in receivables if payment>=current]
            if tradable and cash>0:
                shares+=cash/opening;cash=0.
            pending=sum(amount for _,amount in receivables)
            feature_close=shares*closing+cash+pending
            feature_open=shares*(opening if tradable else preclose)+cash+pending
            if not np.isfinite([shares,cash,pending,feature_close,feature_open]).all():
                raise ValueError('nonfinite feature-index wealth')
        self.state=dict(previous,last_date=str(date.date()),calendar_prefix_sha256=self.calendar.prefixes[str(date.date())],
                        segment=segment,started=started,shares=shares,cash=cash,receivables=receivables,previous_close=closing)
        return dict(date=date,symbol=self.symbol,quality_segment=segment,quality_break=broken,reference_fault=fault,
                    feature_close=feature_close,feature_total_return_open=feature_open)

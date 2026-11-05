"""Reviewed ordinary bonus-share listings, separate from physical credit dates.

This is an accounting component, not a replacement historical account engine.
Captured notice clocks remain retrospective evidence, never historical vintages.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from urllib.parse import urlparse
import json
import math
import re
import pandas as pd
from .phase2_protocol import day, utc, sha256
from .workspace_paths import WorkspacePaths


@dataclass(frozen=True)
class OrdinaryListing:
    symbol: str
    record_date: pd.Timestamp
    ex_date: pd.Timestamp
    available_date: pd.Timestamp
    tradable_from: pd.Timestamp | None
    share_multiplier: float
    cash_per_share: float


class OrdinaryListingBook:
    """Load reviewed source contracts; never infer listing from share credit."""
    def __init__(self, evidence, project, work_root, calendar, allow_unresolved_listing=False):
        paths=WorkspacePaths(project,work_root);file=paths.resolve(evidence)
        rows=json.loads(file.read_text(encoding='utf8'))
        if not isinstance(rows,list):raise ValueError('ordinary listing evidence must be a list')
        cal=pd.DatetimeIndex([day(d) for d in calendar])
        if cal.empty or cal.has_duplicates or not cal.is_monotonic_increasing:raise ValueError('ordered exchange calendar required')
        hashes={paths.name(file):sha256(file)};events={};now=pd.Timestamp(datetime.now(timezone.utc))
        for r in rows:
            if r.get('reviewed') is not True:raise ValueError('explicit ordinary listing review required')
            symbol=r['symbol']
            if not re.fullmatch(r'(sh|sz)\.\d{6}',symbol):raise ValueError('ordinary A-share symbol required')
            dates={k:day(r[k]) for k in ['record_date','ex_date','announcement_date','available_date']}
            unresolved=r.get('listing_status')=='unresolved_original_conflict'
            if unresolved:
                if allow_unresolved_listing is not True or r.get('tradable_from') is not None:raise ValueError('unresolved ordinary listing needs explicit separate profile')
                literal=day(r['source_declared_tradable_from'])
                if literal>=dates['ex_date'] or not isinstance(r.get('unresolved_reason'),str) or len(r['unresolved_reason'].strip())<20:raise ValueError('explicit contradictory original listing literal required')
                dates['tradable_from']=None
            else:
                dates['tradable_from']=day(r['tradable_from'])
            if not dates['announcement_date']<dates['available_date']<=dates['record_date']<dates['ex_date'] or dates['tradable_from'] is not None and dates['ex_date']>dates['tradable_from']:
                raise ValueError('invalid ordinary listing chronology')
            if any(dates[k] is not None and dates[k] not in cal for k in ['record_date','ex_date','available_date','tradable_from']):raise ValueError('listing contract needs exchange sessions')
            next_session=cal[cal>dates['announcement_date']]
            if not len(next_session) or dates['available_date']!=next_session[0]:raise ValueError('announcement availability must use next exchange session')
            multiplier=float(r['expected_share_multiplier']);cash=float(r['expected_cash_per_share'])
            if not math.isfinite(multiplier) or multiplier<=1 or not math.isfinite(cash) or cash<0:raise ValueError('positive ordinary bonus and nonnegative cash required')
            pages=r['parameter_pages']
            if set(pages)!={'record_date','ex_date','tradable_from','share_multiplier','cash_per_share'} or any(not isinstance(v,list) or not v or any(type(p) is not int or p<1 for p in v) for v in pages.values()):raise ValueError('all listing/economic parameter review pages required')
            source=paths.resolve(r['source_file']);capture=paths.resolve(r['source_capture_file'])
            for p,key in [(source,'source_sha256'),(capture,'source_capture_sha256')]:
                if sha256(p)!=r[key]:raise ValueError('ordinary listing source hash changed')
                hashes[paths.name(p)]=r[key]
            with source.open('rb') as stream:
                if stream.read(5)!=b'%PDF-':raise ValueError('ordinary listing primary source must be PDF')
            url=urlparse(r['source_url'])
            if url.scheme!='https' or url.hostname not in {'static.cninfo.com.cn','dataclouds.cninfo.com.cn','static.sse.com.cn','www.sse.com.cn','disc.static.szse.cn'}:raise ValueError('official primary listing notice required')
            observation=utc(r['observed_at_utc']);review=utc(r['reviewed_at_utc'])
            if observation>review or review>now:raise ValueError('actual ordered listing capture/review clocks required')
            raw=json.loads(capture.read_text(encoding='utf8'))
            if raw.get('schema')!='original_candidate_notice_capture_v1' or raw.get('code')!=symbol.split('.')[1] or raw.get('ex_date')!=str(dates['ex_date'].date()):raise ValueError('ordinary listing capture identity differs')
            if raw.get('status')=='failed' or ('search_complete' in raw and raw['search_complete'] is not True):raise ValueError('incomplete ordinary listing capture')
            # Bind the acquisition trail as well as the selected PDF. Legacy
            # captures used one query; current captures retain every page.
            records=[]
            for field in ['issuer_search','query']:
                if field in raw:records.append(raw[field])
            query_pages=raw.get('query_pages',[])
            if not isinstance(query_pages,list):raise ValueError('listing query pages must be a list')
            records.extend(query_pages)
            declared=raw.get('input_hashes',{})
            if not isinstance(declared,dict):raise ValueError('listing input hashes must be a mapping')
            package_hashes=dict(declared)
            for record in records:
                if not isinstance(record,dict):raise ValueError('listing acquisition record required')
                name=record['file'];expected=record['sha256']
                if name in package_hashes and package_hashes[name]!=expected:raise ValueError('listing acquisition hashes disagree')
                package_hashes[name]=expected
                if utc(record['observed_at_utc'])>review:raise ValueError('listing query observed after review')
            if declared:
                required={n['file']:n['sha256'] for n in raw.get('notices',[])}
                required.update({record['file']:record['sha256'] for record in records})
                if any(declared.get(name)!=expected for name,expected in required.items()):raise ValueError('listing input manifest omits captured evidence')
            for name,expected in package_hashes.items():
                # Files belong to this capture, never to another package or
                # an arbitrary path named by an untrusted receipt.
                if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+',name) or name in {'.','..','manifest.json'}:raise ValueError('listing acquisition file must be a local filename')
                if not isinstance(expected,str) or not re.fullmatch(r'[0-9a-f]{64}',expected):raise ValueError('listing acquisition SHA256 required')
                item=(capture.parent/name).resolve()
                if item.parent!=capture.parent.resolve():raise ValueError('listing acquisition file escapes capture')
                item_name=paths.name(item)
                if sha256(paths.resolve(item_name))!=expected:raise ValueError('listing acquisition evidence changed')
                hashes[item_name]=expected
            matches=[n for n in raw.get('notices',[]) if n.get('sha256')==r['source_sha256'] and n.get('url')==r['source_url']]
            if len(matches)!=1:raise ValueError('unique captured listing notice required')
            notice=matches[0]
            # A retrieved correction must stay bound to the reviewed package;
            # hashing only the primary PDF would leave the correction mutable.
            if len(raw.get('notices',[]))>1:
                reviewed=r.get('reviewed_captured_notice_pages',{})
                if set(reviewed)!={n['file'] for n in raw['notices']}:raise ValueError('all captured notices need explicit review pages')
                for extra in raw['notices']:
                    ep=reviewed[extra['file']]
                    if not isinstance(ep,list) or not ep or any(type(p) is not int or p<1 for p in ep):raise ValueError('captured notice review pages required')
                    extra_file=(capture.parent/extra['file']).resolve()
                    name=paths.name(extra_file)
                    if sha256(paths.resolve(name))!=extra['sha256']:raise ValueError('captured supplementary notice changed')
                    with extra_file.open('rb') as stream:
                        if stream.read(5)!=b'%PDF-':raise ValueError('captured supplementary notice must be PDF')
                    extra_url=urlparse(extra['url'])
                    if extra_url.scheme!='https' or extra_url.hostname not in {'static.cninfo.com.cn','dataclouds.cninfo.com.cn','static.sse.com.cn','www.sse.com.cn','disc.static.szse.cn'}:raise ValueError('supplementary official notice required')
                    if utc(extra['observed_at_utc'])>review:raise ValueError('supplementary notice observed after review')
                    hashes[name]=extra['sha256']
            captured_file=(capture.parent/notice['file']).resolve()
            publication=pd.to_datetime(notice['source_announcement_time'],unit='ms',utc=True).tz_convert('Asia/Shanghai').tz_localize(None).normalize()
            if captured_file!=source or utc(notice['observed_at_utc'])!=observation or publication!=dates['announcement_date']:raise ValueError('listing notice path, clock or publication differs')
            if observation.tz_convert('Asia/Shanghai').tz_localize(None).normalize()<publication:raise ValueError('notice capture predates publication')
            key=(symbol,dates['ex_date'])
            if key in events:raise ValueError('duplicate ordinary listing event')
            events[key]=OrdinaryListing(symbol,dates['record_date'],dates['ex_date'],dates['available_date'],dates['tradable_from'],multiplier,cash)
        self.events=MappingProxyType(events)
        self.source_hashes=MappingProxyType(hashes)
        self._paths=paths
        self.historical_vintages_certified=False
        self.physical_credit_dates_inferred=False

    def verify_sources(self):
        for name,expected in self.source_hashes.items():
            if sha256(self._paths.resolve(name))!=expected:raise ValueError('ordinary listing source changed after load')


class OrdinarySettlementStop(ValueError):
    def __init__(self,reason,symbol,date):
        self.reason=reason;self.symbol=symbol;self.date=day(date)
        super().__init__(f'{reason}: {symbol} {self.date.date()}')


class OrdinaryShareWallet:
    """Only extra ordinary shares are locked; cash handled by the account.

    All validation precedes mutation. Record-close holdings determine bonus
    entitlement. Existing sellable holdings are never multiplied on ex-date.
    """
    def __init__(self,book,allow_unresolved_listing=False):
        if not isinstance(book,OrdinaryListingBook):raise ValueError('typed reviewed ordinary listing book required')
        self.book=book;self.claims={};self.registry={};self.applied=set();self.last_open=None;self.last_close=None
        self.allow_unresolved_listing=allow_unresolved_listing is True

    def fork(self):
        other=OrdinaryShareWallet(self.book,self.allow_unresolved_listing)
        other.claims=dict(self.claims);other.registry=dict(self.registry);other.applied=set(self.applied)
        other.last_open=self.last_open;other.last_close=self.last_close
        return other

    def snapshot(self):
        return dict(registered={f'{s}/{d.date()}':q for (s,d),q in self.registry.items()},locked_claims={f'{s}/{d.date()}':dict(symbol=s,ex_date=str(d.date()),quantity=q,tradable_from=None if self.book.events[(s,d)].tradable_from is None else str(self.book.events[(s,d)].tradable_from.date())) for (s,d),q in self.claims.items()})

    def valuation_symbols(self):return {s for (s,_),q in self.claims.items() if q>0}

    def locked_exposure(self,prices):
        values={}
        for (s,_),q in self.claims.items():
            p=prices.get(s)
            if p is None or not math.isfinite(p) or p<=0:raise ValueError('unknown positive ordinary claim valuation')
            values[s]=values.get(s,0.)+q*p
        return values

    def value(self,prices):return math.fsum(self.locked_exposure(prices).values())

    def unresolved_exposure(self,prices):
        self.value(prices)
        values={}
        for (s,d),q in self.claims.items():
            if self.book.events[(s,d)].tradable_from is None:values[s]=values.get(s,0.)+q*prices[s]
        return values

    def open(self,date,holdings,prices,actions,other_locked_symbols=()):
        date=day(date)
        if self.last_open is not None and date<=self.last_open:raise ValueError('strictly ordered ordinary wallet opens required')
        if any(not math.isfinite(q) or q<0 for q in holdings.values()):raise ValueError('known nonnegative ordinary holdings required')
        self.value(prices)
        staged=[];seen=set();pending=self.valuation_symbols();other=set(other_locked_symbols)
        for e in actions.itertuples(index=False):
            if day(e.ex_date)!=date:raise ValueError('only current ex-date ordinary actions allowed')
            key=(e.symbol,date)
            if key in seen:raise ValueError('duplicate ordinary action')
            seen.add(key);shares=holdings.get(e.symbol,0.)
            if e.symbol in pending or e.symbol in other:raise OrdinarySettlementStop('ordinary_corporate_action_overlap',e.symbol,date)
            if shares<=0 and self.registry.get(key,0.)<=0:continue
            mult=float(e.share_multiplier)
            if not math.isfinite(mult) or mult<=0:raise OrdinarySettlementStop('unknown_ordinary_multiplier',e.symbol,date)
            term=self.book.events.get(key)
            if term is not None and (mult!=term.share_multiplier or float(e.cash_per_share)!=term.cash_per_share):raise OrdinarySettlementStop('ordinary_listing_economics_differ',e.symbol,date)
            if mult<1:raise OrdinarySettlementStop('unsupported_ordinary_share_reduction',e.symbol,date)
            if mult==1:continue
            if term is None:raise OrdinarySettlementStop('ordinary_listing_unknown',e.symbol,date)
            if term.tradable_from is None and not self.allow_unresolved_listing:raise OrdinarySettlementStop('ordinary_listing_unknown',e.symbol,date)
            if term.available_date>date or mult!=term.share_multiplier or float(e.cash_per_share)!=term.cash_per_share:raise OrdinarySettlementStop('ordinary_listing_economics_differ',e.symbol,date)
            if key in self.applied:raise ValueError('ordinary event already applied')
            if key not in self.registry or abs(self.registry[key]-shares)>1e-12:raise OrdinarySettlementStop('ordinary_registry_unknown_or_changed',e.symbol,date)
            p=prices.get(e.symbol)
            if p is None or not math.isfinite(p) or p<=0:raise OrdinarySettlementStop('ordinary_claim_price_unknown',e.symbol,date)
            quantity=shares*(mult-1)
            if not math.isfinite(quantity):raise OrdinarySettlementStop('ordinary_claim_quantity_overflow',e.symbol,date)
            staged.append((key,quantity))
        due={key for key in self.registry if key[1]==date and self.registry[key]>0}
        if not due.issubset(seen):
            key=sorted(due-seen)[0];raise OrdinarySettlementStop('ordinary_action_missing',key[0],date)
        self.last_open=date;rows=[]
        for key,q in staged:
            self.applied.add(key);self.claims[key]=q
            rows.append(dict(date=str(date.date()),symbol=key[0],ex_date=str(key[1].date()),kind='bonus_claim_recognized',quantity=q))
        for key,q in list(self.claims.items()):
            if self.book.events[key].tradable_from is not None and date>=self.book.events[key].tradable_from:
                holdings[key[0]]=holdings.get(key[0],0.)+q;del self.claims[key]
                rows.append(dict(date=str(date.date()),symbol=key[0],ex_date=str(key[1].date()),kind='bonus_shares_unlocked',quantity=q))
        return rows

    def close(self,date,holdings):
        date=day(date)
        if self.last_open!=date or self.last_close==date:raise ValueError('one registry close after same-session open required')
        staged=[]
        for key,e in self.book.events.items():
            if e.record_date!=date:continue
            q=holdings.get(e.symbol,0.)
            if not math.isfinite(q) or q<0:raise ValueError('known ordinary registry shares required')
            if e.available_date>date or e.symbol in self.valuation_symbols():raise OrdinarySettlementStop('ordinary_registry_overlap_or_unavailable',e.symbol,date)
            staged.append((key,float(q)))
        self.registry.update(staged);self.last_close=date

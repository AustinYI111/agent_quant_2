"""Replay prospective frozen books with the existing raw-share account.

Retrospective scores cannot fund a paper book. Unknown positions never vanish.
Valuations use session opens, matching the original research account/benchmark.
"""
from pathlib import Path
from datetime import datetime,timezone
import argparse,json,hashlib
import numpy as np
import pandas as pd
from .data import Dataset
from .model import Config
from .pipeline import simulate_monthly
from .causal_quality import HeldValuationError
from .horizon_research import compare_index
from .phase2_protocol import day,load_seal,forecast_role,completed_date_limit,sha256
from .prediction_ledger import read_predictions,_digest
from .streaming_feature_index import ExchangeCalendar
from .accounting_attribution import attribute_open_account


def scheduled_signals(calendar,independent_start,horizon):
    """Anchor to the first independent exchange session, never first forecast."""
    cal=ExchangeCalendar(calendar).dates
    if cal[0]>=day(independent_start):raise ValueError('complete calendar with a prior-year session required; never reanchor a cropped calendar')
    base=cal[cal>=day(independent_start)]
    if base.empty:return base
    if horizon in ['5d','20d']:return base[::int(horizon[:-1])]
    if horizon!='12m':raise ValueError('unknown frozen horizon')
    dates=[];anniversary=base[0]
    while anniversary<=base[-1]:
        index=base.searchsorted(anniversary)
        if index<len(base):dates.append(base[index])
        anniversary+=pd.DateOffset(months=12)
    return pd.DatetimeIndex(dates)


def _verified_batch(database,batch_id,protocol,seal,calendar,synthetic):
    receipt,scores=read_predictions(database,batch_id)
    horizon=receipt['horizon']
    if receipt['protocol_sha256']!=seal['protocol_sha256'] or receipt['model_sha256']!=protocol['models'][horizon]['files_sha256']['model.txt']:
        raise ValueError('paper forecast differs from frozen protocol/model')
    if receipt['synthetic'] is not synthetic:raise ValueError('paper data kind differs from forecast')
    identity={key:receipt[key] for key in ['protocol_sha256','signal_date','horizon','model_sha256','strategy','synthetic']}
    rows=[dict(symbol=r.symbol,trading_symbol=r.trading_symbol,score=float(r.score),rank=int(r.rank)) for r in scores.itertuples()]
    if _digest(identity)!=batch_id or _digest(dict(identity=identity,sources=receipt['sources'],scores=rows))!=receipt['content_sha256']:
        raise ValueError('paper forecast content changed')
    if len(scores)!=receipt['candidate_count'] or len(scores)<20 or scores.symbol.duplicated().any() or not np.isfinite(scores.score).all():
        raise ValueError('paper forecast candidate pool incomplete')
    timing=forecast_role(receipt['signal_date'],receipt['created_at_utc'],max(x['observed_at_utc'] for x in receipt['sources']),calendar,protocol)
    if not synthetic and any(receipt[key]!=timing[key] for key in timing):raise ValueError('paper forecast timing changed')
    return receipt,scores


def position_snapshot(nav,orders,quotes,actions):
    """Reconstruct actual fractional shares and reconcile the last valid NAV."""
    columns=['symbol','shares','valuation_date','raw_open_valuation','market_value']
    if nav.empty:return pd.DataFrame(columns=columns)
    holdings={};last=pd.Timestamp(nav.date.iloc[-1]);lookup=quotes.set_index(['date','symbol'])
    for date in pd.to_datetime(nav.date):
        for event in actions.loc[actions.ex_date.eq(date)].itertuples():
            if event.symbol in holdings:holdings[event.symbol]*=float(event.share_multiplier)
        filled=orders.loc[pd.to_datetime(orders.date).eq(date)&orders.status.eq('filled')] if not orders.empty else orders
        for order in filled.itertuples():
            raw=lookup.loc[(date,order.symbol)]
            if str(raw.tradestatus)!='1' or raw.open<=0:raise ValueError('filled paper order lacks executable raw open')
            quantity=float(order.notional)/float(raw.open)
            holdings[order.symbol]=holdings.get(order.symbol,0)+(quantity if order.side=='buy' else -quantity)
            if holdings[order.symbol]<-1e-10:raise ValueError('paper share reconstruction became negative')
    records=[]
    for symbol,quantity in sorted(holdings.items()):
        if quantity<=1e-12:continue
        row=lookup.loc[(last,symbol)];price=float(row.open if str(row.tradestatus)=='1' and row.open>0 else row.preclose)
        records.append(dict(symbol=symbol,shares=quantity,valuation_date=last,raw_open_valuation=price,market_value=quantity*price))
    frame=pd.DataFrame(records,columns=columns);value=frame.market_value.sum()+float(nav.cash.iloc[-1])+float(nav.dividend_receivable.iloc[-1])
    if not np.isclose(value,float(nav.nav.iloc[-1]),rtol=0,atol=1e-10):raise ValueError('paper shares/cash/receivables do not reconcile to NAV')
    return frame


def replay_paper_books(database,batch_ids,project,seal_directory,raw_prices,actions,calendar,asof,out,benchmark=None,synthetic=False):
    """New snapshot per replay; late forecasts excluded, no invented cash NAV.

    Synthetic fixtures are explicitly separate. An empty prospective book is
    awaiting predictions, not a zero-return portfolio. No model fitting occurs.
    """
    target=Path(out)
    if target.exists():raise ValueError('new paper tracking output required')
    if not batch_ids or len(batch_ids)!=len(set(batch_ids)):raise ValueError('distinct explicit forecast batches required')
    cal=ExchangeCalendar(calendar).dates;cutoff=day(asof)
    if cutoff not in cal or cutoff>completed_date_limit(datetime.now(timezone.utc)):raise ValueError('paper asof is not a completed exchange session')
    protocol,seal=load_seal(seal_directory,project)
    if day(protocol['research_end']) not in cal:raise ValueError('paper calendar lacks frozen research boundary')
    execution=protocol['execution']
    if execution['top_k']!=20 or execution['commission_bps']!=3 or execution['slippage_bps']!=5:raise ValueError('frozen paper execution contract differs')
    accepted={h:{} for h in ['5d','20d','12m']};excluded=[]
    for batch_id in batch_ids:
        receipt,scores=_verified_batch(database,batch_id,protocol,seal,cal,synthetic)
        signal=day(receipt['signal_date'])
        if signal>cutoff:raise ValueError('paper forecast is after valuation date')
        if not synthetic and not receipt['prospective']:
            excluded.append(dict(batch_id=batch_id,horizon=receipt['horizon'],signal_date=receipt['signal_date'],reason='retrospective_not_executable',role=receipt['role']));continue
        group=accepted[receipt['horizon']]
        if signal in group:raise ValueError('duplicate paper horizon/signal')
        group[signal]=(receipt,scores)
    raw=raw_prices.copy()
    if not {'date','symbol','open','close','preclose','tradestatus'}.issubset(raw):raise ValueError('paper raw quote schema incomplete')
    raw['date']=pd.to_datetime(raw.date,format='mixed')
    if raw.empty or raw.date.gt(cutoff).any() or cutoff not in set(raw.date):raise ValueError('paper requires observed quotes through asof, without future bars')
    a=actions.copy()
    for field in ['ex_date','available_date','pay_date']:a[field]=pd.to_datetime(a[field],format='mixed')
    target.mkdir(parents=True)
    proof=dict(schema='frozen_paper_tracking_v1',status='tracking_saved',asof=str(cutoff.date()),created_at_utc=datetime.now(timezone.utc).isoformat(),protocol_sha256=seal['protocol_sha256'],synthetic=synthetic,certified=False,model_ready=False,excluded=excluded,books={},valuation_basis='same-session raw open; dividends/receivables; fractional research shares',limitations=['No limit-up/down or lot-size fill certification.','No live deployment or strategy profitability proven.','Retrospective forecasts are never executed.'])
    for horizon,forecasts in accepted.items():
        schedule=scheduled_signals(cal,protocol['independent_start'],horizon)
        eligible=[d for d in schedule if d in forecasts and cal.searchsorted(d,side='right')<len(cal) and cal[cal.searchsorted(d,side='right')]<=cutoff]
        if not eligible:
            proof['books'][horizon]=dict(status='awaiting_prospective_scheduled_entry',nav_rows=0,orders=0,accepted_forecasts=len(forecasts));continue
        first=min(eligible);required=schedule[(schedule>=first)&(schedule<cutoff)]
        missing=[d for d in required if d not in forecasts]
        last=cutoff;gap=None
        if missing:
            gap=min(missing);last=gap  # missing decision trades at following open
        selected=[]
        for signal in eligible:
            if signal>=last:continue
            receipt,scores=forecasts[signal]
            chosen=scores.head(20).copy();chosen['date']=signal;chosen['target_weight']=1/20;chosen['batch_id']=receipt['batch_id'];selected.append(chosen)
        if not selected:
            proof['books'][horizon]=dict(status='awaiting_prospective_scheduled_entry',nav_rows=0,orders=0);continue
        picks=pd.concat(selected,ignore_index=True);symbols=set(picks.symbol)
        quotes=raw[raw.symbol.isin(symbols)&raw.date.le(last)&raw.date.ge(first)].copy()
        dates=cal[cal<=last]
        tradable=quotes.tradestatus.astype(str).eq('1')&quotes.open.gt(0)
        prices=quotes.copy();prices['total_return_open']=quotes.open.where(tradable,quotes.preclose);prices['can_buy']=tradable;prices['can_sell']=tradable
        events=a[a.symbol.isin(symbols)&a.ex_date.le(last)].copy()
        data=Dataset(prices,pd.DataFrame(),dates,dict(synthetic=synthetic,quality_policy={'schema':'causal_quality_research_v1'}))
        directory=target/horizon;directory.mkdir();picks.to_csv(directory/'scheduled_top20.csv',index=False)
        try:
            nav,orders=simulate_monthly(data,picks,Config(top_k=20,commission_bps=3,slippage_bps=5),quotes,events,.01,causal_quality_research=True)
            result=dict(status='incomplete_missing_forecast' if gap is not None else 'valued_research_prefix',nav_rows=len(nav),orders=len(orders),missing_signal_date=str(gap.date()) if gap is not None else None)
        except HeldValuationError as error:
            nav,orders=error.nav,error.trades
            result=dict(status='incomplete_held_valuation',nav_rows=len(nav),orders=len(orders),symbol=error.symbol,date=str(error.date.date()),reason=error.reason,holdings=error.holdings)
        nav.to_csv(directory/'open_nav.csv',index=False);orders.to_csv(directory/'orders.csv',index=False)
        positions=position_snapshot(nav,orders,quotes,events)
        positions.to_csv(directory/'positions.csv',index=False)
        result['position_rows']=len(positions);result['shares_cash_receivables_reconciled']=not nav.empty
        accounting,position_history,asset_pnl=attribute_open_account(nav,orders,quotes,events,benchmark=benchmark)
        accounting.to_csv(directory/'open_account_attribution.csv',index=False)
        position_history.to_csv(directory/'position_history.csv',index=False)
        asset_pnl.to_csv(directory/'asset_attribution.csv',index=False)
        result['accounting_identity_verified']=not accounting.empty
        result['accounting_max_absolute_error']=float(accounting.reconciliation_error.abs().max()) if not accounting.empty else None
        result['market_reference_method']='prior invested value times CSI300 price-index open return; unit-beta reference, not causal alpha' if benchmark is not None else 'not_supplied'
        result['industry_attribution_status']='pending_dated_membership_and_sector_return_evidence'
        if benchmark is not None and not nav.empty:
            comparison,metrics=compare_index(nav,orders,benchmark,'price_index')
            comparison.to_csv(directory/'benchmark_comparison.csv',index=False);result['price_index_comparison']=metrics
        proof['books'][horizon]=result
    proof['prediction_database_sha256']=sha256(database)
    proof['input_frame_sha256']={name:hashlib.sha256(frame.to_csv(index=False).encode()).hexdigest() for name,frame in [('raw_quotes',raw),('actions',a),('calendar',pd.DataFrame({'date':cal}))]}
    proof['code_sha256']={name:hashlib.sha256(Path(__file__).with_name(name).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest() for name in ['paper_tracking.py','accounting_attribution.py','pipeline.py','corporate_actions.py','causal_quality.py','costs.py']}
    proof['outputs_sha256']={p.relative_to(target).as_posix():sha256(p) for p in target.rglob('*.csv')}
    (target/'manifest.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
    return proof


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',default='.')
    for name in ['ledger','seal','prices','actions','calendar','asof','out']:parser.add_argument('--'+name,required=True)
    parser.add_argument('--batch-id',action='append',required=True)
    parser.add_argument('--benchmark')
    args=parser.parse_args();root=Path(args.project).resolve()
    def local(value):
        relative=Path(value)
        if relative.is_absolute() or '..' in relative.parts or ':' in value:raise ValueError('portable project-relative paper paths required')
        result=(root/relative).resolve()
        if not result.is_relative_to(root):raise ValueError('paper path escapes project')
        return result
    files={name:local(getattr(args,name)) for name in ['ledger','prices','actions','calendar']}
    if args.benchmark:files['benchmark']=local(args.benchmark)
    hashes={path.relative_to(root).as_posix():sha256(path) for path in files.values()}
    cal=pd.read_csv(files['calendar'])
    result=replay_paper_books(files['ledger'],args.batch_id,root,local(args.seal),pd.read_csv(files['prices'],dtype={'tradestatus':str},float_precision='round_trip'),pd.read_csv(files['actions'],float_precision='round_trip',low_memory=False),pd.to_datetime(cal.date),args.asof,local(args.out),benchmark=pd.read_csv(files['benchmark']) if args.benchmark else None)
    if any(sha256(root/name)!=digest for name,digest in hashes.items()):
        result['status']='failed_input_changed'
    result['source_files_sha256']=hashes
    (local(args.out)/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
    if result['status']!='tracking_saved':raise ValueError('paper input changed during replay')
    print(json.dumps({key:result[key] for key in ['status','asof','books','excluded']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()

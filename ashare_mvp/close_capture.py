"""Portable, source-timed raw close acquisition; never an inference approval.

Collects a dated exchange calendar, security names, supplier industry version,
CSI300 price-index bars, and the market cross-section. Financial statements and
company-action notices still need their separate acquisition/review workflow.
"""
from pathlib import Path
from datetime import datetime,timezone
import argparse,json
import numpy as np
import pandas as pd
from .workspace_paths import WorkspacePaths
from .phase2_protocol import day,utc,sha256,completed_date_limit,load_seal
from .provider_session import PersistentQueryClient
from .public_data import _result_frame
from .market_download import download_market_daily
from .network_retry import fetch_with_retry
from .source_timing import audit_market_observations
from .disk_market import write_manifest,code_hashes


def now():return datetime.now(timezone.utc)


def validate_calendar(frame,start,end,signal,limit):
    if not {'calendar_date','is_trading_day'}.issubset(frame) or frame.empty:raise ValueError('complete dated exchange calendar required')
    dates=pd.DatetimeIndex(pd.to_datetime(frame.calendar_date))
    expected=pd.date_range(start,end)
    if dates.tz is not None or dates.hasnans or not dates.equals(expected) or not frame.is_trading_day.isin(['0','1']).all():raise ValueError('every ordered unique requested calendar date required')
    sessions=dates[frame.is_trading_day.eq('1').to_numpy()]
    completed=sessions[sessions<=limit]
    if not len(completed) or completed[-1]!=signal or not (sessions>signal).any() or len(sessions[sessions<=signal])<3:raise ValueError('exact latest completed session and preceding/next sessions required')
    return sessions


def validate_reference(kind,frame,signal,benchmark_dates):
    if frame.empty:raise ValueError('empty reference is not coverage evidence')
    if kind=='csi300':
        if not {'date','code','open','high','low','close'}.issubset(frame) or not frame.code.eq('sh.000300').all():raise ValueError('identified CSI300 price-index bars required')
        dates=pd.DatetimeIndex(pd.to_datetime(frame.date))
        values=frame[['open','high','low','close']].apply(pd.to_numeric,errors='raise')
        if not dates.equals(pd.DatetimeIndex(benchmark_dates)) or not np.isfinite(values.to_numpy()).all() or values.le(0).any().any():raise ValueError('complete positive benchmark sessions required')
        if values.high.lt(values[['open','close','low']].max(axis=1)).any() or values.low.gt(values[['open','close','high']].min(axis=1)).any():raise ValueError('invalid benchmark OHLC')
    elif kind=='stock_all':
        if not {'code','code_name'}.issubset(frame) or frame.code.isna().any() or frame.code.duplicated().any() or frame.code_name.isna().any() or frame.code_name.str.strip().eq('').any():raise ValueError('unique known dated security names required')
    elif kind=='industry':
        if not {'code','industry','updateDate'}.issubset(frame) or frame.code.isna().any() or frame.code.duplicated().any():raise ValueError('unique dated supplier industry rows required')
        dates=pd.to_datetime(frame.updateDate,errors='raise')
        if dates.isna().any() or dates.gt(signal).any():raise ValueError('future or missing industry update date')
    else:raise ValueError('unknown reference contract')
    return frame


def capture_close(project,work_root,seal,signal,calendar_end,out,client=None):
    paths=WorkspacePaths(project,work_root);target=paths.resolve(out);signal=day(signal);end=day(calendar_end)
    started=utc(now());limit=completed_date_limit(started)
    if signal>limit:raise ValueError('requested close has not passed the frozen 17:20 gate')
    if end<=signal:raise ValueError('calendar must include the next execution session')
    sealed=paths.resolve(seal)
    if target.exists() or not target.is_relative_to(paths.work) or target.is_relative_to(sealed) or sealed.is_relative_to(target):raise ValueError('fresh separate external capture directory required')
    _,protocol=load_seal(sealed,paths.project)
    target.mkdir(parents=True);market=target/'market';reference=target/'reference';market.mkdir();reference.mkdir()
    proof=dict(schema='portable_raw_close_capture_v1',status='running',signal_date=str(signal.date()),started_at_utc=started.isoformat(),
               protocol_sha256=protocol['protocol_sha256'],capture_started_on_signal_date=started.tz_convert('Asia/Shanghai').tz_localize(None).normalize()==signal,
               certified=False,synthetic=False,prediction_ready=False,original_vintages_verified=False,model_selection_performed=False,
               code_hashes=code_hashes(['close_capture.py','market_download.py','source_timing.py','workspace_paths.py','provider_session.py','network_retry.py','phase2_protocol.py','public_data.py','disk_market.py']),
               limitations=['Raw acquisition only; not financial PIT, company-action reconciliation, full universe certification or a daily prediction.',
                            'Current supplier industry versions retain actual observation clocks; not proof of historical first availability.',
                            'A later-day recapture is not a same-day security-state observation and cannot authorize an earlier prospective risk view.'])
    ref=dict(schema='phase2_reference_capture_v1',protocol_sha256=protocol['protocol_sha256'],status='running',started_at_utc=started.isoformat(),queries={},synthetic=False,certified=False)
    mark=dict(schema='phase2_latest_market_capture_v1',protocol_sha256=protocol['protocol_sha256'],status='running',started_at_utc=started.isoformat(),synthetic=False,certified=False,prediction_ready=False)
    def save():
        write_manifest(target/'capture.json',proof);write_manifest(reference/'capture.json',ref);write_manifest(market/'capture.json',mark)
    save();client=client if client is not None else PersistentQueryClient(30)
    try:
        login=fetch_with_retry(client.login)
        if login.error_code!='0':raise ValueError('provider login failed')
        start=pd.Timestamp(year=signal.year-1,month=1,day=1)
        calendar=fetch_with_retry(lambda:_result_frame(client.query_trade_dates(start_date=str(start.date()),end_date=str(end.date()))))
        sessions=validate_calendar(calendar,start,end,signal,limit)
        calendar.to_csv(market/'exchange_calendar.csv',index=False)
        mark.update(target_session=str(signal.date()),calendar_sha256=sha256(market/'exchange_calendar.csv'))
        previous=sessions[sessions<=signal][-3:];begin=str(previous[0].date());stop=str(signal.date());suffix=signal.strftime('%Y%m%d')
        jobs=[('csi300',f'csi300_{previous[0]:%Y%m%d}_{suffix}',lambda:client.query_history_k_data_plus('sh.000300','date,code,open,high,low,close,volume,amount,pctChg',start_date=begin,end_date=stop,frequency='d',adjustflag='3')),
              ('stock_all','stock_all_'+suffix,lambda:client.query_all_stock(day=stop)),
              ('industry','industry_'+suffix,lambda:client.query_stock_industry(date=stop))]
        for kind,key,query in jobs:
            try:
                frame=validate_reference(kind,fetch_with_retry(lambda:_result_frame(query())),signal,previous)
                file=reference/(key+'.csv');frame.to_csv(file,index=False)
                item=dict(status='raw_acquired',rows=len(frame),columns=list(frame),sha256=sha256(file),observed_at_utc=utc(now()).isoformat())
                if kind=='csi300':item['return_kind']='price_index'
                if kind=='industry':item.update(original_vintages_verified=False,classification_dates=sorted(frame.updateDate.unique().tolist()),limitation='Supplier dated version; no original publication/first-availability certification.')
                ref['queries'][key]=item
            except Exception as error:ref['queries'][key]=dict(status='failed',error=str(error))
            save()
        ref.update(status='raw_acquired' if all(x['status']=='raw_acquired' for x in ref['queries'].values()) else 'partial',completed_at_utc=utc(now()).isoformat());save()
        result=download_market_daily(market/'latest',stop,stop,client=client)
        if result['status']!='downloaded_raw':raise ValueError('market capture incomplete; preserve staged response')
        observations=audit_market_observations([market/'latest'],market/'closed_market_observations.json')
        mark.update(status='raw_acquired',latest=result,observation_audit=observations,completed_at_utc=utc(now()).isoformat())
        save()
        clocks=[i['observed_at_utc'] for i in ref['queries'].values() if i['status']=='raw_acquired']+[i['observed_at_utc'] for i in observations['records']]
        proof.update(status='raw_acquired' if ref['status']=='raw_acquired' else 'partial',completed_at_utc=utc(now()).isoformat(),
                     all_observations_on_signal_date=all(utc(t).tz_convert('Asia/Shanghai').tz_localize(None).normalize()==signal for t in clocks),
                     output_hashes={p.relative_to(target).as_posix():sha256(p) for p in target.rglob('*') if p.is_file() and p!=target/'capture.json'})
        if code_hashes(list(proof['code_hashes']))!=proof['code_hashes']:raise ValueError('capture implementation changed')
        save();return proof
    except BaseException as error:
        proof.update(status='failed',error=str(error),completed_at_utc=utc(now()).isoformat());save();raise
    finally:client.logout()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',default='.')
    for name in ['work-root','seal','signal','calendar-end','out']:parser.add_argument('--'+name,required=True)
    result=capture_close(**vars(parser.parse_args()))
    print(json.dumps(dict(status=result['status'],signal_date=result['signal_date'],prediction_ready=False)))
    if result['status']!='raw_acquired':raise SystemExit(2)

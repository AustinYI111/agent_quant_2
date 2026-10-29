"""Observe provider dividend plans and prepare a bounded notice-review queue.

Plans are not cash payments. No candidate becomes an account action here;
unexplained terms, dates and conflicts remain explicit and old actions survive.
"""
from pathlib import Path
from datetime import datetime,timezone
import argparse,json,re
import numpy as np
import pandas as pd
from .workspace_paths import WorkspacePaths
from .phase2_protocol import day,sha256
from .disk_market import code_hashes,write_manifest
from .public_data import is_ashare
from .security_identity import code_change_maps
from .dividend_market import download_dividend_market,get_dividend_page
from .dividend_normalization import _terms
from .network_retry import fetch_with_retry

CODES=['company_action_intake.py','dividend_market.py','dividend_normalization.py','security_identity.py','workspace_paths.py','public_data.py','network_retry.py']


def classify_plans(raw,actions,calendar,window_start,window_end,roots=None):
    roots=roots or {};cal=pd.DatetimeIndex(calendar);start,end=day(window_start),day(window_end)
    if raw.empty:raise ValueError('nonempty explicit source rows required')
    if cal.empty or cal.hasnans or cal.has_duplicates or not cal.is_monotonic_increasing or start>end:raise ValueError('ordered calendar and bounded review window required')
    prior=actions.copy();prior['ex_date']=pd.to_datetime(prior.ex_date)
    if prior.duplicated(['symbol','ex_date']).any():raise ValueError('unambiguous prior action keys required')
    known=prior.set_index(['symbol','ex_date']);records=[];observed=set()
    for r in raw.to_dict('records'):
        record=dict(source_security=r['SECUCODE'],source_period=r['source_period'],provider_progress=r['ASSIGN_PROGRESS'],
                    source_file=r['source_file'],source_row=r['source_row'],observed_at_utc=r['observed_at_utc'],
                    provider_description=r['IMPL_PLAN_PROFILE'],ex_date=r['EX_DIVIDEND_DATE'],
                    payment_date_supplied=False,candidate_applied=False)
        match=re.fullmatch(r'(\d{6})\.(SH|SZ|BJ)',str(r['SECUCODE']))
        provider=(match[2].lower()+'.'+match[1]) if match else '';symbol=roots.get(provider,provider)
        record.update(provider_symbol=provider,symbol=symbol)
        if not is_ashare(symbol):record.update(status='excluded_instrument',reason='outside ordinary Shanghai/Shenzhen A-share scope')
        elif r['ASSIGN_PROGRESS']!='实施分配':record.update(status='not_implemented',reason='a proposal or other progress is not an executed distribution')
        else:
            try:
                ex=day(r['EX_DIVIDEND_DATE']);record['ex_date']=str(ex.date())
                if ex<start or ex>end:
                    record.update(status='outside_review_window',reason='retain raw source; no admission outside this explicit window');records.append(record);continue
                observed.add((symbol,ex))
                if ex not in cal:raise ValueError('execution date is not a known exchange session')
                notice=day(r['NOTICE_DATE']);ix=cal.searchsorted(notice,side='right')
                if ix>=len(cal) or cal[ix]>ex:raise ValueError('latest provider notice not visible before execution')
                terms=_terms(pd.Series(dict(dividCashStock=r['IMPL_PLAN_PROFILE'],
                    dividCashPsBeforeTax=float(r['PRETAX_BONUS_RMB'])/10 if pd.notna(r['PRETAX_BONUS_RMB']) else np.nan,
                    dividStocksPs=float(r['BONUS_RATIO'])/10 if pd.notna(r['BONUS_RATIO']) else np.nan,
                    dividReserveToStockPs=float(r['IT_RATIO'])/10 if pd.notna(r['IT_RATIO']) else np.nan)))
                cash=terms['dividCashPsBeforeTax'];shares=1+terms['dividStocksPs']+terms['dividReserveToStockPs']
                record.update(provider_cash_per_share=cash,provider_share_multiplier=shares,provider_notice_date=str(notice.date()),provider_notice_next_session=str(cal[ix].date()))
                if (symbol,ex) in known.index:
                    old=known.loc[(symbol,ex)]
                    equal=np.isclose(float(old.cash_per_share),cash,rtol=0,atol=1e-12) and np.isclose(float(old.share_multiplier),shares,rtol=0,atol=1e-12)
                    record.update(parent_cash_per_share=float(old.cash_per_share),parent_share_multiplier=float(old.share_multiplier),
                                  status='existing_terms_match' if equal else 'existing_terms_conflict',
                                  reason='terms match prior reviewed row; no new evidence of payment or original vintage' if equal else 'provider differs from prior holder terms; do not replace either source')
                else:record.update(status='new_event_needs_notice',reason='payment date and precise holder/reference terms need primary implementation review')
            except (ValueError,TypeError,KeyError,OverflowError) as error:record.update(status='unresolved_provider_terms_or_dates',reason=str(error))
        records.append(record)
    result=pd.DataFrame(records)
    eligible=result.provider_progress.eq('实施分配')&result.symbol.map(is_ashare)&~result.status.isin(['outside_review_window','excluded_instrument','not_implemented'])&result.ex_date.notna()
    overlaps=result.loc[eligible].duplicated(['symbol','ex_date'],keep=False)
    indices=overlaps.index[overlaps]
    if len(indices):
        result.loc[indices,'prior_status']=result.loc[indices,'status']
        result.loc[indices,'status']='overlapping_plan_rows_need_notice'
        result.loc[indices,'reason']='Multiple report-period rows share one execution key; no sum, duplicate payment or preferred source row inferred.'
    queue=result[result.status.isin(['new_event_needs_notice','existing_terms_conflict','unresolved_provider_terms_or_dates','overlapping_plan_rows_need_notice'])].copy()
    in_window=prior[prior.ex_date.between(start,end)]
    absent=in_window[[key not in observed for key in zip(in_window.symbol,in_window.ex_date)]].copy()
    return result,queue,absent


def intake(project,work_root,inputs,out,fetch=None):
    paths=WorkspacePaths(project,work_root);config_file=paths.resolve(inputs);target=paths.resolve(out)
    config=json.loads(config_file.read_text(encoding='utf8'))
    if config.get('schema')!='company_action_intake_inputs_v1':raise ValueError('explicit company-action intake recipe required')
    signal=day(config['signal_date']);started=pd.Timestamp.now(tz='UTC')
    if signal!=started.tz_convert('Asia/Shanghai').tz_localize(None).normalize():raise ValueError('intake must retain its actual observation date')
    first,last=day(config['report_start']),day(config['report_end']);start,end=day(config['window_start']),day(config['window_end'])
    if first>last or last>=signal or start>end or first not in pd.date_range(first,last,freq='QE') or last not in pd.date_range(first,last,freq='QE'):raise ValueError('completed report bounds and ordered execution window required')
    parent=paths.resolve(config['parent_actions']);calendar=paths.resolve(config['calendar']);identity=paths.resolve(config['code_changes'])
    if target.exists() or not target.is_relative_to(paths.work) or any(target.is_relative_to(p) or p.is_relative_to(target) for p in [config_file,parent,calendar,identity]):raise ValueError('fresh separate external intake output required')
    summary=json.loads((parent/'summary.json').read_text(encoding='utf8'));expected=summary.get('output_hashes',{}).get('actions.csv',summary.get('actions_sha256'))
    if not expected or sha256(parent/'actions.csv')!=expected:raise ValueError('unchanged previously reviewed actions required')
    actions=pd.read_csv(parent/'actions.csv',float_precision='round_trip',low_memory=False)
    dates=pd.read_csv(calendar,dtype=str)
    if not {'calendar_date','is_trading_day'}.issubset(dates) or not dates.is_trading_day.isin(['0','1']).all():raise ValueError('known exchange calendar required')
    cal=pd.DatetimeIndex(pd.to_datetime(dates.loc[dates.is_trading_day.eq('1'),'calendar_date']))
    if cal.empty or cal.hasnans or cal.has_duplicates or not cal.is_monotonic_increasing or start<cal[0] or end>cal[-1]:raise ValueError('review window must be within ordered known calendar')
    roots,_,_=code_change_maps(pd.read_json(identity,convert_dates=False))
    source_hashes={paths.name(p):sha256(p) for p in [config_file,parent/'summary.json',parent/'actions.csv',calendar,identity]};codes=code_hashes(CODES)
    target.mkdir(parents=True);proof=dict(schema='company_action_candidate_intake_v1',status='acquiring',signal_date=str(signal.date()),started_at_utc=started.isoformat(),
        source_hashes=source_hashes,code_hashes=codes,window_start=str(start.date()),window_end=str(end.date()),prediction_ready=False,
        candidates_applied=False,existing_holder_terms_changed=False,original_vintages_verified=False,certified=False,
        limitations=['Provider dividend plans do not supply verified cash-payment dates and are not account payment events.',
                     'A bounded report-period/execution-window intake is not complete corporate-action coverage; rights issues and terminal settlements need separate sources.',
                     'Terms and notice clocks are supplier claims until primary review; ST old holdings must retain their legitimate entitlements.'])
    def save():write_manifest(target/'intake.json',proof)
    save();fetch=fetch or get_dividend_page
    try:
        captured=download_dividend_market(target/'raw_plans',str(first.date()),str(last.date()),fetch=lambda d,p:fetch_with_retry(lambda:fetch(d,p)))
        if captured['status']!='downloaded_raw':
            proof.update(status='partial_raw_no_candidate_admission',completed_at_utc=datetime.now(timezone.utc).isoformat());save();return proof
        frames=[]
        for period,item in captured['periods'].items():
            d=day(period);folder=target/'raw_plans'/f'{d.year}Q{d.quarter}'
            for name,digest in item['hashes'].items():
                if Path(name).name!=name or sha256(folder/name)!=digest:raise ValueError('raw plan source changed')
            frame=pd.read_csv(folder/'records.csv',float_precision='round_trip')
            frame['source_period']=period;frame['source_file']=paths.name(folder/'records.csv');frame['source_row']=np.arange(len(frame));frame['observed_at_utc']=item['retrieved_at'];frames.append(frame)
        raw=pd.concat(frames,ignore_index=True)
        coverage,queue,absent=classify_plans(raw,actions,cal,start,end,roots)
        for frame,name in [(coverage,'coverage.csv'),(queue,'notice_review_queue.csv'),(absent,'prior_events_not_observed_in_window.csv')]:frame.to_csv(target/name,index=False)
        for name,digest in source_hashes.items():
            if sha256(paths.resolve(name))!=digest:raise ValueError('prior action source changed during intake')
        if code_hashes(CODES)!=codes:raise ValueError('intake implementation changed')
        proof.update(status='raw_candidates_classified_primary_review_pending',completed_at_utc=datetime.now(timezone.utc).isoformat(),raw_rows=len(raw),review_queue_rows=len(queue),
                     prior_events_in_window_not_observed=len(absent),status_counts=coverage.status.value_counts().to_dict(),
                     output_hashes={p.relative_to(target).as_posix():sha256(p) for p in target.rglob('*') if p.is_file() and p!=target/'intake.json'})
        save();return proof
    except BaseException as error:
        proof.update(status='failed',error=str(error),completed_at_utc=datetime.now(timezone.utc).isoformat());save();raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project',default='.')
    for name in ['work-root','inputs','out']:parser.add_argument('--'+name,required=True)
    result=intake(**vars(parser.parse_args()));print(json.dumps({k:result.get(k) for k in ['status','raw_rows','review_queue_rows','status_counts','candidates_applied']},ensure_ascii=False))
    if result['status']!='raw_candidates_classified_primary_review_pending':raise SystemExit(2)

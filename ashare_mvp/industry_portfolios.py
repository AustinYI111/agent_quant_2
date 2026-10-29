"""Continuous industry/combined ranking accounts against immutable matched NAVs."""
from pathlib import Path
from datetime import datetime, timezone
import argparse, hashlib, json
import numpy as np
import pandas as pd
from .phase2_protocol import load_seal, sha256
from .candidate_portfolios import mature_schedule, account_quotes, paired_net_returns
from .horizon_labels import HORIZONS, label_interval
from .horizon_research import compare_index
from .data import Dataset
from .model import Config
from .pipeline import simulate_monthly
from .causal_quality import HeldValuationError


def matched_family(fitting, accounts, controls, protocol, control_path, control_sha):
    """Reject different training families even when their headline counts match."""
    for item, status in [(fitting,'candidate_fits_complete_selection_pending'),
                         (controls,'candidate_fits_complete_selection_pending'),
                         (accounts,'ranking_accounts_evaluated_selection_pending')]:
        if item.get('status') != status or item.get('independent_data_used') is not False or item.get('model_selection_performed') is not False or item.get('formal_model_promoted') is not False:
            raise ValueError('completed research-only inputs without selection required')
    if len({item.get('protocol_sha256') for item in [fitting,accounts,controls]}) != 1:
        raise ValueError('matching sealed protocol required')
    if fitting['source_hashes'].get(control_path) != control_sha or accounts['source_hashes'].get(control_path) != control_sha:
        raise ValueError('candidate and baseline account training lineage differs')
    expected={(h,g,t,s) for h in protocol['horizons'] for g in ['industry','combined'] for t in protocol['validation']['candidate_estimators'] for s,e in protocol['validation']['research_validation_windows']}
    actual=[(r['horizon'],r['group'],r['n_estimators'],r['validation_start']) for r in fitting['models']]
    if len(actual)!=len(expected) or set(actual)!=expected:
        raise ValueError('complete unique fixed industry family required')
    baseline={(r['horizon'],r['n_estimators'],r['validation_start']):r for r in controls['models'] if r['group']=='baseline'}
    if len(baseline)!=len(expected)//2:
        raise ValueError('complete matched baseline models required')
    for r in fitting['models']:
        b=baseline.get((r['horizon'],r['n_estimators'],r['validation_start']))
        fields=['train_keys_sha256','prediction_keys_sha256','max_label_end','cutoff','train_rows','prediction_rows']
        if b is None or r['matched_control_model_sha256']!=b['model_sha256'] or any(r[k]!=b[k] for k in fields):
            raise ValueError('industry candidate differs from matched training control')
    records=[r for r in accounts['accounts'] if r['group']=='baseline']
    expected_books={(h,t) for h in protocol['horizons'] for t in protocol['validation']['candidate_estimators']}
    if len(records)!=len(expected_books) or {(r['horizon'],r['n_estimators']) for r in records}!=expected_books or any(r['status']!='complete_calendar_mature_ranking_account' for r in records):
        raise ValueError('complete unique baseline accounts required; no valid-prefix inference')
    return {(r['horizon'],r['n_estimators']):r for r in records}


def complete_ranking_family(fitting,controls,protocol):
    expected={(h,g,t,s) for h in protocol['horizons'] for g in ['baseline','market_state','oneil_box'] for t in protocol['validation']['candidate_estimators'] for s,e in protocol['validation']['research_validation_windows']}
    actual=[(r['horizon'],r['group'],r['n_estimators'],r['validation_start']) for r in controls['models']]
    if len(actual)!=len(expected) or set(actual)!=expected:
        raise ValueError('complete unique original three-group family required')
    baseline={(r['horizon'],r['n_estimators'],r['validation_start']):r for r in controls['models'] if r['group']=='baseline'}
    for r in controls['models']+fitting['models']:
        b=baseline[(r['horizon'],r['n_estimators'],r['validation_start'])]
        if any(r[k]!=b[k] for k in ['train_keys_sha256','prediction_keys_sha256','train_rows','prediction_rows','max_label_end','cutoff','validation_end']):
            raise ValueError('full-family training or prediction contract differs')
    return ['baseline','market_state','oneil_box','industry','combined']


def evaluate_industry_rankings(project, training, baseline_accounts, controls, verification, dataset, benchmark, seal_directory, out,valuation_inputs=None,full_family=False):
    if valuation_inputs is not None and not full_family:
        raise ValueError('changed valuation inputs require full-family baseline replay')
    root=Path(project).resolve()
    def local(value):
        p=Path(value)
        if p.is_absolute() or '..' in p.parts or ':' in str(p):
            raise ValueError('project-relative paths required')
        result=(root/p).resolve()
        if not result.is_relative_to(root):
            raise ValueError('path escapes project')
        return result
    trained,base,control,checked,data,index,sealed,target=map(local,[training,baseline_accounts,controls,verification,dataset,benchmark,seal_directory,out])
    if target.exists() or any(target.is_relative_to(p) or p.is_relative_to(target) for p in [trained,base,control,data,index,sealed]) or checked.is_relative_to(target):
        raise ValueError('fresh nonoverlapping output required')
    def read(path): return json.loads(path.read_text(encoding='utf8'))
    protocol,seal=load_seal(sealed,root)
    fitting,previous,reference,verified=map(read,[trained/'manifest.json',base/'manifest.json',control/'manifest.json',checked])
    control_key=(control/'manifest.json').relative_to(root).as_posix()
    books=matched_family(fitting,previous,reference,protocol,control_key,sha256(control/'manifest.json'))
    groups=complete_ranking_family(fitting,reference,protocol) if full_family else ['industry','combined']
    if fitting['protocol_sha256']!=seal['protocol_sha256'] or verified.get('status')!='sixty_models_verified_selection_pending' or verified.get('models')!=60 or verified.get('training_manifest_sha256')!=sha256(trained/'manifest.json') or verified.get('all_rankings_and_ordered_fold_keys_verified') is not True or verified.get('strict_label_embargo_verified') is not True:
        raise ValueError('independently verified fixed training family required')
    sources={}
    def bind(path,expected=None):
        digest=sha256(path)
        if expected is not None and digest!=expected:
            raise ValueError('industry account input changed: '+path.name)
        sources[path.relative_to(root).as_posix()]=digest
    for path in [trained/'manifest.json',base/'manifest.json',control/'manifest.json',checked]: bind(path)
    for contract in [fitting['source_hashes'],previous['source_hashes']]:
        for name,digest in contract.items(): bind(local(name),digest)
    for name,digest in previous['output_hashes'].items(): bind(local((base/name).relative_to(root).as_posix()),digest)
    model_records=[(r,trained) for r in fitting['models']]+([(r,control) for r in reference['models']] if full_family else [])
    for r,model_root in model_records:
        p=local((model_root/r['relative_model_path']).relative_to(root).as_posix())
        for file,digest in [(p,r['model_sha256']),(p.with_name('metadata.json'),r['metadata_sha256']),(p.with_name('rankings.csv'),r['rankings_sha256'])]: bind(file,digest)
    meta=read(data/'metadata.json')
    for name in ['metadata.json','calendar.csv']:
        key=(data/name).relative_to(root).as_posix()
        if previous['source_hashes'].get(key)!=fitting['source_hashes'].get(key) or key not in previous['source_hashes']:
            raise ValueError('identical raw source and calendar required')
    raw_key=(data/'event_raw_prices.csv').relative_to(root).as_posix()
    # Training binds adjusted feature prices, whereas accounting uses raw prices.
    # Their shared immutable metadata binds both, without inventing a training
    # dependency on the raw-share file that the learner never opened.
    bind(data/'event_raw_prices.csv',meta['converted_hashes']['event_raw_prices.csv'])
    if previous['source_hashes'].get(raw_key)!=meta['converted_hashes']['event_raw_prices.csv']:
        raise ValueError('baseline raw prices differ from shared dataset metadata')
    if meta.get('synthetic') is not False:
        raise ValueError('real research data required')
    calendar=pd.DatetimeIndex(pd.to_datetime(pd.read_csv(data/'calendar.csv').date))
    if calendar.max()>pd.Timestamp(protocol['research_end']):
        raise ValueError('independent data cannot enter research comparison')
    bmeta=read(index/'summary.json')
    if bmeta.get('return_kind')!='price_index': raise ValueError('explicit price index required')
    for name in ['summary.json','raw_prices.csv']:
        key=(index/name).relative_to(root).as_posix()
        bind(index/name,previous['source_hashes'].get(key,'missing'))
    benchmark_frame=pd.read_csv(index/'raw_prices.csv',parse_dates=['date'],float_precision='round_trip')
    actions=pd.read_csv(base/'effective_valuation_actions.csv',float_precision='round_trip',low_memory=False)
    for col in ['ex_date','available_date','pay_date']: actions[col]=pd.to_datetime(actions[col],format='mixed')
    names=set(previous['code_hashes'])|set(fitting['code_hashes'])|{'industry_portfolios.py','candidate_portfolios.py'}
    updates=None
    if valuation_inputs is not None:
        updated=local(valuation_inputs)
        if target.is_relative_to(updated) or updated.is_relative_to(target): raise ValueError('valuation output overlaps source')
        updates=read(updated/'manifest.json')
        if updates.get('schema')!='phase2_reviewed_valuation_updates_v1' or updates.get('status')!='reviewed_valuation_updates_ready' or updates.get('independent_data_used') is not False or updates.get('training_features_or_labels_changed') is not False or updates.get('formal_model_promoted') is not False:
            raise ValueError('reviewed research-only valuation update required')
        if updates.get('parent_file')!=(base/'effective_valuation_actions.csv').relative_to(root).as_posix() or updates.get('parent_sha256')!=sha256(base/'effective_valuation_actions.csv'):
            raise ValueError('valuation update parent differs from original baseline actions')
        bind(updated/'manifest.json');bind(updated/'effective_valuation_actions.csv',updates['output_sha256'])
        for name,digest in updates['source_hashes'].items():bind(local(name),digest)
        names.update(updates['code_hashes'])
        actions=pd.read_csv(updated/'effective_valuation_actions.csv',float_precision='round_trip',low_memory=False)
        for col in ['ex_date','available_date','pay_date']:actions[col]=pd.to_datetime(actions[col],format='mixed')
    def code_hashes(): return {n:hashlib.sha256((root/'ashare_mvp'/n).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest() for n in sorted(names)}
    code=code_hashes()
    for contract in [previous['code_hashes'],fitting['code_hashes']]+([updates['code_hashes']] if updates else []):
        if any(code[n]!=digest for n,digest in contract.items()): raise ValueError('original training or accounting code changed')
    target.mkdir(parents=True)
    actions.to_csv(target/'effective_valuation_actions.csv',index=False)
    proof=dict(schema='phase2_full_family_ranking_accounts_v1' if full_family else 'phase2_industry_ranking_accounts_v1',status='evaluating',started_at_utc=datetime.now(timezone.utc).isoformat(),protocol_sha256=seal['protocol_sha256'],source_hashes=sources,code_hashes=code,accounts=[],paired_diagnostics=[],reused_baseline_accounts=0 if full_family else 6,independent_data_used=False,model_selection_performed=False,formal_model_promoted=False,certified=False,valuation_updates=updates,design='continuous raw-share accounts; common valuation actions, costs and mature calendar; all baselines replayed when economics change; no fold reset or final liquidation',pending=['timing accounts','eligible equal-weight control','industry attribution','fixed-family selection review','independent mature validation'],limitations=['historical industry source vintages uncertified','raw prices and executable limits not fully certified','fractional shares and gross dividend entitlements','CSI300 price index excludes dividends'])
    def save(): (target/'manifest.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
    save()
    try:
        for horizon in protocol['horizons']:
            for trees in protocol['validation']['candidate_estimators']:
                old_baseline=pd.read_csv(base/horizon/str(trees)/'baseline/daily_nav.csv',parse_dates=['date'],float_precision='round_trip')
                baseline=None if full_family else old_baseline
                for group in groups:
                    records=[(r,p) for r,p in model_records if r['horizon']==horizon and r['n_estimators']==trees and r['group']==group]
                    pieces=[]
                    for r,model_root in records:
                        p=model_root/r['relative_model_path']
                        ranks=pd.read_csv(p.with_name('rankings.csv'),parse_dates=['date','available_date'],dtype={'symbol':str},float_precision='round_trip')
                        if len(ranks)!=r['prediction_rows'] or not ranks.date.between(r['validation_start'],r['validation_end']).all() or ranks.available_date.gt(ranks.date).any() or not ranks.horizon.eq(horizon).all(): raise ValueError('ranking contract changed')
                        pieces.append(ranks)
                    ranks=pd.concat(pieces,ignore_index=True)
                    if ranks.duplicated(['date','symbol']).any() or not np.isfinite(ranks.score).all(): raise ValueError('unique finite ranks required')
                    signals,last=mature_schedule(ranks.date.unique(),calendar,horizon)
                    first=signals[0]; dates=calendar[calendar<=last]
                    expected_dates=dates[dates>=label_interval(calendar,first,HORIZONS[horizon])[0]]
                    if not pd.DatetimeIndex(old_baseline.date).equals(expected_dates) or books[(horizon,trees)]['first_signal']!=str(first.date()) or books[(horizon,trees)]['requested_end']!=str(last.date()): raise ValueError('baseline dates differ; no survivor-prefix comparison')
                    picks=ranks[ranks.date.isin(signals)].sort_values(['date','score','symbol'],ascending=[True,False,True]).groupby('date',observed=True).head(protocol['execution']['top_k']).copy()
                    if not picks.groupby('date').size().eq(protocol['execution']['top_k']).all(): raise ValueError('full Top20 at every rebalance required')
                    picks['target_weight']=1/protocol['execution']['top_k']
                    symbols=set(picks.symbol);raw=account_quotes(data/'event_raw_prices.csv',symbols,first,last)
                    prices=raw[['date','symbol']].copy();tradable=raw.tradestatus.eq('1')&raw.open.gt(0)
                    prices['can_buy']=tradable;prices['can_sell']=tradable;prices['total_return_open']=raw.open.where(tradable,raw.preclose)
                    events=actions[actions.symbol.isin(symbols)&actions.ex_date.between(first,last)].copy()
                    book=Dataset(prices,pd.DataFrame(),dates,meta)
                    folder=target/horizon/str(trees)/group;folder.mkdir(parents=True)
                    picks.to_csv(folder/'scheduled_top20.csv',index=False)
                    record=dict(horizon=horizon,n_estimators=trees,group=group,scheduled_rebalances=len(signals),first_signal=str(first.date()),requested_end=str(last.date()),raw_symbols=int(raw.symbol.nunique()),costs=dict(commission_bps=3.,slippage_bps=5.,sell_stamp_duty='dated existing schedule'))
                    try:
                        nav,orders=simulate_monthly(book,picks,Config(top_k=20,commission_bps=3.,slippage_bps=5.),raw,events,reference_tick_size=.01,causal_quality_research=True)
                    except HeldValuationError as error:
                        error.nav.to_csv(folder/'partial_valid_nav.csv',index=False);error.trades.to_csv(folder/'partial_orders.csv',index=False)
                        record.update(status='incomplete_held_valuation',symbol=error.symbol,date=str(error.date.date()),reason=error.reason,holdings=error.holdings,limitation='valid prefix only; no full-period result or inference')
                        diagnostic=dict(horizon=horizon,n_estimators=trees,group=group,status='incomplete_account_pair_no_inference')
                        error.__traceback__=None
                    else:
                        if not pd.DatetimeIndex(nav.date).equals(expected_dates): raise ValueError('missing requested valuation sessions')
                        daily,metrics=compare_index(nav,orders,benchmark_frame,'price_index')
                        for value,name in [(nav,'daily_nav.csv'),(orders,'orders.csv'),(daily,'index_comparison.csv')]: value.to_csv(folder/name,index=False)
                        record.update(status='complete_calendar_mature_ranking_account',metrics=metrics)
                        if group=='baseline':
                            baseline=nav
                            record['previous_baseline_max_absolute_nav_difference']=float(np.max(np.abs(nav.nav.to_numpy()-old_baseline.nav.to_numpy())))
                            diagnostic=None
                        elif baseline is not None:
                            excess,diagnostic=paired_net_returns(nav,baseline,seed=protocol['validation']['seed'])
                            diagnostic.update(horizon=horizon,n_estimators=trees,group=group)
                            pd.DataFrame(dict(date=nav.date,paired_net_return_difference=excess)).to_csv(folder/'paired_daily.csv',index=False)
                        else:
                            diagnostic=dict(horizon=horizon,n_estimators=trees,group=group,status='incomplete_baseline_pair_no_inference')
                    (folder/'summary.json').write_text(json.dumps(record,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
                    proof['accounts'].append(record)
                    if group!='baseline':proof['paired_diagnostics'].append(diagnostic)
                    save()
                    print(json.dumps({k:record[k] for k in ['horizon','n_estimators','group','status']}),flush=True)
                    del raw,prices,events,book,picks,ranks,pieces
        for name,digest in sources.items():
            if sha256(root/name)!=digest: raise ValueError('account source changed during evaluation')
        if code_hashes()!=code: raise ValueError('account code changed during evaluation')
        if len(proof['accounts'])!=(30 if full_family else 12):raise ValueError('complete fixed account family must be attempted')
        proof.update(status='full_family_ranking_accounts_evaluated_selection_pending' if full_family else 'industry_ranking_accounts_evaluated_selection_pending',completed_at_utc=datetime.now(timezone.utc).isoformat(),output_hashes={p.relative_to(target).as_posix():sha256(p) for p in target.rglob('*') if p.is_file() and p.name!='manifest.json'})
        save();return proof
    except BaseException as error:
        proof.update(status='failed',error=str(error));save();raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project',default='.')
    for name in ['training','baseline-accounts','controls','verification','dataset','benchmark','seal-directory','out']: parser.add_argument('--'+name,required=True)
    parser.add_argument('--valuation-inputs')
    parser.add_argument('--full-family',action='store_true')
    evaluate_industry_rankings(**vars(parser.parse_args()))

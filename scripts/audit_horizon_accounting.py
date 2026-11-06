from pathlib import Path
from datetime import datetime,timezone
import argparse,json,hashlib
import pandas as pd
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.accounting_attribution import attribute_open_account
parser=argparse.ArgumentParser(description='Independently reconcile an existing sealed three-horizon research account; no model fit or order revision.')
parser.add_argument('--project',default='.')
parser.add_argument('--replay',default='artifacts/three_horizon_valuation_replay5')
parser.add_argument('--data',default='artifacts/market_ratios_causal_quality1')
parser.add_argument('--benchmark',default='artifacts/benchmark_csi300_price_2018_2025')
parser.add_argument('--out',required=True)
args=parser.parse_args();root=Path(args.project).resolve()
def local(value):
    p=Path(value)
    if p.is_absolute() or '..' in p.parts or ':' in value:raise ValueError('project-relative accounting paths required')
    result=(root/p).resolve()
    if not result.is_relative_to(root):raise ValueError('accounting path escapes project')
    return result
source=local(args.replay);dataset=local(args.data);benchmark=local(args.benchmark)/'raw_prices.csv';out=local(args.out)
if out.exists():raise ValueError('new accounting audit output required')
for directory in [source,dataset,benchmark.parent]:
    if out.is_relative_to(directory) or directory.is_relative_to(out):raise ValueError('accounting output must be separate from immutable inputs')
audit=json.loads((source/'valuation_replay_audit.json').read_text(encoding='utf8'));meta=json.loads((dataset/'metadata.json').read_text(encoding='utf8'))
if audit['status']!='completed_all_three_portfolios' or audit['models_retrained'] or audit['actual_economic_terms_changed']:raise ValueError('complete unchanged three-horizon source required')
if sha256(dataset/'metadata.json')!=audit['dataset_metadata_sha256']:raise ValueError('source dataset metadata changed')
if not sha256(dataset/'event_raw_prices.csv')==meta['converted_hashes']['event_raw_prices.csv']==audit['sealed_dataset_files_sha256']['event_raw_prices.csv']:raise ValueError('source raw quote hash differs')
if sha256(source/'effective_valuation_actions.csv')!=audit['effective_actions_sha256'] or sha256(benchmark)!=audit['benchmark_sha256']:raise ValueError('source action/benchmark hash differs')
books={};symbols=set();hashes={}
for horizon in ['5d','20d','12m']:
    folder=source/'portfolios'/horizon;nav=pd.read_csv(folder/'daily_nav.csv',parse_dates=['date'],float_precision='round_trip');orders=pd.read_csv(folder/'orders.csv',parse_dates=['date'],float_precision='round_trip');summary=json.loads((folder/'summary.json').read_text(encoding='utf8'))
    if len(nav)!=summary['net_portfolio']['sessions'] or abs(nav.nav.iloc[-1]-1-summary['net_portfolio']['total_return'])>=1e-14:raise ValueError('source NAV differs from research summary')
    if summary['costs']['commission_bps']!=3 or summary['costs']['slippage_bps']!=5:raise ValueError('source execution assumptions differ')
    books[horizon]=(nav,orders);symbols.update(orders.symbol)
    for file in [folder/'daily_nav.csv',folder/'orders.csv',folder/'summary.json']:hashes[file.relative_to(root).as_posix()]=sha256(file)
first=min(nav.date.min() for nav,_ in books.values());last=max(nav.date.max() for nav,_ in books.values())
chunks=[]
for frame in pd.read_csv(dataset/'event_raw_prices.csv',usecols=['date','symbol','open','close','preclose','tradestatus'],dtype={'symbol':str,'tradestatus':str},parse_dates=['date'],float_precision='round_trip',chunksize=200000):
    selected=frame[frame.symbol.isin(symbols)&frame.date.between(first,last)]
    if not selected.empty:chunks.append(selected)
quotes=pd.concat(chunks,ignore_index=True)
actions=pd.read_csv(source/'effective_valuation_actions.csv',float_precision='round_trip',low_memory=False)
for c in ['ex_date','available_date','pay_date']:actions[c]=pd.to_datetime(actions[c],format='mixed')
actions=actions[actions.symbol.isin(symbols)&actions.ex_date.between(first,last)]
b=pd.read_csv(benchmark,parse_dates=['date']);out.mkdir(parents=True,exist_ok=False)
proof=dict(schema='historical_real_accounting_attribution_v1',status='building',started_at_utc=datetime.now(timezone.utc).isoformat(),source_replay=source.relative_to(root).as_posix(),source_replay_audit_sha256=sha256(source/'valuation_replay_audit.json'),source_raw_sha256=meta['converted_hashes']['event_raw_prices.csv'],actions_sha256=audit['effective_actions_sha256'],benchmark_sha256=audit['benchmark_sha256'],accounting_code_sha256=hashlib.sha256((root/'ashare_mvp/accounting_attribution.py').read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest(),source_files_sha256=hashes,books={},certified=False,model_ready=False,models_retrained=False,forecast_or_account_modified=False,industry_attribution_status='pending_dated_source',market_reference_method='unit-beta invested exposure times CSI300 price-index same-open return; not causal alpha')
def save():(out/'manifest.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
save()
try:
    for horizon,(nav,orders) in books.items():
        daily,positions,assets=attribute_open_account(nav,orders,quotes,actions,benchmark=b)
        directory=out/horizon;directory.mkdir()
        for name,frame in [('daily_accounting',daily),('position_history',positions),('asset_attribution',assets)]:frame.to_csv(directory/(name+'.csv'),index=False)
        values={name:float(daily[name].sum()) for name in ['price_pnl','gross_dividend_entitlement','dividends_paid_cash','commission_cost','slippage_cost','stamp_duty_cost','total_execution_cost','unit_beta_market_reference','price_residual_after_market']}
        expected=values['price_pnl']+values['gross_dividend_entitlement']-values['total_execution_cost']
        if abs(expected-(nav.nav.iloc[-1]-1))>=1e-10:raise ValueError('total accounting change does not reconcile')
        proof['books'][horizon]=dict(sessions=len(daily),first=str(nav.date.min().date()),last=str(nav.date.max().date()),position_rows=len(positions),asset_rows=len(assets),net_total_return=float(nav.nav.iloc[-1]-1),accounting_sum=expected,max_daily_reconciliation_error=float(daily.reconciliation_error.abs().max()),contributions_in_initial_capital_units=values)
        save();print(json.dumps(dict(horizon=horizon,result=proof['books'][horizon])),flush=True)
    for file,digest in hashes.items():
        if sha256(root/file)!=digest:raise ValueError('source portfolio changed during audit')
    proof.update(status='all_three_historical_accounts_reconciled',completed_at_utc=datetime.now(timezone.utc).isoformat(),output_hashes={p.relative_to(out).as_posix():sha256(p) for p in out.rglob('*.csv')});save()
except BaseException as error:proof.update(status='failed',error=str(error));save();raise

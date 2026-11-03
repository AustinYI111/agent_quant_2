"""Independent cash/share replay of saved prospective-paper followups.

Does not call the trade engine, position reconstruction or accounting helpers.
Prices observed later can value a pre-existing forecast; they cannot create one.
"""
from pathlib import Path
from datetime import datetime,timezone
import argparse,json,math,sqlite3
from decimal import Decimal,ROUND_HALF_UP
import numpy as np
import pandas as pd
from ashare_mvp.workspace_paths import WorkspacePaths
from ashare_mvp.phase2_protocol import sha256


def audit(project,work_root,source,actions,archive,expected_archive_sha256,out):
    paths=WorkspacePaths(project,work_root);folder=paths.resolve(source);target=paths.resolve(out);events_file=paths.resolve(actions);database=paths.resolve(archive)
    assert not target.exists() and target.is_relative_to(paths.work) and not target.is_relative_to(folder) and not folder.is_relative_to(target)
    proof=json.loads((folder/'followup.json').read_text(encoding='utf8'))
    assert proof['status']=='observed_market_window_and_paper_replay_finished' and not proof['new_forecasts_created'] and not proof['backfilled_forecasts_executed']
    for name,digest in proof['source_hashes'].items():assert sha256(paths.resolve(name))==digest
    for name,digest in proof['output_hashes'].items():assert sha256(paths.resolve(paths.name(folder)+'/'+name))==digest
    assert proof['source_hashes'][paths.name(events_file)]==sha256(events_file)
    assert sha256(database)==expected_archive_sha256==proof['prediction_archive_sha256']
    raw=pd.read_csv(folder/'raw_window.csv',parse_dates=['date'],dtype={'tradestatus':str,'isST':str},float_precision='round_trip')
    assert not raw.duplicated(['date','symbol']).any();quotes=raw.set_index(['date','symbol'])
    events=pd.read_csv(events_file,parse_dates=['ex_date','available_date','pay_date'],float_precision='round_trip',low_memory=False)
    result=[]
    con=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)
    try:
        for h,book in proof['paper']['books'].items():
            if book['status']=='awaiting_prospective_scheduled_entry':
                assert book['nav_rows']==0 and book['orders']==0
                result.append(dict(horizon=h,status='awaiting_entry_no_invented_nav',sessions=0,orders=0));continue
            base=folder/'paper'/h;nav=pd.read_csv(base/'open_nav.csv',parse_dates=['date'],float_precision='round_trip')
            orders=pd.read_csv(base/'orders.csv',parse_dates=['date'],float_precision='round_trip');picks=pd.read_csv(base/'scheduled_top20.csv',parse_dates=['date'])
            schedules={}
            for signal,g in picks.groupby('date'):
                assert len(g)==20 and not g.symbol.duplicated().any() and g.batch_id.nunique()==1
                batch_id=g.batch_id.iloc[0];row=con.execute('SELECT receipt_json FROM batches WHERE batch_id=?',(batch_id,)).fetchone();assert row is not None
                receipt=json.loads(row[0]);assert receipt['prospective'] and receipt['horizon']==h and pd.Timestamp(receipt['signal_date'])==signal
                view=proof['paper']['selection_views'][str(signal.date())];directory=paths.resolve(view['directory']);assert sha256(directory/'manifest.json')==view['manifest_sha256']
                status=json.loads((directory/'manifest.json').read_text(encoding='utf8'));assert status['horizons'][h]['batch_id']==batch_id
                top=directory/f'top20_{h}.csv';assert sha256(top)==status['output_hashes'][top.name]
                selected=pd.read_csv(top);assert selected.symbol.tolist()==g.symbol.tolist()
                entry=pd.Timestamp(receipt['next_entry_open_utc'])
                schedules[entry.tz_convert('Asia/Shanghai').tz_localize(None).normalize()]=set(g.symbol)
                assert pd.Timestamp(receipt['created_at_utc'])<entry and pd.Timestamp(status['created_at_utc'])<entry
                assert orders.loc[orders.date.ge(signal),'date'].min()>signal
            shares={};cash=1.;receivables=[];maximum=0.;cost_total=0.;filled_buy_keys=0;previous=1.
            for r in nav.itertuples(index=False):
                date=r.date
                def price(symbol):
                    assert (date,symbol) in quotes.index
                    q=quotes.loc[(date,symbol)];v=q.open if q.tradestatus=='1' and q.open>0 else q.preclose
                    assert math.isfinite(v) and v>0;return float(v)
                dates=pd.DatetimeIndex(sorted(raw.date.unique()));ix=dates.get_indexer([date])[0]
                for symbol,quantity in shares.items():
                    assert ix>0 and (dates[ix-1],symbol) in quotes.index
                    before=quotes.loc[(dates[ix-1],symbol)];current=quotes.loc[(date,symbol)]
                    event=events[events.symbol.eq(symbol)&events.ex_date.eq(date)];assert len(event)<=1
                    cash_reference=0.;multiplier=1.
                    if len(event):
                        e=event.iloc[0];cash_reference=e.get('reference_cash_per_share',np.nan);multiplier=e.get('reference_share_multiplier',np.nan)
                        if pd.isna(cash_reference):cash_reference=e.cash_per_share
                        if pd.isna(multiplier):multiplier=e.share_multiplier
                    expected=(Decimal(str(before.close))-Decimal(str(cash_reference)))/Decimal(str(multiplier))
                    rounded=expected.quantize(Decimal('.01'),rounding=ROUND_HALF_UP);observed=Decimal(str(current.preclose))
                    assert abs(observed-expected)<=Decimal('.00000001') or abs(observed-rounded)<=Decimal('.00000001'), 'unreconciled held quote reference'
                for e in events[events.ex_date.eq(date)].itertuples(index=False):
                    quantity=shares.get(e.symbol,0.)
                    if not quantity:continue
                    assert e.available_date<=date and math.isfinite(e.cash_per_share) and e.cash_per_share>=0 and e.share_multiplier>0
                    amount=quantity*e.cash_per_share
                    if amount:receivables.append((e.pay_date,amount))
                    shares[e.symbol]=quantity*e.share_multiplier
                cash+=math.fsum(v for pay,v in receivables if pay<date);receivables=[(pay,v) for pay,v in receivables if pay>=date]
                for t in orders[orders.date.eq(date)].itertuples(index=False):
                    assert date in schedules
                    assert t.side in ['buy','sell'] and t.status in ['filled','blocked']
                    assert math.isfinite(t.notional) and t.notional>=0 and math.isfinite(t.cost) and t.cost>=0
                    if t.status=='blocked':assert t.cost==0 and t.stamp_duty==0;continue
                    if t.side=='buy':assert t.symbol in schedules[date], 'filled buy is absent from authorized forecast'
                    q=quotes.loc[(date,t.symbol)];assert q.tradestatus=='1' and q.open>0
                    sell=t.side=='sell';tax=.0005 if sell else 0.;expected_cost=t.notional*(.0008+tax)
                    assert abs(t.cost-expected_cost)<=1e-12 and abs(t.stamp_duty-t.notional*tax)<=1e-12
                    if not sell:
                        assert t.symbol in schedules[date]
                        assert q.isST=='0';filled_buy_keys+=1
                    sign=-1 if sell else 1;shares[t.symbol]=shares.get(t.symbol,0.)+sign*t.notional/q.open;cash-=sign*t.notional+t.cost;cost_total+=t.cost
                    assert shares[t.symbol]>=-1e-10
                assert cash>=-1e-9;cash=max(0.,cash);shares={s:q for s,q in shares.items() if q>1e-12}
                pending=math.fsum(v for pay,v in receivables);wealth=cash+pending+math.fsum(q*price(s) for s,q in shares.items())
                error=max(abs(wealth-r.nav),abs(cash-r.cash),abs(pending-r.dividend_receivable));assert error<=1e-12 and r.positions==len(shares);maximum=max(maximum,error);previous=r.nav
            positions=pd.read_csv(base/'positions.csv',float_precision='round_trip')
            assert set(positions.symbol)==set(shares)
            for r in positions.itertuples(index=False):assert abs(r.shares-shares[r.symbol])<=1e-12
            comparison=pd.read_csv(base/'benchmark_comparison.csv',float_precision='round_trip') if (base/'benchmark_comparison.csv').exists() else None
            result.append(dict(horizon=h,status='all_saved_account_opens_independently_reconstructed',sessions=len(nav),orders=len(orders),filled_non_st_buy_keys=filled_buy_keys,
                               first_valuation=str(nav.date.iloc[0].date()),last_valuation=str(nav.date.iloc[-1].date()),last_nav=float(nav.nav.iloc[-1]),
                               total_return_from_initial_wealth=float(nav.nav.iloc[-1]-1),total_paid_cost_initial_wealth=cost_total,maximum_absolute_cash_share_error=maximum))
    finally:con.close()
    assert sha256(database)==expected_archive_sha256
    audit=dict(schema='independent_archived_paper_followup_audit_v1',status='saved_paper_followup_independently_verified',verified_at_utc=datetime.now(timezone.utc).isoformat(),
               followup_manifest_sha256=sha256(folder/'followup.json'),accounts=result,archive_sha256=expected_archive_sha256,archive_unchanged=True,new_predictions_created=False,
               filled_buy_forecast_authorization_checked=True,positive_holding_quote_references_checked=True,verification_code_sha256=sha256(Path(__file__)),
               limitations=['Research open-price simulation with fractional shares and frozen costs; not verified broker fills, preopen source vintages or after-tax performance.',
                            'Only the saved followup window is reconstructed; no claim of mature5d/20d/12m validation or causal alpha.'])
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x',encoding='utf8') as stream:json.dump(audit,stream,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps(audit,ensure_ascii=False));return audit


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['project','work-root','source','actions','archive','expected-archive-sha256','out']:parser.add_argument('--'+name,required=True)
    audit(**vars(parser.parse_args()))

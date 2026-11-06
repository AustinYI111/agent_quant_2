"""Independently regroup timing asset ledgers using historical release dates."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json
import numpy as np
import pandas as pd
from ashare_mvp.workspace_paths import WorkspacePaths


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def verify(project,work_root,attribution,audit,memberships,calendar,out):
    paths=WorkspacePaths(project,work_root)
    folder,accounts,mem,cal,target=map(paths.resolve,[attribution,audit,memberships,calendar,out])
    assert target.is_relative_to(paths.work) and not target.exists()
    assert all(not target.is_relative_to(p) and not p.is_relative_to(target) for p in [folder,accounts,mem,cal])
    proof=json.loads((folder/'manifest.json').read_text(encoding='utf8'))
    assert proof['status']=='all_eighteen_timing_accounts_industry_attributed'
    expected={(h,t,g) for h in ['5d','20d','12m'] for t in [30,60] for g in ['market_state','oneil_box','combined']}
    keys=[(r['horizon'],r['n_estimators'],r['group']) for r in proof['accounts']]
    assert len(keys)==18 and set(keys)==expected
    for name,digest in proof['source_hashes'].items():assert sha(paths.resolve(name))==digest
    for name,digest in proof['code_hashes'].items():
        actual=hashlib.sha256((paths.project/'ashare_mvp'/name).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest()
        assert actual==digest
    assert proof['source_hashes'][paths.name(accounts/'manifest.json')]==sha(accounts/'manifest.json')
    for p in [mem/'memberships.csv',mem/'catalog.csv',cal]:assert proof['source_hashes'][paths.name(p)]==sha(p)
    members=pd.read_csv(mem/'memberships.csv',dtype=str)
    catalog=pd.read_csv(mem/'catalog.csv',parse_dates=['published_date'])
    sessions=pd.DatetimeIndex(pd.read_csv(cal,parse_dates=['date']).date)
    rows=[];terms=['price_pnl','gross_dividend_entitlement','commission_cost','slippage_cost','stamp_duty_cost']
    for row in proof['accounts']:
        rel=Path(row['horizon'])/str(row['n_estimators'])/row['group']
        for name,digest in row['output_hashes'].items():assert sha(paths.resolve(paths.name(folder)+'/'+name))==digest
        daily=pd.read_csv(accounts/rel/'daily_attribution.csv',parse_dates=['date'],float_precision='round_trip').set_index('date')
        assets=pd.read_csv(accounts/rel/'asset_attribution.csv',parse_dates=['date'],float_precision='round_trip')
        positions=pd.read_csv(accounts/rel/'positions.csv',parse_dates=['date'],float_precision='round_trip')
        grouped=pd.read_csv(folder/rel/'industry_daily.csv',parse_dates=['date'],float_precision='round_trip')
        labels=pd.read_csv(folder/rel/'historical_labels.csv',parse_dates=['date','classification_date','published_date'],dtype={'release_id':str})
        assert len(daily)==row['sessions'] and not labels.duplicated(['date','symbol']).any()
        ix=sessions.get_indexer(labels.date);assert (ix>0).all()
        previous=sessions[ix-1];assert pd.DatetimeIndex(labels.classification_date).equals(previous)
        chosen={}
        for date in previous.unique():
            available=catalog[catalog.published_date.lt(date)].sort_values('published_date')
            chosen[pd.Timestamp(date)]=None if available.empty else available.iloc[-1].release_id
        release=labels.classification_date.map(chosen)
        assert labels.release_id.fillna('').equals(release.fillna(''))
        names=labels[['date','symbol']].copy();names['release_id']=release
        names=names.merge(members[['symbol','release_id','industry']],on=['symbol','release_id'],how='left',validate='many_to_one')
        names['industry']=names.industry.fillna('__UNKNOWN__')
        assert names.industry.tolist()==labels.industry.tolist()
        pub=names.release_id.map(catalog.set_index('release_id').published_date)
        assert pub.fillna(pd.Timestamp('1900-01-01')).equals(labels.published_date.fillna(pd.Timestamp('1900-01-01')))
        # Rebuild each industry's money and exposure directly from independently
        # reconciled per-asset ledgers, without importing attribution helpers.
        old=positions[['date','symbol','market_value']].copy()
        loc=sessions.get_indexer(old.date);assert (loc>=0).all()
        next_dates={d:sessions[i+1] if i+1<len(sessions) else pd.NaT for d,i in zip(old.date,loc)}
        old['date']=old.date.map(next_dates);old=old[old.date.isin(daily.index)].rename(columns={'market_value':'starting_market_value'})
        base=assets[['date','symbol']+terms].merge(old,on=['date','symbol'],how='outer',validate='one_to_one')
        assert set(map(tuple,base[['date','symbol']].to_numpy()))==set(map(tuple,names[['date','symbol']].to_numpy()))
        base=base.merge(names[['date','symbol','industry']],on=['date','symbol'],validate='one_to_one')
        base[terms+['starting_market_value']]=base[terms+['starting_market_value']].fillna(0.)
        rebuilt=base.groupby(['date','industry'])[terms+['starting_market_value']].sum()
        absent=daily.index.difference(rebuilt.index.get_level_values('date'))
        if len(absent):
            empty=pd.DataFrame(0.,index=pd.MultiIndex.from_arrays([absent,['__NO_POSITION__']*len(absent)],names=['date','industry']),columns=terms+['starting_market_value'])
            rebuilt=pd.concat([rebuilt,empty])
        actual=grouped.set_index(['date','industry']).sort_index();rebuilt=rebuilt.sort_index()
        assert rebuilt.index.equals(actual.index)
        opening=pd.Series(rebuilt.index.get_level_values('date'),index=rebuilt.index).map(daily.opening_nav)
        market=pd.Series(rebuilt.index.get_level_values('date'),index=rebuilt.index).map(daily.unit_beta_market_reference)
        total=rebuilt.groupby(level='date').starting_market_value.transform('sum')
        rebuilt['starting_equity_weight']=rebuilt.starting_market_value/opening
        rebuilt['cash_and_receivable_weight']=1-total/opening
        rebuilt['allocated_market_reference']=market*rebuilt.starting_market_value/total.where(total.gt(0))
        rebuilt.loc[total.eq(0)&market.eq(0),'allocated_market_reference']=0.
        rebuilt['price_residual_after_market']=rebuilt.price_pnl-rebuilt.allocated_market_reference
        rebuilt['net_pnl']=rebuilt.price_pnl+rebuilt.gross_dividend_entitlement-rebuilt[['commission_cost','slippage_cost','stamp_duty_cost']].sum(axis=1)
        rebuilt['net_return_contribution']=rebuilt.net_pnl/opening
        errors=[]
        for name in rebuilt:
            np.testing.assert_allclose(actual[name],rebuilt[name],rtol=0,atol=1e-10,equal_nan=True)
            error=float((actual[name]-rebuilt[name]).abs().max());errors.append(error if np.isfinite(error) else 0.)
        totals=actual.groupby(level='date').sum(min_count=1)
        for left,right in [(c,c) for c in terms]+[('net_pnl','nav_change'),('net_return_contribution','net_return'),('allocated_market_reference','unit_beta_market_reference'),('price_residual_after_market','price_residual_after_market')]:
            np.testing.assert_allclose(totals[left],daily[right],rtol=0,atol=1e-10,equal_nan=True)
            errors.append(float((totals[left]-daily[right]).abs().max()))
        summary=actual.groupby(level='industry')[terms+['net_pnl','allocated_market_reference','price_residual_after_market']].sum(min_count=1)
        stored=pd.read_csv(folder/rel/'industry_pnl_summary.csv',float_precision='round_trip').set_index('industry').sort_index()
        assert stored.index.equals(summary.index)
        np.testing.assert_allclose(stored[summary.columns],summary,rtol=0,atol=1e-10,equal_nan=True)
        age=(labels.classification_date-labels.published_date).dt.days.dropna()
        rows.append(dict(horizon=row['horizon'],n_estimators=row['n_estimators'],group=row['group'],sessions=len(daily),labels=len(labels),
                         maximum_error=max(errors),maximum_publication_age_days=int(age.max()) if len(age) else None,
                         unknown_labels=int(labels.industry.eq('__UNKNOWN__').sum()),
                         unknown_net_pnl=float(summary.loc[summary.index=='__UNKNOWN__','net_pnl'].sum()),
                         account_net_pnl=float(daily.nav_change.sum()),
                         top_industry=summary.net_pnl.idxmax(),top_industry_net_pnl=float(summary.net_pnl.max()),
                         bottom_industry=summary.net_pnl.idxmin(),bottom_industry_net_pnl=float(summary.net_pnl.min())))
        print(json.dumps(dict(verified=len(rows),maximum_error=max(errors))),flush=True)
    for name,digest in proof['source_hashes'].items():assert sha(paths.resolve(name))==digest
    result=dict(schema='independent_timing_industry_verification_v1',status='all_eighteen_industry_accounts_independently_verified',
                verified_at_utc=datetime.now(timezone.utc).isoformat(),manifest_sha256=sha(folder/'manifest.json'),accounts=rows,
                maximum_error=max(r['maximum_error'] for r in rows),verification_code_sha256=sha(Path(__file__)),
                rebuilt_industry_asset_money_and_starting_exposure=True,independent_data_used=False,model_selection_performed=False,formal_model_promoted=False,
                limitations=proof['limitations'])
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x',encoding='utf8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps(dict(status=result['status'],accounts=len(rows),maximum_error=result['maximum_error'])))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['project','work-root','attribution','audit','memberships','calendar','out']:parser.add_argument('--'+name,required=True)
    verify(**vars(parser.parse_args()))

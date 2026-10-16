"""Chronological horizon training and cost/index comparisons with explicit units."""
from dataclasses import dataclass, asdict
from pathlib import Path
from types import SimpleNamespace
import hashlib,json
import numpy as np
import pandas as pd
from .horizon_labels import HORIZONS,resolve_horizons,label_interval
from .model import fit_ranker
from .pipeline import nav_metrics


def _sessions(calendar):
    cal=pd.DatetimeIndex(calendar)
    if cal.empty or cal.hasnans or cal.has_duplicates or not cal.is_monotonic_increasing or cal.tz is not None or not cal.equals(cal.normalize()):
        raise ValueError('ordered normalized unique experiment calendar required')
    return cal


def chronological_partitions(frame,calendar,validation_start,test_start,test_end,embargo_sessions=1):
    cal=_sessions(calendar);v,t,e=map(pd.Timestamp,[validation_start,test_start,test_end])
    if not v<t<=e or any(d not in cal for d in [v,t,e]):raise ValueError('ordered observed validation/test boundaries required')
    if isinstance(embargo_sessions,bool) or not isinstance(embargo_sessions,int) or embargo_sessions<0:raise ValueError('nonnegative integer embargo required')
    f=frame.copy()
    if not {'date','symbol','available_date','label_end','target'}.issubset(f):raise ValueError('dated feature/label schema required')
    if 'horizon' in f and f.horizon.nunique()!=1:raise ValueError('partition one horizon at a time')
    for name in ['date','available_date','label_end']:
        f[name]=pd.to_datetime(f[name]);known=f[name].dropna()
        if known.dt.tz is not None or not known.eq(known.dt.normalize()).all():raise ValueError('normalized experiment clocks required')
    if f[['date','symbol','available_date']].isna().any().any() or f.duplicated(['date','symbol']).any() or not f.date.isin(cal).all() or f.available_date.gt(f.date).any():raise ValueError('ambiguous or future feature record')
    f['target']=pd.to_numeric(f.target,errors='raise')
    if np.isinf(f.target).any() or (f.target.notna() & (f.label_end.isna() | f.label_end.le(f.date))).any():raise ValueError('invalid mature target interval')
    vc=cal[max(0,cal.get_loc(v)-embargo_sessions)];tc=cal[max(0,cal.get_loc(t)-embargo_sessions)]
    known=f.target.notna() & f.label_end.notna()
    train=f[f.date.lt(v)&known&f.label_end.lt(vc)].copy()
    valid=f[f.date.ge(v)&f.date.lt(t)&known&f.label_end.lt(tc)].copy()
    test=f[f.date.ge(t)&f.date.le(e)].copy()
    return train,valid,test,dict(validation_cutoff=vc,test_cutoff=tc)


def rebalance_signals(signal_dates,calendar,horizon_key):
    cal=_sessions(calendar);h=resolve_horizons([horizon_key])[0];signals=pd.DatetimeIndex(signal_dates).sort_values()
    if signals.hasnans or signals.has_duplicates or not signals.isin(cal).all():raise ValueError('unique observed rebalance signals required')
    result=[];next_entry=None
    for day in signals:
        entry,end=label_interval(cal,day,h)
        if pd.isna(entry):continue
        if next_entry is None or entry>=next_entry:
            result.append(day)
            if pd.isna(end):break
            next_entry=end
    return pd.DatetimeIndex(result)


def compare_index(nav,trades,benchmark,benchmark_kind):
    """Incoming NAV must already include actual paid execution costs."""
    if benchmark_kind not in ['price_index','total_return_index']:raise ValueError('explicit price/total-return benchmark kind required')
    n=nav.copy();b=benchmark.copy()
    if not {'date','nav'}.issubset(n) or not {'date','open'}.issubset(b) or n.empty:raise ValueError('NAV and benchmark open schema required')
    for f in [n,b]:
        f['date']=pd.to_datetime(f.date)
        if f.date.isna().any() or f.date.duplicated().any() or f.date.dt.tz is not None or not f.date.eq(f.date.dt.normalize()).all() or not f.date.is_monotonic_increasing:raise ValueError('ordered unique comparison dates required')
    if 'code' in b and b.code.nunique()!=1:raise ValueError('one index per comparison required')
    for f,col,minimum in [(n,'nav',0),(b,'open',0)]:
        f[col]=pd.to_numeric(f[col],errors='raise')
        if not np.isfinite(f[col]).all() or f[col].lt(minimum).any() or (col=='open' and f[col].eq(0).any()):raise ValueError('finite valid comparison prices required')
    aligned=b.set_index('date').reindex(n.date)
    if aligned.open.isna().any():raise ValueError('benchmark missing portfolio valuation sessions; no forward fill')
    index=aligned.open.to_numpy()/float(aligned.open.iloc[0]);values=n.nav.to_numpy(dtype=float)
    years=max((n.date.iloc[-1]-n.date.iloc[0]).days/365.25,1/365.25)
    t=trades.copy();filled=pd.DataFrame(columns=['date','notional','cost'])
    if not t.empty:
        if not {'date','side','status','notional','cost'}.issubset(t):raise ValueError('trade cost and execution status required')
        t['date']=pd.to_datetime(t.date)
        for col in ['notional','cost']:
            t[col]=pd.to_numeric(t[col],errors='raise')
            if not np.isfinite(t[col]).all() or t[col].lt(0).any():raise ValueError('finite nonnegative trade amounts required')
        if not t.date.isin(n.date).all() or not t.status.isin(['filled','blocked']).all() or not t.side.isin(['buy','sell']).all() or (t.status.eq('blocked')&t.cost.ne(0)).any():raise ValueError('invalid trade session/status/cost')
        filled=t[t.status.eq('filled')]
    costs=filled.groupby('date').cost.sum().reindex(n.date,fill_value=0).to_numpy(dtype=float)
    notionals=filled.groupby('date').notional.sum().reindex(n.date,fill_value=0).to_numpy(dtype=float)
    before=values+costs
    if ((before<=0)&(notionals>0)).any():raise ValueError('trade notional has no pre-cost equity')
    two_way=np.divide(notionals,before,out=np.zeros(len(n)),where=before>0)
    initial_buy=filled.loc[filled.side.eq('buy'),'date'].min() if not filled.empty else pd.NaT
    ex_initial=two_way[n.date.ne(initial_buy).to_numpy()] if pd.notna(initial_buy) else two_way
    previous=np.r_[1.,values[:-1]]
    returns=np.divide(values,previous,out=np.full(len(n),np.nan),where=previous>0)-1
    index_returns=np.r_[0.,index[1:]/index[:-1]-1]
    active=returns-index_returns;finite=np.isfinite(active).all();std=float(np.std(active,ddof=1)) if finite and len(active)>1 else 0.
    relative=values/index
    result=dict(net_portfolio=nav_metrics(n),benchmark=nav_metrics(pd.DataFrame({'date':n.date.to_numpy(),'nav':index})),
        benchmark_kind=benchmark_kind,valuation_basis='same-session open; benchmark starts at first portfolio open',
        excess_total_return=float(values[-1]-index[-1]),relative_wealth_return=float(relative[-1]-1),
        relative_wealth_annual_return=float(relative[-1]**(1/years)-1),
        relative_max_drawdown=float((np.r_[1.,relative]/np.maximum.accumulate(np.r_[1.,relative])-1).min()),
        annual_mean_daily_active_return=float(active.mean()*252) if finite else None,
        tracking_error=float(std*np.sqrt(252)) if finite and len(active)>1 else None,
        information_ratio=float(active.mean()/std*np.sqrt(252)) if finite and std>0 else None,
        paid_cost_initial_equity_units=float(costs.sum()),
        two_way_turnover_sum=float(two_way.sum()),one_way_turnover_sum=float(two_way.sum()/2),
        annual_one_way_turnover=float(two_way.sum()/2/years),one_way_turnover_ex_initial=float(ex_initial.sum()/2),initial_allocation_date=str(initial_buy.date()) if pd.notna(initial_buy) else None,
        filled_orders=len(filled),blocked_orders=int(t.status.eq('blocked').sum()) if not t.empty else 0,
        turnover_definition='filled buy+sell notional / pre-cost open equity; one-way is half; initial allocation included; ex_initial removes first filled buy date',
        dividend_basis_warning='price index excludes dividends; dividend differential is not isolated selection alpha' if benchmark_kind=='price_index' else None)
    comparison=n[['date','nav']].copy();comparison['benchmark_nav']=index;comparison['relative_nav']=relative
    comparison['net_return']=returns;comparison['benchmark_return']=index_returns;comparison['paid_cost']=costs;comparison['two_way_turnover']=two_way
    return comparison,result


@dataclass(frozen=True)
class HorizonResearchConfig:
    top_k:int=20
    min_train_dates:int=12
    min_group_size:int=20
    embargo_sessions:int=1
    retrain_months:int=12
    train_months:int=60
    candidate_estimators:tuple=(30,60)
    seed:int=42

    def __post_init__(self):
        for name in ['top_k','min_train_dates','min_group_size','retrain_months','train_months']:
            x=getattr(self,name)
            if isinstance(x,bool) or not isinstance(x,int) or x<1:raise ValueError('positive integer research sizes required')
        if not self.candidate_estimators or len(set(self.candidate_estimators))!=len(self.candidate_estimators) or any(isinstance(x,bool) or not isinstance(x,int) or x<1 for x in self.candidate_estimators):raise ValueError('distinct positive tree candidates required')


def _rank_ic(frame):
    scores=[]
    for _,g in frame.groupby('date'):
        g=g[g.target.notna()]
        if len(g)>1 and g.target.nunique()>1 and g.score.nunique()>1:scores.append(g.target.corr(g.score,method='spearman'))
    return float(np.mean(scores)) if scores else None


def train_horizon_models(samples,labels,features,calendar,validation_start,test_start,test_end,out,config=HorizonResearchConfig()):
    """Validation chooses tree count once; rolling refits use only matured history."""
    import lightgbm,platform
    cal=_sessions(calendar);path=Path(out)
    if path.exists() and any(path.iterdir()):raise ValueError('horizon research output must be empty')
    if not features or len(set(features))!=len(features) or any(x in {'target','label_end','entry_date','horizon','score','valuation_return'} for x in features):raise ValueError('explicit feature order without outcome columns required')
    if not {'date','symbol','available_date',*features}.issubset(samples) or not {'date','symbol','horizon','label_end','target'}.issubset(labels):raise ValueError('feature and horizon label schema required')
    x=samples.copy();y=labels.copy();x['date']=pd.to_datetime(x.date);y['date']=pd.to_datetime(y.date)
    if x.duplicated(['date','symbol']).any() or y.duplicated(['date','symbol','horizon']).any():raise ValueError('ambiguous horizon sample/label keys')
    if not y.horizon.isin(HORIZONS).all():raise ValueError('explicit known horizon labels required')
    if not y.date.isin(cal).all():raise ValueError('label signals outside experiment calendar')
    for field in ['label_end','entry_date']:
        if field in y:y[field]=pd.to_datetime(y[field])
    y['target']=pd.to_numeric(y.target,errors='raise')
    pairs={(date,key):label_interval(cal,date,HORIZONS[key]) for date,key in y[['date','horizon']].drop_duplicates().itertuples(index=False,name=None)}
    expected_end=pd.Series([pairs[(date,key)][1] for date,key in y[['date','horizon']].itertuples(index=False,name=None)],index=y.index)
    if (y.label_end.notna() & y.label_end.ne(expected_end)).any():raise ValueError('label end differs from declared trading/calendar horizon')
    if 'entry_date' in y:
        expected_entry=pd.Series([pairs[(date,key)][0] for date,key in y[['date','horizon']].itertuples(index=False,name=None)],index=y.index)
        if (y.entry_date.notna() & y.entry_date.ne(expected_entry)).any():raise ValueError('label entry differs from next session')
    if 'executable_exit' in y and (y.target.notna() & ~y.executable_exit.eq(True)).any():raise ValueError('non-executable target declared mature')
    for field,attribute in [('horizon_unit','unit'),('horizon_length','length')]:
        if field in y and not y[field].eq(y.horizon.map(lambda key:getattr(HORIZONS[key],attribute))).all():raise ValueError('horizon unit contract differs')
    for feature in features:
        x[feature]=pd.to_numeric(x[feature],errors='raise')
        if np.isinf(x[feature]).any():raise ValueError('infinite feature value')
    source_hashes=dict(samples=hashlib.sha256(samples.to_csv(index=False).encode()).hexdigest(),labels=hashlib.sha256(labels.to_csv(index=False).encode()).hexdigest())
    code_hashes={name:hashlib.sha256(Path(__file__).with_name(name).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest() for name in ['horizon_research.py','model.py','horizon_labels.py']}
    all_scores=[];audits=[];trials=[];models=[];outcomes=[]
    for horizon in resolve_horizons(tuple(k for k in HORIZONS if k in set(y.horizon))):
        hlabels=y[y.horizon.eq(horizon.key)]
        # Signal dates are prescribed by the label protocol. Preserve every
        # feature-eligible security even when its future target is unknown.
        source=x.loc[x.date.isin(hlabels.date),['date','symbol','available_date',*features]]
        frame=source.merge(hlabels[['date','symbol','label_end','target']],on=['date','symbol'],how='left',validate='one_to_one')
        frame['horizon']=horizon.key
        train,valid,test,cuts=chronological_partitions(frame,cal,validation_start,test_start,test_end,config.embargo_sessions)
        def sufficient(f):
            sizes=f.groupby('date').size();return f[f.date.isin(sizes[sizes>=config.min_group_size].index)].sort_values(['date','symbol'])
        train=sufficient(train);valid=sufficient(valid)
        if train.date.nunique()<config.min_train_dates or valid.empty or test.empty:raise ValueError(horizon.key+': insufficient chronological train/validation/test groups')
        candidates=[]
        for trees in config.candidate_estimators:
            model=fit_ranker(train,features,SimpleNamespace(n_estimators=trees,seed=config.seed))
            check=valid[['date','target']].copy();check['score']=model.predict(valid[features]);metric=_rank_ic(check)
            trials.append(dict(horizon=horizon.key,n_estimators=trees,validation_rank_ic=metric,train_max_label_end=train.label_end.max(),validation_max_label_end=valid.label_end.max()))
            if metric is not None and np.isfinite(metric):candidates.append((metric,trees))
        if not candidates:raise ValueError(horizon.key+': validation Rank IC undefined')
        chosen=sorted(candidates,key=lambda z:(-z[0],z[1]))[0][1]
        model=None;next_refit=None
        for date,current in test.groupby('date',sort=True):
            if model is None or date>=next_refit:
                cutoff=cal[max(0,cal.get_loc(date)-config.embargo_sessions)]
                history=frame[(frame.date<date)&(pd.to_datetime(frame.label_end)<cutoff)&frame.target.notna()&(frame.date>=date-pd.DateOffset(months=config.train_months))].copy()
                history=sufficient(history)
                if history.date.nunique()<config.min_train_dates:raise ValueError('insufficient mature rolling history')
                model=fit_ranker(history,features,SimpleNamespace(n_estimators=chosen,seed=config.seed));next_refit=date+pd.DateOffset(months=config.retrain_months)
                bundle=path/horizon.key/date.strftime('%Y%m%d');bundle.mkdir(parents=True,exist_ok=True)
                model.booster_.save_model(str(bundle/'model.txt'))
                meta=dict(schema='horizon_ranker_v1',horizon=asdict(horizon),features=features,n_estimators=chosen,seed=config.seed,
                    trained_asof=str(date.date()),cutoff=str(cutoff.date()),max_label_end=str(pd.Timestamp(history.label_end.max()).date()),
                    preprocessing='supplied point-in-time factor values; no additional fitted transform',
                    dependencies=dict(lightgbm=lightgbm.__version__,numpy=np.__version__,pandas=pd.__version__,python=platform.python_version()),
                    input_frame_sha256=source_hashes,code_sha256=code_hashes,model_sha256=hashlib.sha256((bundle/'model.txt').read_bytes()).hexdigest(),hyperparameters=model.get_params(),certified=False,source_data_kind='supplied_unverified')
                (bundle/'metadata.json').write_text(json.dumps(meta,indent=2,allow_nan=False),encoding='utf8')
                models.append(dict(horizon=horizon.key,fold=date,relative_model_path=(bundle/'model.txt').relative_to(path).as_posix()))
                audits.append(dict(horizon=horizon.key,date=date,cutoff=cutoff,train_rows=len(history),train_dates=history.date.nunique(),max_label_end=history.label_end.max(),max_feature_date=history.date.max(),chosen_estimators=chosen))
            scores=current[['date','symbol','available_date']].copy();scores['horizon']=horizon.key;scores['score']=model.predict(current[features])
            scores=scores.sort_values(['score','symbol'],ascending=[False,True]);scores['rank']=np.arange(1,len(scores)+1)
            scores['selected']=scores['rank'].le(config.top_k) & (len(scores)>=config.top_k);all_scores.append(scores)
            outcomes.append(current[['date','symbol','horizon','label_end','target']].merge(scores[['date','symbol','horizon','score','rank','selected']],on=['date','symbol','horizon'],validate='one_to_one'))
    ranks=pd.concat(all_scores,ignore_index=True)
    observed=pd.concat(outcomes,ignore_index=True)
    observed.to_csv(path/'test_outcomes.csv',index=False)
    for name,frame in [('rankings',ranks),('training_audit',pd.DataFrame(audits)),('validation_trials',pd.DataFrame(trials)),('models',pd.DataFrame(models))]:frame.to_csv(path/(name+'.csv'),index=False)
    summary=dict(schema='three_horizon_rank_experiment_v1',config=asdict(config),features=features,
        validation_start=str(pd.Timestamp(validation_start).date()),test_start=str(pd.Timestamp(test_start).date()),test_end=str(pd.Timestamp(test_end).date()),
        horizons=sorted(ranks.horizon.unique().tolist()),models=len(models),prediction_ranges={key:dict(first=str(g.date.min().date()),last=str(g.date.max().date()),dates=int(g.date.nunique())) for key,g in ranks.groupby('horizon')},input_frame_sha256=source_hashes,code_sha256=code_hashes,
        signal_evaluation={key:dict(predictions=len(g),known_outcomes=int(g.target.notna().sum()),mean_date_rank_ic=_rank_ic(g)) for key,g in observed.groupby('horizon')},certified=False,model_ready=False,
        limitation='ranking experiment only; portfolio/index cost comparison must be executed separately; source vintages unverified')
    (path/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False),encoding='utf8')
    return ranks,pd.DataFrame(audits),summary


def load_horizon_model(directory,expected_horizon,features):
    import lightgbm
    p=Path(directory);meta=json.loads((p/'metadata.json').read_text(encoding='utf8'))
    if meta.get('schema')!='horizon_ranker_v1' or meta['horizon']!=asdict(resolve_horizons([expected_horizon])[0]) or meta['features']!=list(features):raise ValueError('horizon model feature/unit contract differs')
    if hashlib.sha256((p/'model.txt').read_bytes()).hexdigest()!=meta['model_sha256']:raise ValueError('horizon model hash mismatch')
    if meta['dependencies']['lightgbm']!=lightgbm.__version__:raise ValueError('LightGBM runtime version differs')
    return lightgbm.Booster(model_file=str(p/'model.txt'))


def backtest_horizon_rankings(data,rankings,benchmark,benchmark_kind,out,top_k=20,
                              commission_bps=3.,slippage_bps=5.,sell_tax_bps=None,
                              raw_prices=None,corporate_actions=None,reference_tick_size=None):
    """Reuse the existing net share account with horizon-dependent order dates."""
    from .pipeline import simulate_monthly
    from .model import Config
    from .costs import stamp_duty_policy
    config=Config(top_k=top_k,commission_bps=commission_bps,slippage_bps=slippage_bps,sell_tax_bps=sell_tax_bps)
    path=Path(out)
    if path.exists() and any(path.iterdir()):raise ValueError('horizon comparison output must be empty')
    r=rankings.copy()
    if not {'date','symbol','horizon','score','available_date'}.issubset(r):raise ValueError('dated horizon rankings required')
    for col in ['date','available_date']:r[col]=pd.to_datetime(r[col])
    r['score']=pd.to_numeric(r.score,errors='raise')
    if r[['date','symbol','horizon','available_date','score']].isna().any().any() or r.duplicated(['date','symbol','horizon']).any() or r.available_date.gt(r.date).any() or not r.horizon.isin(HORIZONS).all() or not np.isfinite(pd.to_numeric(r.score,errors='raise')).all():raise ValueError('invalid or future horizon forecast')
    first_position=data.calendar.searchsorted(r.date.min(),side='right')
    if first_position>=len(data.calendar):raise ValueError('no common next-open test session')
    common_start=data.calendar[first_position]
    result={}
    for horizon,forecasts in r.groupby('horizon',sort=True):
        signals=rebalance_signals(forecasts.date.unique(),data.calendar,horizon)
        if horizon in ['5d','20d'] and len(signals):
            entries=[label_interval(data.calendar,d,HORIZONS[horizon])[0] for d in signals]
            for previous,following in zip(signals,entries[1:]):
                if label_interval(data.calendar,previous,HORIZONS[horizon])[1]!=following:
                    raise ValueError('missing prescribed short-horizon rebalance signal')
            if pd.notna(label_interval(data.calendar,signals[-1],HORIZONS[horizon])[1]):
                raise ValueError('short-horizon forecasts end before observed holding periods')
        picks=[]
        for date in signals:
            current=forecasts[forecasts.date.eq(date)].sort_values(['score','symbol'],ascending=[False,True])
            if len(current)<top_k:raise ValueError('insufficient ranked candidates for portfolio')
            chosen=current.head(top_k).copy();chosen['target_weight']=1./top_k;picks.append(chosen)
        if not picks:raise ValueError('no executable scheduled forecast dates')
        selected=pd.concat(picks,ignore_index=True)
        nav,orders=simulate_monthly(data,selected,config,raw_prices,corporate_actions,reference_tick_size)
        if nav.empty:raise ValueError('empty horizon account')
        cash_dates=data.calendar[(data.calendar>=common_start)&(data.calendar<nav.date.min())]
        if len(cash_dates):
            prefix=pd.DataFrame(dict(date=cash_dates,nav=1.,cash=1.,positions=0))
            if 'dividend_receivable' in nav:prefix['dividend_receivable']=0.
            if 'return_basis' in nav:prefix['return_basis']=nav.return_basis.iloc[0]
            nav=pd.concat([prefix,nav],ignore_index=True)
        expected=data.calendar[data.calendar>=common_start]
        if not pd.DatetimeIndex(nav.date).equals(expected):raise ValueError('portfolio valuation sessions incomplete')
        daily,metrics=compare_index(nav,orders,benchmark,benchmark_kind)
        metrics.update(comparison_start=str(common_start.date()),comparison_end=str(data.calendar[-1].date()),pre_signal_cash_sessions=len(cash_dates))
        directory=path/horizon;directory.mkdir(parents=True,exist_ok=True)
        for name,frame in [('scheduled_top20',selected),('daily_nav',nav),('orders',orders),('index_comparison',daily)]:frame.to_csv(directory/(name+'.csv'),index=False)
        metrics.update(horizon=asdict(HORIZONS[horizon]),scheduled_rebalances=len(signals),
            costs=dict(commission_bps=commission_bps,slippage_bps=slippage_bps,sell_stamp_duty=stamp_duty_policy(sell_tax_bps)),
            accounting='existing fractional-share research account; initial costs included; no forced final liquidation',certified=False)
        (directory/'summary.json').write_text(json.dumps(metrics,indent=2,allow_nan=False),encoding='utf8');result[horizon]=metrics
    (path/'summary.json').write_text(json.dumps(dict(schema='horizon_portfolio_comparison_v1',horizons=result,certified=False,model_ready=False),indent=2,allow_nan=False),encoding='utf8')
    return result


def public_horizon_inputs(directory,asof=None,oneil=False):
    """Reuse the existing sealed public dataset and factor definitions."""
    from .public_conversion import load_public_data,public_snapshot,_public_event_inputs
    from .factors import default_library,technical_panel
    from .data import month_ends
    from .horizon_labels import forward_horizon_labels
    data=load_public_data(directory,asof);raw,actions,tick=_public_event_inputs(directory,data)
    if raw is None:raise ValueError('three-horizon research requires sealed raw-price/event inputs')
    cal=data.calendar
    if len(cal)<252:raise ValueError('factor warmup requires 252 observed sessions')
    source_calendar=pd.DatetimeIndex(pd.to_datetime(pd.read_csv(Path(directory)/'calendar.csv')['date']))
    completed=month_ends(source_calendar)
    dates={'5d':cal[251::5],'20d':cal[251::20],'12m':completed[(completed>=cal[251])&(completed<=cal[-1])]}
    union=pd.DatetimeIndex(sorted(set().union(*(set(d) for d in dates.values()))))
    technical=technical_panel(data);library=default_library();frames=[]
    features=[name for name,item in library.factors.items() if oneil or item.family!='oneil']
    for date in union:
        snap=public_snapshot(data,technical,date)
        if snap.empty:continue
        values=library.compute(snap,oneil);values['date']=date;values['available_date']=snap.available_date
        frames.append(values.rename_axis('symbol').reset_index())
    if not frames:raise ValueError('no dated eligible public factor snapshots')
    samples=pd.concat(frames,ignore_index=True)
    labels=pd.concat([forward_horizon_labels(raw,actions,cal,signals,[key],tick) for key,signals in dates.items()],ignore_index=True)
    return data,samples,labels,features,raw,actions,tick


def run_three_horizon_research(directory,benchmark_directory,out,validation_start,test_start,test_end,
                               config=HorizonResearchConfig(),asof=None,oneil=False,exploratory=False,
                               commission_bps=3.,slippage_bps=5.,sell_tax_bps=None):
    if not exploratory:raise ValueError('unverified public vintages require explicit exploratory research mode')
    path=Path(out)
    if path.exists() and any(path.iterdir()):raise ValueError('three-horizon run output must be empty')
    bench=Path(benchmark_directory);meta=json.loads((bench/'summary.json').read_text(encoding='utf8'))
    raw_benchmark=(bench/'raw_prices.csv').read_bytes()
    if meta.get('schema')!='benchmark_raw_v1' or hashlib.sha256(raw_benchmark).hexdigest()!=meta.get('raw_sha256'):raise ValueError('sealed benchmark hash/schema mismatch')
    from io import BytesIO
    benchmark=pd.read_csv(BytesIO(raw_benchmark))
    if meta.get('request',{}).get('symbol')=='sh.000300' and meta.get('return_kind')!='price_index':raise ValueError('CSI300 price series cannot be relabelled total return')
    data,samples,labels,features,raw,actions,tick=public_horizon_inputs(directory,asof,oneil)
    if pd.Timestamp(test_end)>data.calendar[-1]:raise ValueError('requested test end beyond observed dataset')
    path.mkdir(parents=True,exist_ok=True)
    samples.to_csv(path/'factor_samples.csv',index=False);labels.to_csv(path/'labels.csv',index=False)
    ranks,audit,models=train_horizon_models(samples,labels,features,data.calendar,validation_start,test_start,test_end,path/'models',config)
    # Test boundary also limits portfolio marks and orders, including subsequent
    # raw event inputs; training cannot consume the benchmark or NAV metrics.
    from .data import Dataset
    cutoff=pd.Timestamp(test_end)
    test_data=Dataset(data.prices[data.prices.date<=cutoff].copy(),data.fundamentals[data.fundamentals.available_date<=cutoff].copy(),data.calendar[data.calendar<=cutoff],data.metadata)
    raw=raw[pd.to_datetime(raw.date)<=cutoff].copy();actions=actions[pd.to_datetime(actions.ex_date)<=cutoff].copy()
    comparisons=backtest_horizon_rankings(test_data,ranks,benchmark,meta['return_kind'],path/'portfolios',config.top_k,
        commission_bps,slippage_bps,sell_tax_bps,raw,actions,tick)
    if set(comparisons)!=set(HORIZONS):raise ValueError('all three horizon portfolios required')
    result=dict(schema='three_horizon_research_v1',mode='exploratory_unverified_public_source',certified=False,model_ready=False,
        models=models,portfolios=comparisons,data=data.metadata,benchmark_sha256=meta['raw_sha256'],
        interpretation='separate time-purged horizon experiments; numerical evidence does not certify profitability, source vintages or broker-executable fills')
    (path/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf8')
    return result

import pandas as pd
import pytest
from test_prediction_ledger import arguments
from test_risk_selection import prepared
from ashare_mvp import risk_selection as risk
from ashare_mvp.workspace_paper_tracking import replay_paper_books
from ashare_mvp.phase2_protocol import sha256


def replay(prepared,flag=None,held_change=False,policy='2026-10-20'):
    args,base,source,_,_=prepared;view=risk.build_risk_selection(**args)
    selected=pd.read_csv(args['work_root']/'view/top20_5d.csv');symbol=selected.symbol.iloc[0]
    raw=pd.DataFrame([dict(date=d,symbol=s,open=10.,close=10.,preclose=10.,tradestatus='1',isST='0') for d in base['calendar'] for s in base['scores'].symbol])
    raw['isST']=raw.isST.astype(object)
    if flag=='missing_column':raw=raw.drop(columns='isST')
    else:raw.loc[raw.symbol.eq(symbol)&raw.date.eq('2026-10-22' if held_change else '2026-10-21'),'isST']=flag
    views={'2026-10-20':dict(directory='@work/view',manifest_sha256=sha256(args['work_root']/'view/manifest.json'))}
    actions=pd.DataFrame(columns=['symbol','ex_date','available_date','pay_date','cash_per_share','share_multiplier'])
    target=args['work_root']/'paper'
    result=replay_paper_books(source/'predictions.sqlite',[r['batch_id'] for r in view['horizons'].values()],args['project'],base['seal_directory'],raw,actions,
                             pd.DatetimeIndex(['2025-12-31']).append(base['calendar']),'2026-10-22',target,work_root=args['work_root'],selection_views=views,risk_policy_start=policy)
    return result,target,symbol


@pytest.mark.parametrize('flag',['1',None,'unavailable',True,'missing_column',2])
def test_new_buy_blocks_st_or_unknown_execution_day_even_with_earlier_clean_name(prepared,flag):
    result,target,symbol=replay(prepared,flag)
    for h in ['5d','20d','12m']:
        orders=pd.read_csv(target/h/'orders.csv');nav=pd.read_csv(target/h/'open_nav.csv');positions=pd.read_csv(target/h/'positions.csv')
        affected=orders[orders.symbol.eq(symbol)];assert len(affected)==1 and affected.iloc[0].status=='blocked' and affected.iloc[0].cost==0
        assert len(orders[orders.status.eq('filled')])==(0 if flag=='missing_column' else 19)
        assert symbol not in set(positions.symbol) and nav.cash.gt(0).all()
    assert result['execution_st_policy']['first_gated_open']=='2026-10-21'


def test_existing_holding_that_becomes_st_remains_in_shares_and_nav(prepared):
    _,target,symbol=replay(prepared,'1',held_change=True)
    for h in ['5d','20d','12m']:
        orders=pd.read_csv(target/h/'orders.csv');positions=pd.read_csv(target/h/'positions.csv');nav=pd.read_csv(target/h/'open_nav.csv')
        assert len(orders)==20 and orders.status.eq('filled').all()
        assert len(positions)==20 and positions.loc[positions.symbol.eq(symbol),'shares'].iloc[0]>0
        assert len(nav)==2 and nav.positions.eq(20).all() and abs(nav.nav.iloc[0]-nav.nav.iloc[1])<1e-12


def test_execution_gate_starts_after_policy_signal_without_rewriting_prior_entry(prepared):
    result,target,_=replay(prepared,'missing_column',policy='2026-10-21')
    assert result['execution_st_policy']['first_gated_open']=='2026-10-22'
    for h in ['5d','20d','12m']:
        orders=pd.read_csv(target/h/'orders.csv');assert len(orders)==20 and orders.status.eq('filled').all()

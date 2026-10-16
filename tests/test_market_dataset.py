import hashlib
import json
import pandas as pd
import pytest
from ashare_mvp.market_dataset import prepare_market_ratios
from ashare_mvp.public_conversion import load_public_data


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(root):
    paths=[root/name for name in ['market','finance','master','actions']]
    for path in paths:path.mkdir(parents=True)
    market,finance,master,actions=paths
    cal=pd.bdate_range('2018-01-02',periods=4)
    pd.DataFrame({'calendar_date':cal,'is_trading_day':'1'}).to_csv(market/'calendar.csv',index=False)
    files={'calendar.csv':{'sha256':digest(market/'calendar.csv')}}
    for index,date in enumerate(cal):
        frame=pd.DataFrame([dict(date=date,code=code,open=price,close=price,preclose=price,volume=100,
                           adjustflag='3',tradestatus='1',isST='1' if code=='sz.000001' and index==2 else '0',peTTM=10,pbMRQ=2)
                           for code,price in [('sh.600000',10 if index==0 else 9),('sz.000001',20),('bj.920001',5)]])
        name=f'daily_{date:%Y%m%d}.csv';frame.to_csv(market/name,index=False)
        files[name]={'sha256':digest(market/name),'rows':len(frame)}
    (market/'manifest.json').write_text(json.dumps(dict(schema='market_daily_raw_v1',synthetic=True,source='fixture',start=str(cal[0].date()),end=str(cal[-1].date()),status='downloaded_raw',files=files)))
    pd.DataFrame([dict(symbol=code,report_date='2017-09-30',available_date=cal[0],publication_date='2017-12-29',provider_update_date='2017-12-29',roe=.1,revenue_growth=.2,earnings_growth=.3,debt_ratio=float('nan')) for code in ['sh.600000','sz.000001']]).to_csv(finance/'financial_panel.csv',index=False)
    (finance/'summary.json').write_text(json.dumps(dict(panel_sha256=digest(finance/'financial_panel.csv'),partial_source=True)))
    pd.DataFrame([dict(code=code,ipoDate='2010-01-01',outDate='',type='1') for code in ['sh.600000','sz.000001']]).to_csv(master/'security_master.csv',index=False)
    pd.DataFrame({'code':['sh.600000','sz.000001']}).to_csv(master/'input_targets.csv',index=False)
    (master/'manifest.json').write_text(json.dumps(dict(schema='requested_security_master_snapshot_v1',master_sha256=digest(master/'security_master.csv'),targets_sha256=digest(master/'input_targets.csv'))))
    pd.DataFrame([dict(symbol='sh.600000',ex_date=cal[1],available_date=cal[0],pay_date=cal[2],cash_per_share=1.,share_multiplier=1.)]).to_csv(actions/'actions.csv',index=False)
    pd.DataFrame({'security':['600000.SH'],'status':['matched_payment_evidence']}).to_csv(actions/'coverage.csv',index=False)
    (actions/'summary.json').write_text(json.dumps(dict(actions_sha256=digest(actions/'actions.csv'),coverage_sha256=digest(actions/'coverage.csv'),matched_events=1,partial_market_source=False,status_counts={'matched_payment_evidence':1},evaluation_start=str(cal[0].date()),evaluation_end=str(cal[-1].date()))))
    return paths,cal


def prepare(root,paths,**kwargs):return prepare_market_ratios(*paths,root/'dataset',root/'audit',**kwargs)


def test_real_source_contract_preserves_st_history_and_native_missing_ratios(tmp_path):
    paths,cal=fixture(tmp_path)
    result=prepare(tmp_path,paths)
    assert result['status']=='prepared_exploratory' and not result['certified'] and not result['model_ready']
    assert result['excluded_non_ashare_quote_rows']==4 and result['raw_files_hash_checked']==4
    data=load_public_data(tmp_path/'dataset')
    assert set(data.prices.symbol)=={'sh.600000','sz.000001'} and len(data.prices)==8
    assert data.prices.is_st.sum()==1 and not data.prices.loc[data.prices.is_st,'eligible'].any()
    assert data.fundamentals.debt_ratio.isna().all()
    assert data.prices.earnings_yield.eq(.1).all() and data.prices.book_yield.eq(.5).all()
    assert 'market_cap' not in data.prices and 'earnings_ttm' not in data.fundamentals
    assert data.prices.loc[data.prices.symbol.eq('sh.600000'),'close'].tolist()==pytest.approx([10,10,10,10])
    assert data.metadata['event_return_inputs']['raw_prices']=='event_raw_prices.csv'


def test_missing_calendar_and_unresolved_identity_are_reported_without_dataset(tmp_path):
    paths,cal=fixture(tmp_path);market,_,master,_=paths
    p=market/'manifest.json';m=json.loads(p.read_text());del m['files'][f'daily_{cal[1]:%Y%m%d}.csv'];p.write_text(json.dumps(m))
    t=master/'input_targets.csv';pd.DataFrame({'code':['sh.600000','sz.000001','sh.600001']}).to_csv(t,index=False)
    p=master/'manifest.json';m=json.loads(p.read_text());m['targets_sha256']=digest(t);p.write_text(json.dumps(m))
    result=prepare(tmp_path,paths)
    assert result['issue_counts']=={'missing_daily_cross_section':1,'unresolved_security_identity':1}
    assert result['validation_stage']=='source_inventory' and result['raw_files_hash_checked']==0
    assert not (tmp_path/'dataset').exists()
    assert (tmp_path/'audit/input_market_manifest.json').exists()


def test_tampered_financial_source_is_rejected(tmp_path):
    paths,_=fixture(tmp_path)
    with (paths[1]/'financial_panel.csv').open('a') as f:f.write('changed')
    with pytest.raises(ValueError,match='source hash mismatch'):prepare(tmp_path,paths)
    assert not (tmp_path/'dataset').exists()


def test_missing_listing_head_is_not_dropped_or_forward_filled(tmp_path):
    paths,cal=fixture(tmp_path);market=paths[0];name=f'daily_{cal[0]:%Y%m%d}.csv';p=market/name
    f=pd.read_csv(p);f[f.code!='sz.000001'].to_csv(p,index=False)
    m=json.loads((market/'manifest.json').read_text());m['files'][name]={'sha256':digest(p),'rows':2};(market/'manifest.json').write_text(json.dumps(m))
    result=prepare(tmp_path,paths)
    assert result['status']=='blocked' and result['issue_counts']['missing_sessions_or_terminal_valuation']==1
    assert result['symbols']==2 and not (tmp_path/'dataset').exists()


def test_unreconciled_price_does_not_remove_security(tmp_path):
    paths,_=fixture(tmp_path);events=paths[3];p=events/'actions.csv'
    f=pd.read_csv(p);f['cash_per_share']=.5;f.to_csv(p,index=False)
    m=json.loads((events/'summary.json').read_text());m['actions_sha256']=digest(p);(events/'summary.json').write_text(json.dumps(m))
    result=prepare(tmp_path,paths)
    assert result['issue_counts']['unreconciled_adjustment']==1 and result['symbols']==2
    assert not (tmp_path/'dataset').exists()


def test_same_issuer_code_change_preserves_listing_date_and_trading_code(tmp_path):
    paths,cal=fixture(tmp_path);market,finance,master,_=paths
    p=market/'manifest.json';m=json.loads(p.read_text())
    for date in cal:
        name=f'daily_{date:%Y%m%d}.csv';file=market/name;f=pd.read_csv(file)
        f.loc[f.code=='sz.000001','code']='sz.000043' if date<cal[2] else 'sz.001914'
        f.to_csv(file,index=False);m['files'][name]['sha256']=digest(file)
    p.write_text(json.dumps(m))
    f=pd.DataFrame([dict(code='sh.600000',ipoDate='2010-01-01',outDate='',type='1'),dict(code='sz.000043',ipoDate='2010-01-01',outDate=str(cal[2].date()),type='1'),dict(code='sz.001914',ipoDate=str(cal[2].date()),outDate='',type='1')]);f.to_csv(master/'security_master.csv',index=False)
    f[['code']].to_csv(master/'input_targets.csv',index=False)
    p=master/'manifest.json';m=json.loads(p.read_text());m.update(master_sha256=digest(master/'security_master.csv'),targets_sha256=digest(master/'input_targets.csv'));p.write_text(json.dumps(m))
    p=finance/'financial_panel.csv';f=pd.read_csv(p);f.loc[f.symbol=='sz.000001','symbol']='sz.001914';f.to_csv(p,index=False)
    p=finance/'summary.json';m=json.loads(p.read_text());m['panel_sha256']=digest(finance/'financial_panel.csv');p.write_text(json.dumps(m))
    changes=pd.DataFrame([dict(old_symbol='sz.000043',new_symbol='sz.001914',announcement_date='2018-01-02',effective_date=cal[2],share_ratio=1,source_url='https://example.test/notice.pdf')])
    result=prepare(tmp_path,paths,code_changes=changes)
    assert result['status']=='prepared_exploratory'
    data=load_public_data(tmp_path/'dataset');p=data.prices[data.prices.symbol=='sz.000043']
    assert p.trading_symbol.tolist()==['sz.000043','sz.000043','sz.001914','sz.001914']
    assert p.listing_date.eq(pd.Timestamp('2010-01-01')).all()
    assert set(data.fundamentals.symbol)=={'sh.600000','sz.000043'}
    assert data.metadata['security_identity_policy']['changes'][0]['new_symbol']=='sz.001914'


def test_unimplemented_plan_is_not_a_paid_event_or_blocker(tmp_path):
    paths,_=fixture(tmp_path);events=paths[3]
    coverage=pd.DataFrame({'security':['600000.SH','000001.SZ'],'status':['matched_payment_evidence','not_implemented']})
    coverage.to_csv(events/'coverage.csv',index=False)
    p=events/'summary.json';m=json.loads(p.read_text());m.update(coverage_sha256=digest(events/'coverage.csv'),status_counts=coverage.status.value_counts().to_dict());p.write_text(json.dumps(m))
    result=prepare(tmp_path,paths)
    assert result['status']=='prepared_exploratory' and result['not_implemented_plans_ignored']==1
    events=pd.read_csv(tmp_path/'dataset/event_corporate_actions.csv')
    assert len(events)==1 and events.symbol.tolist()==['sh.600000']


def test_future_financial_dependency_is_rejected(tmp_path):
    paths,cal=fixture(tmp_path);finance=paths[1]
    p=finance/'financial_panel.csv';f=pd.read_csv(p);f['balance_available_date']=cal[-1];f.to_csv(p,index=False)
    p=finance/'summary.json';m=json.loads(p.read_text());m['panel_sha256']=digest(finance/'financial_panel.csv');p.write_text(json.dumps(m))
    with pytest.raises(ValueError,match='future financial dependency'):prepare(tmp_path,paths)
    assert not (tmp_path/'dataset').exists()


def test_missing_required_source_hash_cannot_disable_validation(tmp_path):
    paths,_=fixture(tmp_path);master=paths[2]
    p=master/'manifest.json';m=json.loads(p.read_text());m['master_sha256']=None;p.write_text(json.dumps(m))
    with pytest.raises(ValueError,match='source hash required'):prepare(tmp_path,paths)
    assert not (tmp_path/'dataset').exists()


@pytest.mark.parametrize('security,kind',[('302132.SZ','unresolved_event_evidence'),('689009.SH','unresolved_event_identity'),('unknown','unresolved_event_identity')])
def test_unknown_implemented_security_is_not_silently_out_of_universe(tmp_path,security,kind):
    paths,cal=fixture(tmp_path);events=paths[3]
    coverage=pd.read_csv(events/'coverage.csv')
    coverage=pd.concat([coverage,pd.DataFrame([dict(security=security,status='unresolved',ex_date=cal[1])])],ignore_index=True)
    coverage.to_csv(events/'coverage.csv',index=False)
    p=events/'summary.json';m=json.loads(p.read_text());m.update(coverage_sha256=digest(events/'coverage.csv'),status_counts=coverage.status.value_counts().to_dict());p.write_text(json.dumps(m))
    result=prepare(tmp_path,paths)
    assert result['status']=='blocked' and result['issue_counts']=={kind:1}
    assert not (tmp_path/'dataset').exists()


def test_unknown_security_execution_explicitly_outside_calendar_is_separate(tmp_path):
    paths,cal=fixture(tmp_path);events=paths[3]
    coverage=pd.read_csv(events/'coverage.csv')
    coverage=pd.concat([coverage,pd.DataFrame([dict(security='689009.SH',status='unresolved',ex_date=cal[-1]+pd.Timedelta(days=10))])],ignore_index=True)
    coverage.to_csv(events/'coverage.csv',index=False)
    p=events/'summary.json';m=json.loads(p.read_text());m.update(coverage_sha256=digest(events/'coverage.csv'),status_counts=coverage.status.value_counts().to_dict());p.write_text(json.dumps(m))
    assert prepare(tmp_path,paths)['status']=='prepared_exploratory'


@pytest.mark.parametrize('mode',['missing','different','tampered','matching'])
def test_event_identity_policy_is_verified_and_matches_feature_policy(tmp_path,mode):
    from test_dividend_identity import changes
    from ashare_mvp.security_identity import code_change_payload
    paths,_=fixture(tmp_path);events=paths[3];c=changes()
    raw=code_change_payload(c).encode();(events/'input_code_changes.json').write_bytes(raw)
    p=events/'summary.json';m=json.loads(p.read_text());m['code_changes_sha256']=hashlib.sha256(raw).hexdigest();p.write_text(json.dumps(m))
    if mode=='missing':c=None
    elif mode=='different':c['effective_date']='2025-02-18'
    elif mode=='tampered':(events/'input_code_changes.json').write_text('changed')
    else:
        # String dates and timestamp dates produce the same sealed policy.
        c['announcement_date']=pd.to_datetime(c.announcement_date);c['effective_date']=pd.to_datetime(c.effective_date);c['share_ratio']=1.
    if mode=='matching':
        assert prepare(tmp_path,paths,code_changes=c)['status']=='prepared_exploratory'
        assert (tmp_path/'audit/input_event_code_changes.json').read_bytes()==raw
    else:
        with pytest.raises(ValueError,match='event code-change'):
            prepare(tmp_path,paths,code_changes=c)
        assert not (tmp_path/'dataset').exists()

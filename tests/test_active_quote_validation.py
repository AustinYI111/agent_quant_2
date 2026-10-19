import json
import numpy as np
import pandas as pd
import pytest
from test_market_dataset import fixture,prepare,digest
from ashare_mvp.public_conversion import load_public_data


def test_inactive_alias_missing_volume_is_quarantined_before_active_value_check(tmp_path):
    paths,cal=fixture(tmp_path);market,finance,master,_=paths
    changes=pd.DataFrame([dict(old_symbol='sz.000043',new_symbol='sz.001914',announcement_date='2018-01-02',effective_date=cal[2],share_ratio=1,source_url='https://example.test/notice.pdf')])
    p=market/'manifest.json';m=json.loads(p.read_text());original={}
    for date in cal:
        name=f'daily_{date:%Y%m%d}.csv';file=market/name;f=pd.read_csv(file)
        f.loc[f.code.eq('sz.000001'),'code']='sz.000043' if date<cal[2] else 'sz.001914'
        if date==cal[0]:
            ghost=f[f.code.eq('sz.000043')].copy();ghost['code']='sz.001914';ghost['volume']=np.nan;ghost['tradestatus']='0'
            f=pd.concat([f,ghost],ignore_index=True)
        f.to_csv(file,index=False);original[name]=file.read_bytes();m['files'][name].update(sha256=digest(file),rows=len(f))
    p.write_text(json.dumps(m))
    stocks=pd.DataFrame([dict(code='sh.600000',ipoDate='2010-01-01',outDate='',type='1'),dict(code='sz.000043',ipoDate='2010-01-01',outDate=str(cal[2].date()),type='1'),dict(code='sz.001914',ipoDate=str(cal[2].date()),outDate='',type='1')])
    stocks.to_csv(master/'security_master.csv',index=False);stocks[['code']].to_csv(master/'input_targets.csv',index=False)
    p=master/'manifest.json';m=json.loads(p.read_text());m.update(master_sha256=digest(master/'security_master.csv'),targets_sha256=digest(master/'input_targets.csv'));p.write_text(json.dumps(m))
    p=finance/'financial_panel.csv';f=pd.read_csv(p);f.loc[f.symbol.eq('sz.000001'),'symbol']='sz.000043';f.to_csv(p,index=False)
    p=finance/'summary.json';m=json.loads(p.read_text());m['panel_sha256']=digest(finance/'financial_panel.csv');p.write_text(json.dumps(m))
    r=prepare(tmp_path,paths,code_changes=changes)
    assert r['status']=='prepared_exploratory' and r['quarantined_quote_rows']==1
    data=load_public_data(tmp_path/'dataset')
    assert len(data.prices)==8 and data.prices.volume.notna().all()
    assert data.prices[data.prices.symbol.eq('sz.000043')].trading_symbol.tolist()==['sz.000043','sz.000043','sz.001914','sz.001914']
    for name,raw in original.items():assert (market/name).read_bytes()==raw


@pytest.mark.parametrize('value,kind',[(np.nan,'missing_active_market_value'),(-1,'negative_active_market_value')])
def test_active_quote_invalid_volume_blocks_with_dated_evidence(tmp_path,value,kind):
    paths,cal=fixture(tmp_path);market=paths[0];name=f'daily_{cal[0]:%Y%m%d}.csv';file=market/name
    f=pd.read_csv(file);f.loc[f.code.eq('sz.000001'),'volume']=value;f.loc[f.code.eq('sz.000001'),'tradestatus']=1;f.to_csv(file,index=False)
    p=market/'manifest.json';m=json.loads(p.read_text());m['files'][name]['sha256']=digest(file);p.write_text(json.dumps(m))
    r=prepare(tmp_path,paths)
    assert r['status']=='blocked' and r['validation_stage']=='active_price_values'
    assert r['issue_counts']=={kind:1} and r['raw_files_hash_checked']==4
    issues=pd.read_csv(tmp_path/'audit/blocking_issues.csv');row=issues.iloc[0]
    assert row.symbol=='sz.000001' and row.date.startswith('2018-01-02') and 'volume' in row.detail
    assert not (tmp_path/'dataset').exists()
    actual=pd.read_csv(file).loc[1,'volume']
    if pd.isna(value):assert pd.isna(actual)
    else:assert actual==value


def test_nontrading_unknown_volume_retained_without_inventing_zero(tmp_path):
    paths,cal=fixture(tmp_path);market=paths[0];name=f'daily_{cal[0]:%Y%m%d}.csv';file=market/name
    f=pd.read_csv(file);f.loc[f.code.eq('sz.000001'),'volume']=np.nan;f.loc[f.code.eq('sz.000001'),'tradestatus']=0;f.to_csv(file,index=False)
    p=market/'manifest.json';m=json.loads(p.read_text());m['files'][name]['sha256']=digest(file);p.write_text(json.dumps(m))
    original=file.read_bytes();r=prepare(tmp_path,paths)
    assert r['status']=='prepared_exploratory' and r['nontrading_unknown_volume_rows']==1
    data=load_public_data(tmp_path/'dataset');row=data.prices[data.prices.symbol.eq('sz.000001')&data.prices.date.eq(cal[0])].iloc[0]
    assert pd.isna(row.volume) and not row.can_buy and not row.can_sell
    assert row.close==20 and file.read_bytes()==original
    audit=pd.read_csv(tmp_path/'audit/nontrading_unknown_volume.csv')
    assert len(audit)==1 and audit.volume.isna().all()
    assert 'no imputation' in data.metadata['nontrading_volume_policy']


@pytest.mark.parametrize('field',['open','close','preclose'])
def test_nontrading_session_still_requires_supplied_price_values(tmp_path,field):
    paths,cal=fixture(tmp_path);market=paths[0];name=f'daily_{cal[0]:%Y%m%d}.csv';file=market/name
    f=pd.read_csv(file);f[field]=f[field].astype(float)
    f.loc[f.code.eq('sz.000001'),field]=np.nan;f.loc[f.code.eq('sz.000001'),'volume']=np.nan;f.loc[f.code.eq('sz.000001'),'tradestatus']=0;f.to_csv(file,index=False)
    p=market/'manifest.json';m=json.loads(p.read_text());m['files'][name]['sha256']=digest(file);p.write_text(json.dumps(m))
    r=prepare(tmp_path,paths)
    assert r['status']=='blocked' and r['issue_counts']=={'missing_active_market_value':1}
    assert r['nontrading_unknown_volume_rows']==1
    detail=pd.read_csv(tmp_path/'audit/blocking_issues.csv').detail.iloc[0]
    assert 'fields='+field in detail and not (tmp_path/'dataset').exists()

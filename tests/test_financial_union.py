import hashlib,json
import numpy as np
import pandas as pd
import pytest
from ashare_mvp.calendar_union import seal_calendar_union
from ashare_mvp.financial_union import union_financial_panels


def prepare(tmp_path):
    raw=tmp_path/'calendar_source';raw.mkdir()
    dates=pd.date_range('2024-01-01','2024-12-31')
    pd.DataFrame(dict(calendar_date=dates,is_trading_day=(dates.dayofweek<5).astype(int))).to_csv(raw/'calendar.csv',index=False)
    (raw/'manifest.json').write_text(json.dumps(dict(schema='market_daily_raw_v1',start='2024-01-01',end='2024-12-31',files={'calendar.csv':{'sha256':hashlib.sha256((raw/'calendar.csv').read_bytes()).hexdigest()}})))
    calendar=tmp_path/'calendar';seal_calendar_union([raw],calendar)
    frame=pd.DataFrame([dict(symbol='sh.600000',report_date='2023-12-31',publication_date='2024-04-01',provider_update_date='2024-04-01',provider_ingest_date=None,available_date='2024-04-02',roe=.1,revenue_growth=.2,earnings_growth=.3,debt_ratio=np.nan,eps=1.,book_value_per_share=2.,operating_cashflow_per_share=3.,visibility_status='visible',origin_quarter='2023-12-31')])
    return calendar,frame


def save(path,frame,calendar):
    path.mkdir();frame.to_csv(path/'financial_panel.csv',index=False)
    (path/'summary.json').write_text(json.dumps(dict(source='test',records=len(frame),panel_sha256=hashlib.sha256((path/'financial_panel.csv').read_bytes()).hexdigest(),partial_source=False)))
    c=json.loads((calendar/'summary.json').read_text())
    (path/'calendar_lineage.json').write_text(json.dumps(dict(calendar_sha256=c['calendar_sha256'],calendar_summary_sha256=hashlib.sha256((calendar/'summary.json').read_bytes()).hexdigest())))
    return path


def test_union_retains_late_versions_and_invisible_future_records(tmp_path):
    c,f=prepare(tmp_path);a=save(tmp_path/'a',f,c)
    bframe=f.copy();bframe['roe']=.9;bframe['provider_update_date']='2024-06-03';bframe['available_date']='2024-06-04'
    future=f.copy();future['report_date']='2024-12-31';future['publication_date']='2025-04-01';future['provider_update_date']='2025-04-01';future['available_date']=None
    b=save(tmp_path/'b',pd.concat([bframe,future]),c)
    s=union_financial_panels([a,b],c,tmp_path/'out');r=pd.read_csv(tmp_path/'out/financial_panel.csv')
    assert s['records']==3 and s['visible_records']==2 and s['invisible_records']==1
    assert r.loc[r.available_date.eq('2024-04-02'),'roe'].iloc[0]==.1
    assert r.loc[r.available_date.eq('2024-06-04'),'roe'].iloc[0]==.9
    assert r.available_date.isna().sum()==1 and r.debt_ratio.isna().all()
    assert not s['model_ready'] and not s['certified']
    origins=pd.read_csv(tmp_path/'out/record_origins.csv');assert origins.source_index.tolist()==[0,1,1]


def test_only_payload_equal_versions_coalesce_with_traceable_audit(tmp_path):
    c,f=prepare(tmp_path);a=save(tmp_path/'a',f,c);b=save(tmp_path/'b',f,c)
    s=union_financial_panels([a,b],c,tmp_path/'out')
    assert s['records']==1 and s['equal_duplicate_rows_removed']==1
    audit=pd.read_csv(tmp_path/'out/equal_version_coalescing.csv')
    assert audit.kept_source.iloc[0]==0 and audit.duplicate_source.iloc[0]==1
    assert (tmp_path/'out/input_0_summary.json').read_bytes()==(a/'summary.json').read_bytes()


def test_unidentified_provider_rows_retained_but_not_called_visible(tmp_path):
    c,f=prepare(tmp_path);unknown=f.copy();unknown['symbol']=None;unknown['visibility_status']='unsupported_security_exchange'
    a=save(tmp_path/'a',pd.concat([f,unknown]),c)
    s=union_financial_panels([a],c,tmp_path/'out')
    assert s['records']==2 and s['visible_records']==1 and s['unidentified_records']==1 and s['not_visible_records']==1
    assert pd.read_csv(tmp_path/'out/financial_panel.csv').symbol.isna().sum()==1


@pytest.mark.parametrize('field,value',[('roe',.9),('provider_update_date','2024-03-31'),('industry','changed')])
def test_conflicting_same_clock_versions_never_choose_source(tmp_path,field,value):
    c,f=prepare(tmp_path)
    if field=='industry':f['industry']='original'
    a=save(tmp_path/'a',f,c);g=f.copy();g[field]=value;b=save(tmp_path/'b',g,c)
    with pytest.raises(ValueError,match='conflicting'):union_financial_panels([a,b],c,tmp_path/'out')
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('problem',['hash','lineage','future_clock','weekend','infinite','count'])
def test_corrupt_or_premature_inputs_never_publish_manifest(tmp_path,problem):
    c,f=prepare(tmp_path)
    if problem=='future_clock':f['provider_update_date']='2024-04-02'
    if problem=='weekend':f['available_date']='2024-04-06'
    if problem=='infinite':f['roe']=np.inf
    a=save(tmp_path/'a',f,c)
    if problem=='hash':(a/'financial_panel.csv').write_text('corrupt')
    if problem=='lineage':(a/'calendar_lineage.json').write_text('{}')
    if problem=='count':
        s=json.loads((a/'summary.json').read_text());s['records']=99;(a/'summary.json').write_text(json.dumps(s))
    with pytest.raises(ValueError):union_financial_panels([a],c,tmp_path/'out')
    assert not (tmp_path/'out').exists()

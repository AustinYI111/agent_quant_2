import hashlib,json
import pandas as pd
import pytest
from ashare_mvp.market_range_union import merge_market_ranges
from ashare_mvp.market_download import download_market_daily


def source(path,start,end,missing=()):
    path.mkdir();dates=pd.date_range(start,end)
    pd.DataFrame(dict(calendar_date=dates.strftime('%Y-%m-%d'),is_trading_day=(dates.dayofweek<5).astype(int))).to_csv(path/'calendar.csv',index=False)
    files={'calendar.csv':{'sha256':hashlib.sha256((path/'calendar.csv').read_bytes()).hexdigest()}}
    queries={}
    for d in dates[dates.dayofweek<5]:
        key=str(d.date())
        if key in missing:continue
        name=f'daily_{d:%Y%m%d}.csv'
        pd.DataFrame([dict(date=key,code=code,open='10',close='10',preclose='10',volume='100',adjustflag='3',tradestatus='1',isST='1' if code=='sh.600000' else '0',peTTM='10',pbMRQ='2') for code in ['sh.600000','bj.920001']]).to_csv(path/name,index=False)
        files[name]={'rows':2,'sha256':hashlib.sha256((path/name).read_bytes()).hexdigest()};queries[key]={'status':'ok'}
    m=dict(schema='market_daily_raw_v1',source='test provider',synthetic=True,start=start,end=end,status='running',files=files,queries=queries)
    (path/'manifest.json').write_text(json.dumps(m));return path


def rewrite(path,name,change):
    frame=pd.read_csv(path/name,dtype=str);change(frame);frame.to_csv(path/name,index=False)
    m=json.loads((path/'manifest.json').read_text());m['files'][name]['sha256']=hashlib.sha256((path/name).read_bytes()).hexdigest();(path/'manifest.json').write_text(json.dumps(m))


def test_adjacent_ranges_preserve_raw_st_beijing_and_sealed_inputs(tmp_path):
    a=source(tmp_path/'a','2024-01-01','2024-01-05');b=source(tmp_path/'b','2024-01-06','2024-01-10')
    old=(b/'manifest.json').read_bytes();out=tmp_path/'out';r=merge_market_ranges([a,b],out)
    assert r['sessions_saved']==8 and r['missing_sessions']==0 and r['rows']==16
    assert r['status']=='downloaded_raw' and r['source_statuses']==['running','running']
    f=pd.read_csv(out/'daily_20240101.csv',dtype=str);assert f.code.str.startswith('bj.').any() and f.isST.eq('1').any()
    (b/'manifest.json').write_text('{}');assert (out/'input_1_manifest.json').read_bytes()==old
    assert json.loads((out/'manifest.json').read_text())['read_only_snapshot']
    with pytest.raises(ValueError,match='immutable'):download_market_daily(out,'2024-01-01','2024-01-10',resume=True)


def test_missing_session_stays_missing_and_cannot_become_training_ready(tmp_path):
    a=source(tmp_path/'a','2024-01-01','2024-01-05',missing=['2024-01-03'])
    r=merge_market_ranges([a],tmp_path/'out')
    assert r['status']=='partial' and r['missing_sessions']==1 and r['continuous_prefix_sessions']==2
    assert not r['model_ready'] and pd.read_csv(tmp_path/'out/missing_sessions.csv').date.tolist()==['2024-01-03']
    assert not (tmp_path/'out/daily_20240103.csv').exists()


def test_overlapping_same_bytes_coalesce_with_both_sources(tmp_path):
    a=source(tmp_path/'a','2024-01-01','2024-01-05');b=source(tmp_path/'b','2024-01-05','2024-01-10')
    r=merge_market_ranges([a,b],tmp_path/'out')
    assert r['overlapping_equal_sessions']==1 and r['rows']==16
    m=json.loads((tmp_path/'out/manifest.json').read_text());assert m['file_origins']['daily_20240105.csv']==[0,1]


def test_overlapping_different_bytes_reject_without_source_preference(tmp_path):
    a=source(tmp_path/'a','2024-01-01','2024-01-05');b=source(tmp_path/'b','2024-01-05','2024-01-10')
    rewrite(b,'daily_20240105.csv',lambda f:f.__setitem__('close','11'))
    with pytest.raises(ValueError,match='conflicting'):merge_market_ranges([a,b],tmp_path/'out')
    assert not (tmp_path/'out/manifest.json').exists()


@pytest.mark.parametrize('problem',['hash','date','duplicate','basis','state','query','count','filename','synthetic'])
def test_invalid_raw_snapshot_cannot_publish_ready_manifest(tmp_path,problem):
    a=source(tmp_path/'a','2024-01-01','2024-01-05');name='daily_20240101.csv'
    if problem=='hash':(a/name).write_text('corrupt')
    if problem=='date':rewrite(a,name,lambda f:f.__setitem__('date','2024-01-02'))
    if problem=='duplicate':rewrite(a,name,lambda f:f.__setitem__('code','sh.600000'))
    if problem=='basis':rewrite(a,name,lambda f:f.__setitem__('adjustflag','2'))
    if problem=='state':rewrite(a,name,lambda f:f.__setitem__('isST','unknown'))
    m=json.loads((a/'manifest.json').read_text())
    if problem=='query':m['queries']['2024-01-01']['status']='failed'
    if problem=='count':m['files'][name]['rows']=3
    if problem=='filename':m['files']['../escape.csv']=m['files'].pop(name)
    if problem=='synthetic':m.pop('synthetic')
    (a/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError):merge_market_ranges([a],tmp_path/'out')
    assert not (tmp_path/'out/manifest.json').exists()

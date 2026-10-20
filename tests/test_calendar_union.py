import hashlib,json
import pandas as pd
import pytest
from ashare_mvp.calendar_union import seal_calendar_union


def source(path,start,end,status='running'):
    path.mkdir();dates=pd.date_range(start,end)
    f=pd.DataFrame({'calendar_date':dates.strftime('%Y-%m-%d'),'is_trading_day':(dates.dayofweek<5).astype(int)})
    f.to_csv(path/'calendar.csv',index=False)
    refresh(path,start,end,status)
    return path


def refresh(path,start,end,status='running'):
    (path/'manifest.json').write_text(json.dumps(dict(schema='market_daily_raw_v1',start=start,end=end,status=status,files={'calendar.csv':{'sha256':hashlib.sha256((path/'calendar.csv').read_bytes()).hexdigest()}})))


def test_union_seals_calendar_independently_of_running_prices(tmp_path):
    a=source(tmp_path/'a','2024-01-01','2024-01-05','downloaded_raw')
    b=source(tmp_path/'b','2024-01-06','2024-01-10')
    out=tmp_path/'out';s=seal_calendar_union([a,b],out)
    assert s['sessions']==8 and s['calendar_days']==10
    assert s['sources'][1]['price_acquisition_status']=='running'
    assert not s['model_ready'] and 'calendar only' in s['scope']
    original=(out/'input_1_manifest.json').read_bytes()
    (b/'manifest.json').write_text('{}')
    assert (out/'input_1_manifest.json').read_bytes()==original
    with pytest.raises(ValueError,match='empty'):seal_calendar_union([a],out)


def test_overlap_requires_identical_flags_and_no_preferred_source(tmp_path):
    a=source(tmp_path/'a','2024-01-01','2024-01-05')
    b=source(tmp_path/'b','2024-01-05','2024-01-10')
    assert seal_calendar_union([a,b],tmp_path/'ok')['calendar_days']==10
    f=pd.read_csv(b/'calendar.csv');f.loc[0,'is_trading_day']=0;f.to_csv(b/'calendar.csv',index=False)
    refresh(b,'2024-01-05','2024-01-10')
    with pytest.raises(ValueError,match='conflicting'):seal_calendar_union([a,b],tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


@pytest.mark.parametrize('problem',['hash','missing_day','flag','range_gap','reordered'])
def test_invalid_calendar_cannot_be_published(tmp_path,problem):
    a=source(tmp_path/'a','2024-01-01','2024-01-05')
    b=source(tmp_path/'b','2024-01-07' if problem=='range_gap' else '2024-01-06','2024-01-10')
    f=pd.read_csv(a/'calendar.csv')
    if problem=='missing_day':f=f.drop(1)
    if problem=='flag':f.loc[0,'is_trading_day']=2
    if problem=='reordered':f=f.iloc[::-1]
    f.to_csv(a/'calendar.csv',index=False)
    if problem=='hash':(a/'calendar.csv').write_text('corrupted')
    else:refresh(a,'2024-01-01','2024-01-05')
    with pytest.raises(ValueError):seal_calendar_union([a,b],tmp_path/'out')
    assert not (tmp_path/'out').exists()

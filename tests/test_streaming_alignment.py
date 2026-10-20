import hashlib,json
import pandas as pd
import pytest
from ashare_mvp.market_alignment import align_market_financials


def inputs(root):
    market,financial=root/'market',root/'financial';market.mkdir();financial.mkdir()
    manifest={'status':'partial','files':{}}
    for date in ['20231027','20231030','20231031']:
        frame=pd.DataFrame({'date':[pd.Timestamp(date)]*2,'code':['sh.600000','sz.000001'],'close':[10,20]})
        if date=='20231031':frame['pbMRQ']=[2.,3.]
        name='daily_'+date+'.csv';frame.to_csv(market/name,index=False)
        manifest['files'][name]={'sha256':hashlib.sha256((market/name).read_bytes()).hexdigest(),'columns':list(frame.columns),'rows':len(frame)}
    (market/'manifest.json').write_text(json.dumps(manifest))
    f=pd.DataFrame({'symbol':['sh.600000','sz.000001'],'report_date':['2023-09-30']*2,'available_date':['2023-10-30','2023-11-01'],'roe':[.1,.9],'debt_ratio':[.4,.8]})
    f.to_csv(financial/'financial_panel.csv',index=False)
    (financial/'summary.json').write_text(json.dumps({'partial_source':True,'panel_sha256':hashlib.sha256((financial/'financial_panel.csv').read_bytes()).hexdigest()}))
    return market,financial,manifest


def test_daily_stream_keeps_every_quote_late_field_and_visibility_boundary(tmp_path):
    market,financial,_=inputs(tmp_path)
    summary=align_market_financials(market,financial,tmp_path/'out')
    p=pd.read_csv(tmp_path/'out/aligned_market.csv',parse_dates=['date'])
    assert len(p)==6 and summary['daily_files_hash_checked']==3
    assert summary['peak_price_cross_section_rows']==2 and summary['matched_financial_rows']==2 and summary['matched_debt_rows']==2
    assert p[p.symbol=='sh.600000'].roe.tolist()[1:]==[.1,.1]
    assert p[p.symbol=='sz.000001'].roe.isna().all()
    assert p.loc[p.date<pd.Timestamp('2023-10-31'),'pbMRQ'].isna().all()
    assert p.loc[p.date==pd.Timestamp('2023-10-31'),'pbMRQ'].tolist()==[2.,3.]
    assert summary['output_sha256']==hashlib.sha256((tmp_path/'out/aligned_market.csv').read_bytes()).hexdigest()
    assert not summary['model_ready']


def test_late_source_hash_failure_never_publishes_partial_alignment(tmp_path):
    market,financial,_=inputs(tmp_path)
    (market/'daily_20231031.csv').write_text('changed')
    with pytest.raises(ValueError,match='daily file hash mismatch'):
        align_market_financials(market,financial,tmp_path/'out')
    assert not (tmp_path/'out').exists() and not list(tmp_path.glob('.out-*.pending.csv'))


def test_duplicate_quote_on_late_day_never_publishes_partial_alignment(tmp_path):
    market,financial,m=inputs(tmp_path);name='daily_20231031.csv';p=market/name
    f=pd.read_csv(p);f=pd.concat([f,f.iloc[:1]],ignore_index=True);f.to_csv(p,index=False)
    m['files'][name].update(rows=len(f),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    (market/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError,match='duplicate market price keys'):
        align_market_financials(market,financial,tmp_path/'out')
    assert not (tmp_path/'out').exists() and not list(tmp_path.glob('.out-*.pending.csv'))


def test_recorded_schema_cannot_silently_drop_or_invent_fields(tmp_path):
    market,financial,m=inputs(tmp_path)
    m['files']['daily_20231031.csv']['columns'].append('invented')
    (market/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError,match='header differs'):
        align_market_financials(market,financial,tmp_path/'out')
    assert not (tmp_path/'out').exists()

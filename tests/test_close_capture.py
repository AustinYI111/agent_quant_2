from datetime import datetime,timezone
from types import SimpleNamespace
import json
import pandas as pd
import pytest
from ashare_mvp import close_capture as mod
from ashare_mvp.public_data import _FrameResult
from ashare_mvp.phase2_protocol import completed_date_limit,sha256


class Client:
    def __init__(self,fault=None):self.fault=fault;self.queries=0;self.closed=0
    def login(self):return SimpleNamespace(error_code='0',error_msg='')
    def logout(self):self.closed+=1
    def query_trade_dates(self,start_date,end_date):
        self.queries+=1;dates=pd.date_range(start_date,end_date)
        frame=pd.DataFrame(dict(calendar_date=dates.strftime('%Y-%m-%d'),is_trading_day='1'))
        if self.fault=='calendar_gap' and len(frame)>1:frame=frame.drop(index=1)
        if self.fault=='non_session':frame.loc[frame.calendar_date==str(self.signal.date()),'is_trading_day']='0'
        return _FrameResult(frame)
    def query_all_stock(self,day):
        self.queries+=1;names=['正常股份'] if self.fault!='empty_name' else ['']
        return _FrameResult(pd.DataFrame(dict(code=['sh.600000'],code_name=names)))
    def query_stock_industry(self,date):
        self.queries+=1;update=str((self.signal+pd.Timedelta(days=1)).date()) if self.fault=='future_industry' else date
        return _FrameResult(pd.DataFrame(dict(code=['sh.600000'],industry=['C13'],updateDate=[update])))
    def query_history_k_data_plus(self,*args,**kwargs):
        self.queries+=1;dates=pd.date_range(kwargs['start_date'],kwargs['end_date'])
        frame=pd.DataFrame(dict(date=dates.strftime('%Y-%m-%d'),code='sh.000300',open='4000',high='4010',low='3990',close='4005',volume='100',amount='100000',pctChg='0.1'))
        if self.fault=='benchmark_gap':frame=frame.iloc[1:]
        return _FrameResult(frame)
    def query_daily_history_k_AStock(self,date):
        self.queries+=1
        return _FrameResult(pd.DataFrame(dict(date=[date],code=['sh.600000'],open=['10'],close=['10'],preclose=['10'],volume=['100'],adjustflag=['3'],tradestatus=['1'],isST=['0'])))


def inputs(tmp_path,monkeypatch,fault=None):
    project=tmp_path/'project';work=tmp_path/'work';project.mkdir();work.mkdir()
    (project/'sealed').mkdir()
    monkeypatch.setattr(mod,'load_seal',lambda *a:({},dict(protocol_sha256='a'*64)))
    signal=completed_date_limit(datetime.now(timezone.utc));client=Client(fault);client.signal=signal
    args=dict(project=str(project),work_root=str(work),seal='sealed',signal=str(signal.date()),calendar_end=str((signal+pd.Timedelta(days=3)).date()),out='@work/capture',client=client)
    return args,work,client


def test_successful_raw_capture_keeps_real_observation_clocks_and_all_hashes(tmp_path,monkeypatch):
    args,work,client=inputs(tmp_path,monkeypatch);result=mod.capture_close(**args)
    assert result['status']=='raw_acquired' and result['prediction_ready'] is False
    assert client.closed>=1
    for name,digest in result['output_hashes'].items():assert sha256(work/'capture'/name)==digest
    assert 'reference/capture.json' in result['output_hashes'] and 'market/capture.json' in result['output_hashes']
    assert (work/'capture/market/closed_market_observations.json').is_file()
    assert not (work/'capture/predictions.sqlite').exists()
    before=sha256(work/'capture/capture.json')
    with pytest.raises(ValueError,match='fresh'):mod.capture_close(**args)
    assert sha256(work/'capture/capture.json')==before


def test_before_close_gate_makes_no_provider_request_or_output(tmp_path,monkeypatch):
    args,work,client=inputs(tmp_path,monkeypatch)
    early=pd.Timestamp(args['signal']).tz_localize('Asia/Shanghai')+pd.Timedelta(hours=17,minutes=19)
    monkeypatch.setattr(mod,'now',lambda:early)
    with pytest.raises(ValueError,match='17:20'):mod.capture_close(**args)
    assert client.queries==0 and not (work/'capture').exists()


@pytest.mark.parametrize('fault',['calendar_gap','non_session'])
def test_incomplete_calendar_or_wrong_requested_session_cannot_be_relabeled(tmp_path,monkeypatch,fault):
    args,work,client=inputs(tmp_path,monkeypatch,fault)
    with pytest.raises(ValueError):mod.capture_close(**args)
    assert json.loads((work/'capture/capture.json').read_text())['status']=='failed'
    assert not (work/'capture/market/latest').exists()


@pytest.mark.parametrize('fault',['empty_name','future_industry','benchmark_gap'])
def test_failed_reference_preserves_raw_market_but_never_reports_complete(tmp_path,monkeypatch,fault):
    args,work,client=inputs(tmp_path,monkeypatch,fault);result=mod.capture_close(**args)
    assert result['status']=='partial' and result['prediction_ready'] is False
    refs=json.loads((work/'capture/reference/capture.json').read_text())
    assert sum(r['status']=='failed' for r in refs['queries'].values())==1
    assert (work/'capture/market/latest/manifest.json').is_file()


@pytest.mark.parametrize('out',['local_output','@work/../escape'])
def test_output_cannot_use_project_disk_or_escape_workspace(tmp_path,monkeypatch,out):
    args,work,client=inputs(tmp_path,monkeypatch);args['out']=out
    with pytest.raises(ValueError):mod.capture_close(**args)
    assert client.queries==0

import pandas as pd

from ashare_mvp.dividend_download import download_dividends
from ashare_mvp.public_data import _FrameResult


class Client:
    def __init__(self, wrong_year=False, fail=False):
        self.calls = 0
        self.wrong_year, self.fail = wrong_year, fail

    def login(self):
        return type("Login", (), {"error_code": "0"})()

    def logout(self):
        pass

    def query_dividend_data(self, code, year, yearType):
        self.calls += 1
        assert yearType == "operate"
        if self.fail:
            raise RuntimeError("provider unavailable")
        return _FrameResult(pd.DataFrame([dict(code=code, dividPlanDate=f"{year}-06-01",
            dividOperateDate=f"{int(year) + int(self.wrong_year)}-06-15", dividPayDate=f"{year}-06-15",
            dividStockMarketDate="", dividCashPsBeforeTax="0.1", dividStocksPs="0",
            dividReserveToStockPs="", dividCashStock="10派1元")]))


def test_resume_checks_hash_and_never_refetches_completed_event(tmp_path):
    args = (tmp_path, ["sh.600000"], 2018, 2019)
    client = Client()
    result = download_dividends(*args, client=client, delay=0)
    assert result["status"] == "downloaded_raw" and result["rows"] == 2
    assert result["certified"] is False and client.calls == 2
    result = download_dividends(*args, resume=True, client=client, delay=0)
    assert result["status"] == "downloaded_raw" and client.calls == 2
    (tmp_path / "sh.600000_2018.csv").write_text("changed", encoding="utf-8")
    result = download_dividends(*args, resume=True, client=client, delay=0)
    assert result["status"] == "failed" and "evidence changed" in result["error"]
    assert client.calls == 2


def test_wrong_execution_year_is_not_saved(tmp_path):
    result = download_dividends(tmp_path, ["sh.600000"], 2018, 2018, client=Client(wrong_year=True), delay=0)
    assert result["status"] == "partial" and result["files"] == {}
    assert result["queries"]["sh.600000_2018.csv"]["status"] == "failed"


def test_provider_outage_stops_then_resume_only_retries_missing(tmp_path):
    client = Client(fail=True)
    args = (tmp_path, ["sh.600000"], 2018, 2023)
    result = download_dividends(*args, client=client, delay=0)
    assert result["status"] == "failed" and client.calls == 3
    healthy = Client()
    result = download_dividends(*args, resume=True, client=healthy, delay=0)
    assert result["status"] == "downloaded_raw" and healthy.calls == 6
    assert "error" not in result


class DuplicateClient(Client):
    def query_dividend_data(self,code,year,yearType):
        row=super().query_dividend_data(code,year,yearType)
        # Preserve both raw rows; duplicate semantics belong to normalization.
        from ashare_mvp.public_data import _result_frame
        frame=_result_frame(row)
        return _FrameResult(pd.concat([frame,frame],ignore_index=True))


def test_raw_duplicate_execution_rows_retained_for_normalization(tmp_path):
    result=download_dividends(tmp_path,['sh.600000'],2018,2018,client=DuplicateClient(),delay=0)
    assert result['status']=='downloaded_raw' and result['rows']==2
    info=result['files']['sh.600000_2018.csv']
    assert info['executed_rows']==2 and info['duplicate_execution_rows']==2
    assert len(pd.read_csv(tmp_path/'sh.600000_2018.csv'))==2

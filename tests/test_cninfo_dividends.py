import pytest
from ashare_mvp.cninfo_dividends import download_cninfo_dividends


def payload(symbol):
    row = dict(F006D="2018-07-06", F020D="2018-07-13", F023D="2018-07-16", F025D=None,
               F010N=0, F011N=0, F012N=1, F007V="10派1元", F001V="2017年报", F044V="年度分红")
    return dict(resultcode=200, count=1, total=1, records=[row])


def test_full_history_sealed_and_resume_skips_requests(tmp_path):
    calls = []
    def fetch(symbol):
        calls.append(symbol)
        return payload(symbol)
    args = (tmp_path, ["sh.600000"])
    result = download_cninfo_dividends(*args, fetch=fetch, delay=0)
    assert result["status"] == "downloaded_raw" and result["rows"] == 1
    assert result["model_ready"] is False
    download_cninfo_dividends(*args, fetch=fetch, resume=True, delay=0)
    assert len(calls) == 1
    (tmp_path / "sh.600000.csv").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        download_cninfo_dividends(*args, fetch=fetch, resume=True, delay=0)


def test_truncated_history_not_saved(tmp_path):
    def fetch(symbol):
        result = payload(symbol)
        result["total"] = 20
        return result
    result = download_cninfo_dividends(tmp_path, ["sh.600000"], fetch=fetch, delay=0)
    assert result["status"] == "partial" and result["rows"] == 0
    assert not (tmp_path / "sh.600000.json").exists()


def test_outage_stops_after_three_failures(tmp_path):
    calls = []
    def fetch(symbol):
        calls.append(symbol)
        return dict(resultcode=500)
    result = download_cninfo_dividends(tmp_path, [f"sh.60000{x}" for x in range(5)], fetch=fetch, delay=0)
    assert result["status"] == "partial" and len(calls) == 3

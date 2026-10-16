import pytest

from ashare_mvp.dividend_market import download_dividend_market, fetch_dividend_period


def page(period, number, count=501, duplicate=False, short=False):
    start = (number - 1) * 500
    rows = [dict(SECUCODE=f"{0 if duplicate else index:06d}.SZ", REPORT_DATE=period,
                 EX_DIVIDEND_DATE=None, PLAN_NOTICE_DATE="2024-03-01", NOTICE_DATE="2024-03-01",
                 ASSIGN_PROGRESS="预案", PRETAX_BONUS_RMB=1, BONUS_RATIO=0, IT_RATIO=0,
                 IMPL_PLAN_PROFILE="10派1元") for index in range(start, min(start + 500, count))]
    if short:
        rows = rows[:-1]
    return dict(success=True, result=dict(pages=(count + 499) // 500, count=count, data=rows))


def test_fetch_all_pages_and_refuse_partial_or_repeated_security(tmp_path):
    result = fetch_dividend_period("2023-12-31", tmp_path / "good", page, delay=0)
    assert result["rows"] == 501 and result["pages"] == 2
    for kind in ["short", "duplicate"]:
        with pytest.raises(ValueError):
            fetch_dividend_period("2023-12-31", tmp_path / kind,
                                  lambda period, number: page(period, number, **{kind: True}), delay=0)
        assert not (tmp_path / kind).exists()


def test_batch_resume_only_uses_verified_cache(tmp_path):
    calls = []
    def fetch(period, number):
        calls.append((period, number))
        return page(period, number, count=1)
    args = (tmp_path, "2023-12-31", "2023-12-31")
    result = download_dividend_market(*args, fetch=fetch)
    assert result["status"] == "downloaded_raw" and result["model_ready"] is False
    download_dividend_market(*args, fetch=fetch, resume=True)
    assert len(calls) == 1
    (tmp_path / "2023Q4/records.csv").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        download_dividend_market(*args, fetch=fetch, resume=True)


def test_provider_pagination_change_and_outage_are_visible(tmp_path):
    def changed(period, number):
        result = page(period, number)
        if number == 2:
            result["result"]["count"] = 502
        return result
    with pytest.raises(ValueError, match="pagination changed"):
        fetch_dividend_period("2023-12-31", tmp_path / "changed", changed, delay=0)
    calls = []
    def fail(period, number):
        calls.append(period)
        return dict(success=False, message="unavailable")
    result = download_dividend_market(tmp_path / "outage", "2017-03-31", "2023-12-31", fetch=fail)
    assert result["status"] == "partial" and len(calls) == 3 and result["saved_periods"] == 0

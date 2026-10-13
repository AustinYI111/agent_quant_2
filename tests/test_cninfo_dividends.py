import pytest
from ashare_mvp.cninfo_dividends import download_cninfo_dividends
from ashare_mvp.cninfo_dividends import normalize_cninfo_dividends


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


def normalize_fixture(tmp_path, changes):
    import pandas as pd
    data = payload("sh.600000")
    data["records"][0].update(changes)
    source = tmp_path / "raw"
    download_cninfo_dividends(source, ["sh.600000"], fetch=lambda symbol: data, delay=0)
    calendar = tmp_path / "calendar.csv"
    pd.DataFrame({"date": pd.bdate_range("2018-07-02", "2018-07-20")}).to_csv(calendar, index=False)
    return source, calendar, tmp_path / "normalized"


def test_normalization_preserves_real_payment_and_credit_without_inventing_tradability(tmp_path):
    import pandas as pd
    args = normalize_fixture(tmp_path, dict(F010N=2, F011N=3, F007V="10送2转3派1元", F025D="2018-07-17"))
    result = normalize_cninfo_dividends(*args)
    assert result["accepted"] == 1 and result["rejected"] == 0
    row = pd.read_csv(args[2] / "actions.csv").iloc[0]
    assert row.cash_per_share == 0.1 and row.share_multiplier == 1.5
    assert row.pay_date == "2018-07-16" and row.share_credit_date == "2018-07-17"
    assert pd.isna(row.stock_market_date)


@pytest.mark.parametrize("changes", [dict(F023D=None), dict(F012N=2), dict(F006D="2018-07-13")])
def test_unproven_payments_or_terms_are_quarantined(tmp_path, changes):
    args = normalize_fixture(tmp_path, changes)
    result = normalize_cninfo_dividends(*args)
    assert result["accepted"] == 0 and result["rejected"] == 1 and result["status"] == "partial"


def test_irrelevant_history_classified_outside_calendar(tmp_path):
    args = normalize_fixture(tmp_path, dict(F020D="2017-07-13"))
    result = normalize_cninfo_dividends(*args)
    assert result["outside_calendar"] == 1 and result["accepted"] == 0 and result["rejected"] == 0


def test_competing_same_date_events_all_quarantined_not_arbitrarily_selected(tmp_path):
    import pandas as pd
    data = payload("sh.600000")
    data["records"] += [dict(data["records"][0], F044V="另一方案")]
    data["count"] = data["total"] = 2
    source = tmp_path / "raw"
    download_cninfo_dividends(source, ["sh.600000"], fetch=lambda symbol: data, delay=0)
    calendar = tmp_path / "calendar.csv"
    pd.DataFrame({"date": pd.bdate_range("2018-07-02", "2018-07-20")}).to_csv(calendar, index=False)
    result = normalize_cninfo_dividends(source, calendar, tmp_path / "out")
    assert result["accepted"] == 0 and result["rejected"] == 2 and result["status"] == "partial"
    assert pd.read_csv(tmp_path / "out/actions.csv").empty

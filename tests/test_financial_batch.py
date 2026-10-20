import json
import hashlib
import pandas as pd
import pytest

from ashare_mvp.financial_batch import fetch_quarter, download_financials, normalize_financial_batch
from ashare_mvp.market_alignment import financial_events, align_market_financials


def payload(page, count=501):
    indices = range(500) if page == 1 else range(500, count)
    rows = [dict(SECUCODE=f"{index:06d}.SZ", REPORTDATE="2023-09-30", NOTICE_DATE="2023-10-27",
                 UPDATE_DATE="2023-10-27", EITIME="2023-10-26 19:00:00", WEIGHTAVG_ROE=10,
                 YSTZ=20, SJLTZ=-5) for index in indices]
    return dict(success=True, result=dict(pages=2, count=count, data=rows))


def test_quarter_complete_pages_and_resume(tmp_path):
    calls = []
    def fetch(date, page):
        calls.append((date, page))
        return payload(page)
    result = download_financials(tmp_path / "raw", "2023-09-30", "2023-09-30", fetch=fetch)
    assert result["status"] == "downloaded_raw"
    assert result["quarters"]["2023-09-30"]["rows"] == 501
    again = download_financials(tmp_path / "raw", "2023-09-30", "2023-09-30", resume=True, fetch=fetch)
    assert again["saved_quarters"] == 1 and len(calls) == 2


def test_missing_financial_page_rejected(tmp_path):
    def fetch(date, page):
        value = payload(page)
        if page == 2:
            value["result"]["data"] = []
        return value
    with pytest.raises(ValueError, match="empty financial"):
        fetch_quarter("2023-09-30", tmp_path, fetch, delay=0)


def test_duplicate_cross_page_financial_record_rejected(tmp_path):
    def fetch(date, page):
        value = payload(page)
        if page == 2:
            value["result"]["data"][0]["SECUCODE"] = "000000.SZ"
        return value
    with pytest.raises(ValueError, match="duplicate"):
        fetch_quarter("2023-09-30", tmp_path, fetch, delay=0)


def test_financial_update_clock_prevents_earlier_use(tmp_path):
    def fetch(date, page):
        value = payload(page)
        if page == 1:
            value["result"]["data"][0]["UPDATE_DATE"] = "2026-08-06"
        return value
    source, out = tmp_path / "raw", tmp_path / "normalized"
    download_financials(source, "2023-09-30", "2023-09-30", fetch=fetch)
    result = normalize_financial_batch(source, pd.bdate_range("2023-10-01", "2024-01-31"), out)
    frame = pd.read_csv(out / "financial_panel.csv", parse_dates=["available_date"])
    assert pd.isna(frame.available_date.iloc[0])
    assert frame.available_date.iloc[1] == pd.Timestamp("2023-10-30")
    assert frame.roe.iloc[1] == 0.1 and frame.revenue_growth.iloc[1] == 0.2
    assert frame.debt_ratio.isna().all()
    assert result["update_after_publication"] == 1 and not result["certified"]


def test_late_old_period_update_does_not_replace_latest_report():
    panel = pd.DataFrame({"symbol": ["sh.600000"] * 3,
                          "report_date": pd.to_datetime(["2022-12-31", "2023-03-31", "2022-12-31"]),
                          "available_date": pd.to_datetime(["2023-04-03", "2023-04-28", "2023-05-10"]),
                          "roe": [0.1, 0.2, 0.9]})
    result = financial_events(panel)
    assert len(result) == 2 and result.roe.iloc[-1] == 0.2


def test_asof_join_never_uses_next_session_financial(tmp_path):
    market, financial = tmp_path / "market", tmp_path / "financial"
    market.mkdir()
    financial.mkdir()
    manifest = {"status": "partial", "files": {}}
    for date, price in [("20231027", 10), ("20231030", 11)]:
        price_file = market / f"daily_{date}.csv"
        pd.DataFrame({"date": [pd.to_datetime(date)], "code": ["sh.600000"], "close": [price]}).to_csv(price_file, index=False)
        manifest["files"][price_file.name] = {"sha256": hashlib.sha256(price_file.read_bytes()).hexdigest()}
    (market / "manifest.json").write_text(json.dumps(manifest))
    financial_file = financial / "financial_panel.csv"
    pd.DataFrame({"symbol": ["sh.600000"], "report_date": ["2023-09-30"],
                  "available_date": ["2023-10-30"], "roe": [0.1]}).to_csv(financial_file, index=False)
    summary = {"panel_sha256": hashlib.sha256(financial_file.read_bytes()).hexdigest(), "partial_source": True}
    (financial / "summary.json").write_text(json.dumps(summary))
    info = align_market_financials(market, financial, tmp_path / "aligned")
    result = pd.read_csv(tmp_path / "aligned" / "aligned_market.csv")
    assert pd.isna(result.roe.iloc[0]) and result.roe.iloc[1] == 0.1
    assert info["matched_financial_rows"] == 1 and not info["certified"]

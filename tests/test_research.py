from dataclasses import replace
from types import SimpleNamespace
import json

import numpy as np
import pandas as pd
import pytest

from ashare_mvp.demo import make_demo
from ashare_mvp.data import validate
from ashare_mvp.experiments import compare_oneil
from ashare_mvp.model import Config
from ashare_mvp.public_data import download_public, is_ashare, _result_frame, _FrameResult, readiness_audit
from ashare_mvp.research import technical_observations, financial_research, value_holdings, load_holdings, daily_report, update_prediction_history


@pytest.fixture(scope="module")
def data():
    d = make_demo(stocks=2, periods=800)
    p = d.prices.copy()
    p["raw_close"] = p.close / 2
    f = d.fundamentals.copy()
    f["industry"] = "synthetic sector"
    return replace(d, prices=p, fundamentals=f)


def test_technical_ranges_have_actual_price_scale(data):
    date = data.calendar[400]
    result = technical_observations(data, date, ["SYN0000"]).iloc[0]
    price = data.prices[(data.prices.date == date) & (data.prices.symbol == "SYN0000")].iloc[0]
    assert result.current_price == price.raw_close
    assert result.price_basis == "current_raw_price"
    assert 0 <= result.pullback_low <= result.pullback_high
    assert result.breakout_low <= result.breakout_high


def test_technical_ranges_future_invariant(data):
    date = data.calendar[400]
    p = data.prices.copy()
    p.loc[p.date > date, ["close", "volume", "raw_close"]] *= 100
    a = technical_observations(data, date, ["SYN0000"])
    b = technical_observations(replace(data, prices=p), date, ["SYN0000"])
    pd.testing.assert_frame_equal(a, b)


def test_raw_quote_absence_is_explicit(data):
    d = replace(data, prices=data.prices.drop(columns="raw_close"))
    observations = technical_observations(d, data.calendar[400], ["SYN0000"])
    assert observations.price_basis.iloc[0] == "adjusted_units_only"
    h = pd.DataFrame({"symbol": ["SYN0000"], "shares": [100], "average_cost": [10]})
    valued = value_holdings(d, data.calendar[400], h)
    assert valued.market_value.isna().all()


def test_holdings_actual_raw_valuation(data):
    h = pd.DataFrame({"symbol": ["SYN0000"], "shares": [100], "average_cost": [10]})
    valued = value_holdings(data, data.calendar[400], h).iloc[0]
    assert valued.market_value == pytest.approx(100 * valued.raw_price)
    assert valued.unrealized_pnl == pytest.approx(100 * (valued.raw_price - 10))


def test_financial_facts_and_dated_peers(data):
    date = data.calendar[400]
    a = financial_research(data, date, ["SYN0000"])
    f = data.fundamentals.copy()
    f.loc[f.available_date > date, "roe"] = 100
    b = financial_research(replace(data, fundamentals=f), date, ["SYN0000"])
    pd.testing.assert_frame_equal(a, b)
    assert a.available_date.iloc[0] <= date
    assert a.peer_count.iloc[0] == 2


def test_prediction_history_immutable_and_matures(data, tmp_path):
    picks = pd.DataFrame([dict(date=data.calendar[300], symbol="SYN0000", score=0.42, rank=1)])
    path = tmp_path / "ledger.csv"
    initial = update_prediction_history(data, picks, data.calendar[310], path)
    assert initial.target.isna().all()
    mature = update_prediction_history(data, picks, data.calendar[-1], path)
    assert len(mature) == 3 and mature.target.notna().all()
    update_prediction_history(data, picks, data.calendar[-1], path)
    assert len(pd.read_csv(path)) == 3
    changed = picks.copy()
    changed["score"] = 0.9
    with pytest.raises(ValueError, match="overwrite"):
        update_prediction_history(data, changed, data.calendar[-1], path)


def test_ledger_time_travel_rejected(data, tmp_path):
    picks = pd.DataFrame([dict(date=data.calendar[400], symbol="SYN0000", score=0.42, rank=1)])
    path = tmp_path / "ledger.csv"
    update_prediction_history(data, picks, data.calendar[410], path)
    earlier = picks.copy()
    earlier["date"] = data.calendar[300]
    before = path.read_bytes()
    with pytest.raises(ValueError, match="separate ledger"):
        update_prediction_history(data, earlier, data.calendar[310], path)
    assert path.read_bytes() == before


def test_report_flags_stale_and_incomplete(data, tmp_path):
    picks = pd.DataFrame([dict(date=data.calendar[400], symbol="SYN0000", score=0.42, rank=1)])
    h = pd.DataFrame({"symbol": ["UNKNOWN"], "shares": [100], "average_cost": [10]})
    out = tmp_path / "report"
    result = daily_report(data, picks, h, out, data.calendar[410])
    assert result["stale_prediction"] and not result["valuation_complete"]
    assert result["market_value"] is None
    assert (out / "daily_report.md").exists()
    json.loads((out / "summary.json").read_text())


def test_manual_holdings_reject_duplicate_and_nonfinite(tmp_path):
    file = tmp_path / "holdings.csv"
    file.write_text("symbol,shares,average_cost\n000001,100,10\n000001,100,10\n")
    with pytest.raises(ValueError, match="unique"):
        load_holdings(file)
    file.write_text("symbol,shares,average_cost\n000001,inf,10\n")
    with pytest.raises(ValueError, match="invalid"):
        load_holdings(file)
    file.write_text("symbol,shares,average_cost\n000001,100,10\n")
    assert load_holdings(file).symbol.iloc[0] == "000001"


def test_invalid_raw_quote_rejected(data):
    p = data.prices.copy()
    p.loc[0, "raw_close"] = np.inf
    with pytest.raises(ValueError, match="invalid raw_close"):
        validate(replace(data, prices=p))


def test_ablation_changes_only_factor_switch(tmp_path):
    d = make_demo(stocks=20, periods=750)
    cfg = Config(min_train_groups=3, n_estimators=3)
    result = compare_oneil(d, tmp_path / "experiment", cfg, evaluation_start=d.calendar[500])
    baseline = json.loads((tmp_path / "experiment" / "baseline" / "summary.json").read_text())
    variant = json.loads((tmp_path / "experiment" / "oneil" / "summary.json").read_text())
    assert baseline["config"]["oneil"] is False
    assert variant["config"]["oneil"] is True
    a, b = baseline["config"].copy(), variant["config"].copy()
    a.pop("oneil")
    b.pop("oneil")
    assert a == b
    assert baseline["input_hashes"] == variant["input_hashes"]
    assert result["synthetic"]


class FakePublic:
    def login(self):
        return SimpleNamespace(error_code="0", error_msg="")

    def logout(self):
        pass

    def query_trade_dates(self, start, end):
        return _FrameResult(pd.DataFrame({"calendar_date": ["2024-01-02", "2024-01-31"], "is_trading_day": ["1", "1"]}))

    def query_all_stock(self, date):
        return _FrameResult(pd.DataFrame({"code": ["sh.600000", "sh.000001", "sz.000001"]}))

    def __getattr__(self, name):
        return lambda *args, **kwargs: _FrameResult(pd.DataFrame({"provider_field": ["raw_value"]}))


def test_raw_public_download_manifest_never_certifies_pit(tmp_path):
    result = download_public(tmp_path / "raw", "2024-01-02", "2024-01-31", max_symbols=1, client=FakePublic())
    assert result["status"] == "downloaded_raw"
    assert not result["training_ready"] and not result["synthetic"]
    assert result["downloaded_symbols"] == ["sh.600000"]
    assert "profit_sh_600000_2024Q1.csv" not in result["files"]
    assert all("sha256" in item for item in result["files"].values())


def test_public_failure_is_saved(tmp_path):
    client = FakePublic()
    client.query_trade_dates = lambda *args: SimpleNamespace(error_code="-1", error_msg="offline")
    result = download_public(tmp_path / "raw", "2024-01-02", "2024-01-31", client=client)
    assert result["status"] == "failed"
    assert result["queries"][0]["status"] == "failed"
    assert (tmp_path / "raw" / "manifest.json").exists()


def test_silent_provider_page_truncation_rejected():
    result = _FrameResult(pd.DataFrame({"code": ["sh.600000", "sz.000001"]}))
    result.data = [["sh.600000"], ["sz.000001"]]
    result.per_page_count = "2"
    with pytest.raises(RuntimeError, match="silent truncation"):
        _result_frame(result)


def test_public_audit_detects_modified_files(tmp_path):
    path = tmp_path / "raw"
    result = download_public(path, "2024-01-02", "2024-01-31", max_symbols=1, client=FakePublic())
    target = next(iter(result["files"]))
    (path / target).write_text("provider_field\nchanged\n")
    audit = readiness_audit(path)
    assert target in audit["hash_mismatches"]
    assert not audit["training_ready"]


@pytest.mark.parametrize("code,expected", [("sh.000001", False), ("sh.600000", True),
                                           ("sz.300001", True), ("bj.830001", False), ("sh.510300", False)])
def test_universe_filter(code, expected):
    assert is_ashare(code) == expected


def identity_data(data):
    from ashare_mvp.security_identity import apply_identity_dataset
    p=data.prices.copy(); f=data.fundamentals.copy(); day=data.calendar[350]
    selected=p.symbol.eq("SYN0000")
    p.loc[selected,"symbol"]="sz.000043"
    p.loc[selected & p.date.ge(day),"symbol"]="sz.001914"
    f.loc[f.symbol.eq("SYN0000"),"symbol"]="sz.000043"
    changes=pd.DataFrame([dict(old_symbol="sz.000043",new_symbol="sz.001914",announcement_date=str(data.calendar[340].date()),effective_date=str(day.date()),share_ratio=1,source_url="https://static.cninfo.com.cn/test-fixture.pdf")])
    return apply_identity_dataset(replace(data,prices=p,fundamentals=f),changes)


def test_new_code_holdings_value_and_daily_report_find_canonical_history(data,tmp_path):
    d=identity_data(data); day=d.calendar[400]
    h=pd.DataFrame(dict(symbol=["sz.001914"],shares=[100],average_cost=[10.]))
    original=h.copy(deep=True)
    row=value_holdings(d,day,h).iloc[0]
    assert row.symbol=="sz.000043" and row.input_symbol=="sz.001914" and row.trading_symbol=="sz.001914"
    assert row.market_value==pytest.approx(100*row.raw_price)
    picks=pd.DataFrame(dict(date=[day],symbol=["sz.000043"],score=[.5],rank=[1]))
    summary=daily_report(d,picks,h,tmp_path/'report',day)
    assert summary["valuation_complete"] and summary["position_count"]==1
    technical=pd.read_csv(tmp_path/'report/technical_observations.csv')
    assert technical.status.eq("observed").all() and technical.trading_symbol.eq("sz.001914").all()
    assert "sz.001914" in (tmp_path/'report/daily_report.md').read_text(encoding='utf-8')
    pd.testing.assert_frame_equal(h,original)


def test_duplicate_alias_holdings_and_future_code_rejected(data):
    d=identity_data(data)
    h=pd.DataFrame(dict(symbol=["sz.000043","sz.001914"],shares=[100,100],average_cost=[10.,10.]))
    with pytest.raises(ValueError,match="same issuer"): value_holdings(d,d.calendar[400],h)
    with pytest.raises(ValueError,match="not active"): value_holdings(d,d.calendar[300],h.iloc[1:])


def test_identity_report_handles_explicit_empty_synthetic_holdings(data,tmp_path):
    d=identity_data(data)
    holdings=pd.DataFrame(columns=["symbol","shares","average_cost"])
    picks=pd.DataFrame(columns=["date","symbol","score","rank"])
    result=daily_report(d,picks,holdings,tmp_path/'empty',d.calendar[400])
    assert result["position_count"]==0 and result["market_value"]==0
    assert pd.read_csv(tmp_path/'empty/technical_observations.csv').empty
    assert pd.read_csv(tmp_path/'empty/holdings.csv').empty

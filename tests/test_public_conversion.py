import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from ashare_mvp.demo import make_demo
from ashare_mvp.model import Config
from ashare_mvp.public_conversion import convert_public, public_samples, run_public_research
from ashare_mvp.market_download import download_market_daily
from ashare_mvp.public_data import _FrameResult


def staging(path, stocks=20, periods=900):
    path.mkdir()
    data = make_demo(stocks=stocks, periods=periods)
    manifest = dict(source="synthetic public-format fixture", synthetic=True, downloaded_symbols=[], files={}, blockers=["test fixture; not actual market evidence"])

    def save(name, frame):
        file = path / name
        frame.to_csv(file, index=False)
        manifest["files"][name] = dict(sha256=hashlib.sha256(file.read_bytes()).hexdigest())

    save("trade_dates.csv", pd.DataFrame({"calendar_date": data.calendar, "is_trading_day": "1"}))
    for index, (_, rows) in enumerate(data.prices.groupby("symbol")):
        symbol = f"sh.{600000 + index:06d}"
        key = symbol.replace(".", "_")
        manifest["downloaded_symbols"].append(symbol)
        save(f"basic_{key}.csv", pd.DataFrame({"code": [symbol], "ipoDate": ["2010-01-01"]}))
        prices = pd.DataFrame(dict(date=rows.date, code=symbol, open=rows.total_return_open,
                                   close=rows.close, preclose=rows.close.shift().fillna(rows.close.iloc[0]),
                                   volume=rows.volume, tradestatus="1", isST="0", peTTM=10 + index, pbMRQ=2 + index / 10))
        save(f"prices_raw_{key}.csv", prices)
        save(f"prices_adjusted_{key}.csv", prices)
        for period in pd.period_range(data.calendar[0] - pd.DateOffset(years=1), data.calendar[-1], freq="Q"):
            stat = period.end_time.normalize()
            pub = stat + pd.Timedelta(days=50)
            common = dict(code=[symbol], statDate=[stat], pubDate=[pub])
            save(f"profit_{key}_{period.year}Q{period.quarter}.csv", pd.DataFrame({**common, "roeAvg": [0.1 + index / 100], "MBRevenue": [1000 + period.year]}))
            save(f"growth_{key}_{period.year}Q{period.quarter}.csv", pd.DataFrame({**common, "YOYNI": [0.1]}))
            save(f"balance_{key}_{period.year}Q{period.quarter}.csv", pd.DataFrame({**common, "liabilityToAsset": [0.4]}))
    (path / "manifest.json").write_text(json.dumps(manifest))
    return data, manifest


def test_public_conversion_preserves_source_and_does_not_invent_amounts(tmp_path):
    source, out = tmp_path / "source", tmp_path / "out"
    staging(source, stocks=1, periods=350)
    metadata = convert_public(source, out)
    prices = pd.read_csv(out / "prices.csv")
    assert metadata["synthetic"] and not metadata["certified"]
    assert "market_cap" not in prices and "book_equity" not in prices
    assert prices.earnings_yield.iloc[0] == pytest.approx(0.1)
    assert prices.book_yield.iloc[0] == pytest.approx(0.5)
    financial = pd.read_csv(out / "fundamentals.csv", parse_dates=["available_date", "publication_date"])
    assert (financial.available_date > financial.publication_date).all()


def test_missing_income_is_not_replaced_with_profit_growth(tmp_path):
    source, out = tmp_path / "source", tmp_path / "out"
    _, manifest = staging(source, stocks=1, periods=350)
    for name in list(manifest["files"]):
        if name.startswith("profit_"):
            file = source / name
            frame = pd.read_csv(file)
            frame["MBRevenue"] = np.nan
            frame.to_csv(file, index=False)
            manifest["files"][name]["sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
    (source / "manifest.json").write_text(json.dumps(manifest))
    convert_public(source, out)
    financial = pd.read_csv(out / "fundamentals.csv")
    assert financial.revenue_growth.isna().all()
    assert financial.earnings_growth.notna().all()


def test_public_incomplete_daily_coverage_not_silently_excluded(tmp_path):
    source, out = tmp_path / "source", tmp_path / "out"
    _, manifest = staging(source, stocks=1, periods=350)
    name = "prices_raw_sh_600000.csv"
    file = source / name
    pd.read_csv(file).iloc[:-1].to_csv(file, index=False)
    manifest["files"][name]["sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
    (source / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="terminal delisting"):
        convert_public(source, out)
    assert (out / "coverage_failures.csv").exists()


def test_public_model_requires_explicit_exploration_and_purges_labels(tmp_path):
    source, prepared = tmp_path / "source", tmp_path / "prepared"
    data, _ = staging(source)
    convert_public(source, prepared)
    cfg = Config(min_train_groups=3, n_estimators=3)
    with pytest.raises(ValueError, match="exploratory mode"):
        run_public_research(prepared, tmp_path / "no", cfg)
    summary = run_public_research(prepared, tmp_path / "run", cfg, exploratory=True)
    assert summary["trained_months"] > 0 and summary["latest_selection_count"] == 20
    assert summary["certified"] is False and summary["data"]["synthetic"] is True
    audit = pd.read_csv(tmp_path / "run" / "training_audit.csv", parse_dates=["cutoff", "max_label_end"])
    trained = audit[audit.status == "trained"]
    assert (trained.max_label_end < trained.cutoff).all()
    _, samples, _, _ = public_samples(prepared, cfg, data.calendar[600])
    assert samples.date.max() <= data.calendar[600]
    from ashare_mvp.model_registry import export_model,predict_model
    from ashare_mvp.public_conversion import load_public_data
    export_model(tmp_path / "run", tmp_path / "portable")
    recovered=predict_model(tmp_path / "portable",load_public_data(prepared),tmp_path / "recovered.csv")
    original=pd.read_csv(tmp_path / "run/latest_top20.csv")
    assert recovered.symbol.tolist()==original.symbol.tolist()
    np.testing.assert_allclose(recovered.score,original.score,rtol=0,atol=1e-12)
    from dataclasses import replace
    from ashare_mvp.model_registry import check_model
    public_data=load_public_data(prepared)
    wrong=replace(public_data,metadata={**public_data.metadata,"schema":"standard_dataset"})
    with pytest.raises(ValueError,match="schema differs"):
        predict_model(tmp_path / "portable",wrong,tmp_path / "wrong.csv")
    manifest_path=tmp_path / "portable/model.json"
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["feature_contract"]["snapshot_source_sha256"]="0"*64
    manifest_path.write_text(json.dumps(manifest),encoding="utf-8")
    with pytest.raises(ValueError,match="snapshot implementation differs"):
        check_model(tmp_path / "portable")
    # Converted inputs are sealed against an unnoticed future-date edit.
    (prepared / "fundamentals.csv").write_text("changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        public_samples(prepared, cfg)


class FakeMarket:
    def __init__(self):
        self.calls = 0

    def login(self):
        from types import SimpleNamespace
        return SimpleNamespace(error_code="0", error_msg="")

    def logout(self):
        pass

    def query_trade_dates(self, start, end):
        return _FrameResult(pd.DataFrame({"calendar_date": ["2024-01-02", "2024-01-03"], "is_trading_day": ["1", "1"]}))

    def query_daily_history_k_AStock(self, date):
        self.calls += 1
        return _FrameResult(pd.DataFrame(dict(date=[date], code=["sh.600000"], open=["10"], close=["10"], preclose=["10"],
                                              volume=["100"], adjustflag=["3"], tradestatus=["1"], isST=["0"])))


def test_full_market_download_resume_uses_verified_cache(tmp_path):
    client = FakeMarket()
    path = tmp_path / "market"
    first = download_market_daily(path, "2024-01-02", "2024-01-03", client=client, delay=0)
    assert first["status"] == "downloaded_raw" and client.calls == 2
    again = download_market_daily(path, "2024-01-02", "2024-01-03", resume=True, client=client, delay=0)
    assert again["sessions_saved"] == 2 and client.calls == 2
    (path / "daily_20240102.csv").write_text("tampered")
    result = download_market_daily(path, "2024-01-02", "2024-01-03", resume=True, client=client, delay=0)
    assert result["status"] == "failed" and "changed" in result["error"]


def test_market_download_stops_after_repeated_provider_failure(tmp_path):
    class BrokenMarket(FakeMarket):
        def query_trade_dates(self, start, end):
            return _FrameResult(pd.DataFrame({"calendar_date": pd.date_range(start, end).strftime("%Y-%m-%d"), "is_trading_day": "1"}))

        def query_daily_history_k_AStock(self, date):
            self.calls += 1
            raise RuntimeError("provider unavailable")

    client = BrokenMarket()
    result = download_market_daily(tmp_path / "market", "2024-01-02", "2024-01-05", client=client, delay=0)
    assert result["status"] == "failed"
    assert client.calls == 3 and len(result["queries"]) == 3
    assert "three consecutive" in result["error"]

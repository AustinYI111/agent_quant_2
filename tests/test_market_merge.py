import hashlib
import json
import pandas as pd
import pytest

from ashare_mvp.market_merge import merge_market_snapshots
from ashare_mvp.market_download import download_market_daily


def write_day(directory, day, close=10):
    name = f"daily_{day.replace('-', '')}.csv"
    frame = pd.DataFrame([dict(date=day, code="sh.600000", open=10, close=close, preclose=10,
                               volume=100, adjustflag="3", tradestatus="1", isST="0")])
    frame.to_csv(directory / name, index=False)
    return name, dict(rows=1, sha256=hashlib.sha256((directory / name).read_bytes()).hexdigest())


def inputs(tmp_path):
    base, repairs = tmp_path / "base", tmp_path / "repairs"
    base.mkdir()
    repairs.mkdir()
    pd.DataFrame({"calendar_date": ["2024-01-02", "2024-01-03"], "is_trading_day": ["1", "1"]}).to_csv(base / "calendar.csv", index=False)
    name, info = write_day(base, "2024-01-02")
    calendar_info = dict(rows=2, sha256=hashlib.sha256((base / "calendar.csv").read_bytes()).hexdigest())
    manifest = dict(schema="market_daily_raw_v1", source="synthetic test", synthetic=True,
                    start="2024-01-02", end="2024-01-03", status="partial",
                    files={name: info, "calendar.csv": calendar_info},
                    queries={"2024-01-02": dict(status="ok"), "2024-01-03": dict(status="failed")})
    raw = json.dumps(manifest).encode()
    (base / "manifest.json").write_bytes(raw)
    (repairs / "input_manifest.json").write_bytes(raw)
    name, info = write_day(repairs, "2024-01-03")
    repair_manifest = dict(schema="market_gap_repairs_v1", source_manifest_sha256=hashlib.sha256(raw).hexdigest(),
                           dates=["2024-01-03"], files={name: info}, queries={"2024-01-03": dict(status="ok")})
    (repairs / "manifest.json").write_text(json.dumps(repair_manifest))
    return base, repairs, tmp_path / "merged"


def test_repairs_make_calendar_continuous_without_certifying_model(tmp_path):
    base, repairs, out = inputs(tmp_path)
    result = merge_market_snapshots(base, [repairs], out)
    assert result["sessions_saved"] == 2 and result["continuous_prefix_sessions"] == 2
    assert result["repair_files_added"] == 1 and result["status"] == "downloaded_raw"
    assert result["model_ready"] is False
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["synthetic"] is True and manifest["read_only_snapshot"] is True
    with pytest.raises(ValueError, match="immutable"):
        download_market_daily(out, "2024-01-02", "2024-01-03", resume=True)


def test_conflicting_daily_sources_are_not_chosen_by_preference(tmp_path):
    base, repairs, out = inputs(tmp_path)
    manifest = json.loads((base / "manifest.json").read_text())
    name, info = write_day(base, "2024-01-03", close=20)
    manifest["files"][name] = info
    manifest["queries"]["2024-01-03"] = dict(status="ok")
    (base / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="conflicting"):
        merge_market_snapshots(base, [repairs], out)
    assert not out.exists()


def test_repair_tamper_does_not_publish_manifest(tmp_path):
    base, repairs, out = inputs(tmp_path)
    (repairs / "daily_20240103.csv").write_text("changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        merge_market_snapshots(base, [repairs], out)
    assert not (out / "manifest.json").exists()


def test_repair_origin_cannot_be_changed(tmp_path):
    base, repairs, out = inputs(tmp_path)
    (repairs / "input_manifest.json").write_text("{}")
    with pytest.raises(ValueError, match="lineage"):
        merge_market_snapshots(base, [repairs], out)
    assert not out.exists()


def test_duplicate_source_still_requires_its_own_cache_hash(tmp_path):
    base, repairs, out = inputs(tmp_path)
    manifest = json.loads((base / "manifest.json").read_text())
    name, info = write_day(base, "2024-01-03")
    manifest["files"][name] = info
    manifest["queries"]["2024-01-03"] = dict(status="ok")
    (base / "manifest.json").write_text(json.dumps(manifest))
    (repairs / name).write_text("changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        merge_market_snapshots(base, [repairs], out)
    assert not out.exists()

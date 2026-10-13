import pandas as pd
import pytest
import json
import hashlib

from ashare_mvp.universe import historical_universe, build_public_universe


def master():
    return pd.DataFrame({"code": ["sh.600000", "sz.000001", "sh.000001"],
                         "ipoDate": ["2000-01-01", "2024-01-03", "1990-01-01"],
                         "outDate": ["2024-01-04", "", ""], "type": ["1", "1", "2"]})


def test_lifetime_universe_keeps_historically_delisted():
    calendar = pd.date_range("2024-01-02", "2024-01-05")
    panel, _ = historical_universe(master(), calendar)
    assert set(panel[panel.date == calendar[0]].symbol) == {"sh.600000"}
    assert set(panel[panel.date == calendar[1]].symbol) == {"sh.600000", "sz.000001"}
    assert set(panel[panel.date == calendar[2]].symbol) == {"sz.000001"}
    assert "sh.000001" not in panel.symbol.values


def test_historical_snapshot_mismatches_are_explicit():
    date = pd.Timestamp("2024-01-03")
    snapshot = pd.DataFrame({"code": ["sz.000001", "sh.600001"]})
    _, audit = historical_universe(master(), [date], {date: snapshot})
    assert set(audit.discrepancy) == {"lifetime_only", "snapshot_only"}
    assert len(audit) == 2


def test_security_master_duplicate_is_rejected():
    data = master()
    with pytest.raises(ValueError, match="unique"):
        historical_universe(pd.concat([data, data.iloc[:1]]), pd.date_range("2024-01-01", periods=2))


def test_inverted_security_lifetime_rejected():
    data = master()
    data.loc[0, "outDate"] = "1999-01-01"
    with pytest.raises(ValueError, match="invalid security lifetime"):
        historical_universe(data, pd.date_range("2024-01-01", periods=2))


def snapshot_directory(path):
    path.mkdir()
    file = path / "universe_20240103.csv"
    pd.DataFrame({"code": ["sh.600000", "sh.000001"], "tradeStatus": ["0", "1"],
                  "code_name": ["bank", "index"]}).to_csv(file, index=False)
    manifest = {"source": "test provider", "files": {file.name: {"sha256": hashlib.sha256(file.read_bytes()).hexdigest()}}}
    (path / "manifest.json").write_text(json.dumps(manifest))
    return file


def test_public_snapshots_preserve_suspensions_without_inventing_dates(tmp_path):
    source = tmp_path / "source"
    snapshot_directory(source)
    result = build_public_universe(source, tmp_path / "out")
    panel = pd.read_csv(tmp_path / "out" / "universe_panel.csv")
    assert len(panel) == 1 and panel.trading_status.iloc[0] == 0
    assert result["dates"] == ["2024-01-03"]
    assert not result["daily_coverage"] and not result["complete_a_share_market"]


def test_public_snapshot_hash_mismatch_rejected(tmp_path):
    source = tmp_path / "source"
    file = snapshot_directory(source)
    file.write_text("tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        build_public_universe(source, tmp_path / "out")

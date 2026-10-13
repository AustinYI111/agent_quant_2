import hashlib
import json

import pandas as pd
import pytest

from ashare_mvp.universe_audit import audit_market_universe


def fixture(tmp_path):
    master, market = tmp_path / "master", tmp_path / "market"
    master.mkdir(); market.mkdir()
    pd.DataFrame([dict(code="sh.600000", ipoDate="2000-01-01", outDate="2022-01-05", type="1", status="0")]).to_csv(master / "security_master.csv", index=False)
    (master / "manifest.json").write_text(json.dumps(dict(schema="requested_security_master_v1", requested_securities=2,
        master_sha256=hashlib.sha256((master / "security_master.csv").read_bytes()).hexdigest())))
    pd.DataFrame(dict(calendar_date=["2022-01-03", "2022-01-04", "2022-01-05"], is_trading_day=[1,1,1])).to_csv(market / "calendar.csv", index=False)
    pd.DataFrame(dict(code=["sh.600000", "sz.000001"], date=["2022-01-03"]*2)).to_csv(market / "daily_20220103.csv", index=False)
    pd.DataFrame(dict(code=["sh.600000"], date=["2022-01-05"])).to_csv(market / "daily_20220105.csv", index=False)
    files={p.name: dict(rows=len(pd.read_csv(p)), sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in market.glob("*.csv")}
    (market / "manifest.json").write_text(json.dumps(dict(files=files)))
    return master, market


def test_historical_status_and_terminal_date_and_unattempted_sessions(tmp_path):
    master, market = fixture(tmp_path)
    result = audit_market_universe(master, market, tmp_path / "out")
    coverage = pd.read_csv(tmp_path / "out/session_coverage.csv")
    assert coverage.expected.tolist() == [1,1,0]  # current status=0 must not remove historical listing
    assert coverage.status.tolist() == ["observed", "missing_session", "observed"]
    discrepancies = pd.read_csv(tmp_path / "out/discrepancies.csv")
    assert discrepancies.discrepancy.tolist() == ["unknown_master", "outside_lifetime"]
    assert result["sessions_observed"] == 2 and not result["complete_a_share_market"]


@pytest.mark.parametrize("which", ["master", "market"])
def test_input_mutation_rejected(tmp_path, which):
    master, market = fixture(tmp_path)
    path = master / "security_master.csv" if which == "master" else market / "daily_20220103.csv"
    path.write_text("changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        audit_market_universe(master, market, tmp_path / "out")

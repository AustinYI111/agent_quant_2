import pandas as pd
import pytest

from ashare_mvp.public_data import _FrameResult
from ashare_mvp.security_master import download_security_master


class Client:
    def __init__(self, conflict=False):
        self.calls = []
        self.conflict = conflict

    def logout(self):
        pass

    def query_stock_basic(self, **kwargs):
        self.calls.append(kwargs)
        code = kwargs.get("code") or "sh.600000"
        if kwargs.get("code_name") == "A":
            rows = []
        else:
            rows = [dict(code=code, code_name="浦发", ipoDate="1999-11-10", outDate="", type="1", status="1")]
            if self.conflict and kwargs.get("code_name") == "B":
                rows[0]["ipoDate"] = "2000-01-01"
        return _FrameResult(pd.DataFrame(rows, columns=["code", "code_name", "ipoDate", "outDate", "type", "status"]))


def test_complete_name_group_then_exact_missing_code_fallback(tmp_path):
    targets = tmp_path / "targets.csv"
    pd.DataFrame({"code": ["sh.600000", "sz.000001"], "code_name": ["浦发", "A银行"]}).to_csv(targets, index=False)
    client = Client()
    result = download_security_master(targets, tmp_path / "out", client=client, delay=0)
    assert result["status"] == "downloaded_raw" and result["resolved_securities"] == 2
    assert dict(code="sz.000001") in client.calls
    before = len(client.calls)
    download_security_master(targets, tmp_path / "out", resume=True, client=client, delay=0)
    assert len(client.calls) == before
    assert pd.read_csv(tmp_path / "out/security_master.csv").ipoDate.tolist() == ["1999-11-10"] * 2


def test_lifetime_conflict_not_resolved_by_source_preference(tmp_path):
    targets = tmp_path / "targets.csv"
    pd.DataFrame({"code": ["sh.600000", "sz.000001"], "code_name": ["浦发", "B银行"]}).to_csv(targets, index=False)
    result = download_security_master(targets, tmp_path / "out", client=Client(conflict=True), delay=0)
    coverage = pd.read_csv(tmp_path / "out/coverage.csv").set_index("code")
    assert result["status"] == "partial" and coverage.loc["sh.600000", "status"] == "unresolved"
    assert "conflicting" in coverage.loc["sh.600000", "reason"]


def test_cached_master_query_tamper_rejected(tmp_path):
    targets = tmp_path / "targets.csv"
    pd.DataFrame({"code": ["sh.600000"], "code_name": ["浦发"]}).to_csv(targets, index=False)
    client = Client()
    download_security_master(targets, tmp_path / "out", client=client, delay=0)
    file = next((tmp_path / "out").glob("query_*.csv"))
    file.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        download_security_master(targets, tmp_path / "out", resume=True, client=client, delay=0)

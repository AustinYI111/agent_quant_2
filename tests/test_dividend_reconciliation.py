import hashlib
import json
import pandas as pd

from ashare_mvp.dividend_reconciliation import reconcile_dividend_sources


def inputs(tmp_path, payment_cash=0.1):
    market, payments = tmp_path / "market", tmp_path / "payments"
    directory = market / "2017Q4"
    directory.mkdir(parents=True)
    payments.mkdir()
    records = []
    for security, progress in [("600000.SH", "实施分配"), ("600001.SH", "实施分配"), ("600002.SH", "取消分配")]:
        records.append(dict(SECUCODE=security, REPORT_DATE="2017-12-31", EX_DIVIDEND_DATE="2018-07-13",
                            NOTICE_DATE="2018-07-06", ASSIGN_PROGRESS=progress, IMPL_PLAN_PROFILE="10派1元",
                            PRETAX_BONUS_RMB=1.0, BONUS_RATIO=0, IT_RATIO=0))
    pd.DataFrame(records).to_csv(directory / "records.csv", index=False)
    digest = hashlib.sha256((directory / "records.csv").read_bytes()).hexdigest()
    manifest = dict(schema="market_dividend_plans_v1", status="downloaded_raw",
                    periods={"2017-12-31": dict(status="ok", rows=3, hashes={"records.csv": digest})})
    (market / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    pd.DataFrame([dict(symbol="sh.600000", ex_date="2018-07-13", available_date="2018-07-09",
                       announcement_date="2018-07-06", pay_date="2018-07-16",
                       cash_per_share=payment_cash, share_multiplier=1)]).to_csv(payments / "actions.csv", index=False)
    digest = hashlib.sha256((payments / "actions.csv").read_bytes()).hexdigest()
    (payments / "summary.json").write_text(json.dumps(dict(actions_sha256=digest)), encoding="utf-8")
    calendar = tmp_path / "calendar.csv"
    pd.DataFrame({"date": pd.bdate_range("2018-07-02", "2018-07-20")}).to_csv(calendar, index=False)
    return market, payments, calendar, tmp_path / "out"


def test_only_agreeing_implemented_events_receive_real_payment_date(tmp_path):
    args = inputs(tmp_path)
    result = reconcile_dividend_sources(*args)
    assert result["status_counts"] == {"matched_payment_evidence": 1, "missing_payment_evidence": 1, "not_implemented": 1}
    assert result["model_ready"] is False
    actions = pd.read_csv(args[3] / "actions.csv")
    assert len(actions) == 1 and actions.pay_date.iloc[0] == "2018-07-16"
    assert actions.available_date.iloc[0] == "2018-07-09"
    assert actions.cash_per_share.iloc[0] == 0.1


def test_conflicting_terms_are_not_exported_as_cash_event(tmp_path):
    args = inputs(tmp_path, payment_cash=0.2)
    result = reconcile_dividend_sources(*args)
    assert result["matched_events"] == 0 and result["status_counts"]["unresolved"] == 1
    assert pd.read_csv(args[3] / "actions.csv").empty


def test_payment_hash_tamper_fails_before_any_export(tmp_path):
    import pytest
    args = inputs(tmp_path)
    (args[1] / "actions.csv").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        reconcile_dividend_sources(*args)
    assert not args[3].exists()

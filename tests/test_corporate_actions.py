import pandas as pd
import pytest

from ashare_mvp.corporate_actions import economic_return_prices
from ashare_mvp.corporate_actions import reconcile_price_files


def inputs():
    dates = pd.bdate_range("2024-01-02", periods=4)
    prices = pd.DataFrame({"date": dates, "symbol": "sh.600000", "open": [10, 9.5, 9.5, 9.5],
                           "close": [10, 9.5, 9.5, 9.5], "preclose": [10, 9.5, 9.5, 9.5], "tradestatus": "1"})
    actions = pd.DataFrame({"symbol": ["sh.600000"], "ex_date": [dates[1]], "available_date": [dates[0]],
                            "pay_date": [dates[2]], "cash_per_share": [0.5], "share_multiplier": [1.0]})
    return prices, actions, dates


def test_dividend_receivable_preserves_wealth_and_reinvests_after_payment():
    p, a, dates = inputs()
    result = economic_return_prices(p, a, dates)
    assert result.total_return_open.tolist() == pytest.approx([10, 10, 10, 10])
    assert result.dividend_receivable.tolist() == [0, 0.5, 0.5, 0]
    assert result.economic_shares.iloc[2] == 1
    assert result.economic_shares.iloc[3] == pytest.approx(1 + 0.5 / 9.5)


def test_stock_split_is_not_a_loss():
    p, a, dates = inputs()
    p.loc[1:, ["open", "close", "preclose"]] = 5
    a["cash_per_share"] = 0
    a["share_multiplier"] = 2
    result = economic_return_prices(p, a, dates)
    assert result.total_return_open.tolist() == [10] * 4


def test_differential_cash_is_distinct_from_exchange_reference_deduction():
    p, a, dates = inputs()
    p.loc[1:, ["open", "close", "preclose"]] = 9.72
    a["cash_per_share"] = 0.321
    a["reference_cash_per_share"] = 0.28
    result = economic_return_prices(p, a, dates)
    assert result.total_return_open.iloc[1] == pytest.approx(10.041)
    assert result.dividend_receivable.iloc[1] == pytest.approx(0.321)
    with pytest.raises(ValueError, match="unreconciled"):
        economic_return_prices(p, a.drop(columns="reference_cash_per_share"), dates)


def test_missing_corporate_event_not_silently_treated_as_loss():
    p, a, dates = inputs()
    with pytest.raises(ValueError, match="unreconciled"):
        economic_return_prices(p, a.iloc[:0], dates)


def test_future_action_record_does_not_change_past_index():
    p, a, dates = inputs()
    before = economic_return_prices(p, a, dates)
    changed = p.copy()
    changed.loc[3, ["open", "close"]] = 20
    after = economic_return_prices(changed, a, dates)
    pd.testing.assert_frame_equal(before.iloc[:3], after.iloc[:3])


def test_action_archive_outside_evaluation_interval_does_not_change_index():
    p, a, dates = inputs()
    before = economic_return_prices(p, a, dates)
    future = a.copy()
    future["ex_date"] = pd.Timestamp("2025-01-02")
    future["pay_date"] = pd.Timestamp("2025-01-02")
    future["available_date"] = pd.Timestamp("2024-12-31")
    after = economic_return_prices(p, pd.concat([a, future]), dates)
    pd.testing.assert_frame_equal(before, after)


def test_incomplete_security_history_rejected():
    p, a, dates = inputs()
    with pytest.raises(ValueError, match="terminal valuation"):
        economic_return_prices(p.iloc[:-1], a, dates)


def test_late_disclosure_and_rights_capital_rejected():
    p, a, dates = inputs()
    a.loc[0, "available_date"] = dates[2]
    with pytest.raises(ValueError, match="not known"):
        economic_return_prices(p, a, dates)
    a.loc[0, "available_date"] = dates[0]
    a["rights_cash_required"] = 1
    with pytest.raises(ValueError, match="external capital"):
        economic_return_prices(p, a, dates)


def test_file_reconciliation_seals_evidence_and_rejects_truncated_history(tmp_path):
    import hashlib
    p, a, dates = inputs()
    p.rename(columns={"symbol": "code"}).to_csv(tmp_path / "prices.csv", index=False)
    a.to_csv(tmp_path / "actions.csv", index=False)
    pd.DataFrame({"calendar_date": dates, "is_trading_day": "1"}).to_csv(tmp_path / "calendar.csv", index=False)
    args = [tmp_path / "prices.csv", tmp_path / "actions.csv", tmp_path / "calendar.csv"]
    result = reconcile_price_files(*args, tmp_path / "out")
    assert result["rows"] == 4 and result["certified"] is False and result["model_ready"] is False
    assert result["input_sha256"]["prices"] == hashlib.sha256(args[0].read_bytes()).hexdigest()
    pd.testing.assert_series_equal(pd.read_csv(tmp_path / "out/economic_prices.csv").total_return_open,
                                   pd.Series([10.] * 4, name="total_return_open"))
    p.iloc[:-1].to_csv(args[0], index=False)
    with pytest.raises(ValueError, match="terminal valuation"):
        reconcile_price_files(*args, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()

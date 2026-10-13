import pandas as pd
import pytest

from ashare_mvp.corporate_actions import economic_return_prices


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

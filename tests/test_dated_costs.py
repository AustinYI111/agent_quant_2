from types import SimpleNamespace
import pandas as pd
import pytest

from ashare_mvp.costs import stamp_duty_rate
from ashare_mvp.model import Config
from ashare_mvp.pipeline import simulate_monthly, cohort_performance, forward_labels


def prices(dates, symbols=("A",)):
    # Explicit synthetic flat-price observations isolate execution costs.
    return pd.DataFrame([dict(date=date, symbol=symbol, total_return_open=10., close=10.,
                              can_buy=True, can_sell=True) for date in dates for symbol in symbols])


def test_statutory_boundary_and_explicit_research_override():
    assert stamp_duty_rate("2023-08-25") == .001
    assert stamp_duty_rate("2023-08-28") == .0005
    assert stamp_duty_rate("2023-08-28", 50) == .005
    with pytest.raises(ValueError, match="2008-09-19"):
        stamp_duty_rate("2007-01-01")


def test_delayed_sale_uses_actual_fill_date_not_blocked_order_date():
    dates = pd.to_datetime(["2023-08-23", "2023-08-24", "2023-08-25", "2023-08-28"])
    frame = prices(dates, ("A", "B"))
    frame.loc[(frame.date == dates[2]) & (frame.symbol == "A"), "can_sell"] = False
    data = SimpleNamespace(prices=frame, calendar=pd.DatetimeIndex(dates))
    picks = pd.DataFrame([dict(date=dates[0], symbol="A", target_weight=1.),
                          dict(date=dates[1], symbol="B", target_weight=1.),
                          dict(date=dates[2], symbol="B", target_weight=1.)])
    nav, trades = simulate_monthly(data, picks, Config(commission_bps=0, slippage_bps=0))
    sells = trades[trades.side == "sell"]
    blocked = sells[sells.status == "blocked"].iloc[0]
    filled = sells[sells.status == "filled"].iloc[0]
    assert blocked.stamp_duty == 0 and blocked.cost == 0
    assert filled.date == pd.Timestamp("2023-08-28") and filled.stamp_duty_bps == 5
    assert filled.stamp_duty == pytest.approx(.0005)
    assert nav.nav.iloc[-1] == pytest.approx(.9995)


def test_cohort_horizons_apply_their_own_exit_date_rate():
    dates = pd.to_datetime(["2023-03-31", "2023-04-03", "2023-07-03", "2023-10-09"])
    data = SimpleNamespace(prices=prices(dates), calendar=pd.DatetimeIndex(dates))
    selected = pd.DataFrame([dict(date=dates[0], symbol="A")])
    labels = forward_labels(data, [dates[0]])
    result = cohort_performance(selected, labels, selected, data,
                                Config(top_k=1, commission_bps=0, slippage_bps=0)).set_index("horizon_months")
    assert result.loc[3, "sell_tax_bps"] == 10 and result.loc[6, "sell_tax_bps"] == 5
    assert result.loc[3, "indicative_net_return"] == pytest.approx(-.001)
    assert result.loc[6, "indicative_net_return"] == pytest.approx(-.0005)
    assert not result.loc[12, "complete"] and pd.isna(result.loc[12, "sell_tax_bps"])

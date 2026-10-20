import pandas as pd
from ashare_mvp.price_action_audit import audit_price_actions


def inputs():
    dates=pd.bdate_range("2024-01-02",periods=4)
    prices=pd.DataFrame(dict(date=dates,symbol="sh.600000",close=[10,9,9,9],preclose=[10,9,9,9]))
    actions=pd.DataFrame(dict(symbol=["sh.600000"],ex_date=[dates[1]],cash_per_share=[1.],share_multiplier=[1.]))
    return prices,actions,dates


def test_dividend_gap_detected_and_matched_event_explains_quote():
    prices,actions,dates=inputs()
    gaps,summary=audit_price_actions(prices,actions.iloc[:0],dates)
    assert summary["issue_counts"]=={"unreconciled_adjustment":1}
    assert gaps.date.iloc[0]==dates[1]
    gaps,summary=audit_price_actions(prices,actions,dates)
    assert gaps.empty and summary["model_ready"] is False


def test_terminal_missing_quotes_are_reported_without_removing_security():
    prices,actions,dates=inputs()
    gaps,summary=audit_price_actions(prices.iloc[:-1],actions,dates)
    assert summary["symbols"]==1 and summary["affected_symbols"]==1
    assert gaps.iloc[0].issue=="missing_sessions_or_terminal_valuation"
    assert gaps.iloc[0].date==dates[-1]


def test_missing_session_does_not_create_false_corporate_action_gap():
    prices,actions,dates=inputs()
    prices.loc[2,"close"]=8
    prices.loc[3,"preclose"]=8
    gaps,summary=audit_price_actions(prices.drop(index=2),actions,dates)
    assert summary["issue_counts"]=={"missing_sessions_or_terminal_valuation":1}
    assert gaps.date.iloc[0]==dates[2]

import pandas as pd
import pytest
from ashare_mvp.balance_sheet import normalize_balance_records


def record():
    return dict(SECURITY_CODE="600519", REPORT_DATE="2019-12-31", NOTICE_DATE="2020-04-22", UPDATE_DATE="2021-03-31", TOTAL_ASSETS=100, TOTAL_LIABILITIES=140)


def test_restatement_not_backfilled_and_insolvent_ratio_preserved():
    result=normalize_balance_records([record()], "sh.600519", pd.bdate_range("2020-01-01", "2021-04-02"))
    assert result.available_date.iloc[0] == pd.Timestamp("2021-04-01")
    assert result.debt_ratio.iloc[0] == 1.4
    early=normalize_balance_records([record()], "sh.600519", pd.bdate_range("2020-01-01", "2020-12-31"))
    assert pd.isna(early.available_date.iloc[0])


def test_missing_debt_not_zero_and_wrong_identity_rejected():
    row=record(); row['TOTAL_LIABILITIES']=None
    result=normalize_balance_records([row], "sh.600519", pd.bdate_range("2020-01-01", "2021-04-02"))
    assert pd.isna(result.debt_ratio.iloc[0])
    with pytest.raises(ValueError,match="identity"):
        normalize_balance_records([row], "sz.000001", pd.bdate_range("2020-01-01", "2021-04-02"))

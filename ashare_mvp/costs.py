"""Dated A-share sale stamp duty; commissions/slippage remain assumptions."""
import math
import pandas as pd


def stamp_duty_rate(date, override_bps=None):
    if override_bps is not None:
        if not math.isfinite(override_bps) or not 0 <= override_bps < 10000:
            raise ValueError("invalid stamp duty override")
        return override_bps / 10000
    date = pd.Timestamp(date)
    if pd.isna(date) or date.tz is not None:
        raise ValueError("stamp duty requires a valid timezone-free trade date")
    if date < pd.Timestamp("2008-09-19"):
        raise ValueError("automatic stamp duty supports 2008-09-19 onward; supply an explicit research cost override")
    return 0.001 if date < pd.Timestamp("2023-08-28") else 0.0005


def stamp_duty_policy(override_bps=None):
    if override_bps is not None:
        return dict(mode="constant_research_override", basis_points=override_bps)
    return dict(mode="dated_A_share_sell_stamp_duty", schedule=[
        dict(effective_date="2008-09-19", basis_points=10,
             source="https://www.mof.gov.cn/zhengwuxinxi/caizhengxinwen/200809/t20080919_76432.htm"),
        dict(effective_date="2023-08-28", basis_points=5,
             source="https://m.mof.gov.cn/czxw/202308/t20230827_3904226.htm")])

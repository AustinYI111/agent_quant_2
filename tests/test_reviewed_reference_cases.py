"""Regression against independently reviewed notices and cached raw daily bars."""
from pathlib import Path
import pandas as pd
import pytest
from ashare_mvp.reference_overrides import load_reference_evidence,apply_reference_overrides
from ashare_mvp.price_action_audit import audit_price_actions


@pytest.mark.parametrize('symbol,ex,ann,available,before,preclose,actual',[
    ('sh.600019','2021-10-15','2021-10-11','2021-10-12',8.04,7.70,.35),
    ('sh.600114','2019-06-06','2019-05-30','2019-05-31',6.83,6.44,.4),
])
def test_notice_reference_fixes_raw_quote_without_rewriting_cash(symbol,ex,ann,available,before,preclose,actual):
    evidence=load_reference_evidence(Path(__file__).parents[1]/'config/reviewed_reference_cash.json')
    evidence=evidence[evidence.symbol.eq(symbol)&evidence.ex_date.eq(ex)]
    assert len(evidence)==1
    cal=pd.bdate_range(ann,ex)
    prices=pd.DataFrame({'symbol':symbol,'date':cal,
        'close':[before]*(len(cal)-1)+[preclose],
        'preclose':[before]*(len(cal)-1)+[preclose]})
    actions=pd.DataFrame([dict(symbol=symbol,ex_date=ex,available_date=available,pay_date=ex,cash_per_share=actual,share_multiplier=1.)])
    gaps,_=audit_price_actions(prices,actions,cal,reference_tick_size=.01)
    assert len(gaps)==1 and gaps.iloc[0].issue=='unreconciled_adjustment'
    corrected=apply_reference_overrides(actions,evidence,cal)
    gaps,_=audit_price_actions(prices,corrected,cal,reference_tick_size=.01)
    assert gaps.empty
    pd.testing.assert_series_equal(corrected.cash_per_share,actions.cash_per_share)
    pd.testing.assert_series_equal(corrected.share_multiplier,actions.share_multiplier)

import pandas as pd
import pytest
from ashare_mvp.lifetime_coverage import audit_lifetime_coverage


def test_delisting_is_a_valuation_gap_not_a_missing_live_quote():
    cal=pd.bdate_range('2020-01-01',periods=5)
    prices=pd.DataFrame({'symbol':['A']*3,'date':cal[:3]})
    lives=pd.DataFrame([dict(symbol='A',listing_date=cal[0],terminal_date=cal[2])])
    original=prices.copy(deep=True)
    rows, summary=audit_lifetime_coverage(prices,lives,cal)
    assert summary['missing_lifetime_quotes']==0
    assert summary['unresolved_post_terminal_valuations']==2
    assert summary['terminal_affected_issuers']==1
    assert not summary['terminal_settlement_verified']
    assert not rows.iloc[0].terminal_settlement_verified
    pd.testing.assert_frame_equal(prices,original)


def test_live_hole_is_distinct_from_later_missing_terminal_values():
    cal=pd.bdate_range('2020-01-01',periods=6)
    prices=pd.DataFrame({'symbol':['A']*3,'date':cal[[0,2,3]]})
    lives=pd.DataFrame([dict(symbol='A',listing_date=cal[0],terminal_date=cal[3])])
    rows, summary=audit_lifetime_coverage(prices,lives,cal)
    assert summary['missing_lifetime_quotes']==1
    assert summary['unresolved_post_terminal_valuations']==2
    assert rows.iloc[0].first_missing_lifetime_quote==cal[1]
    assert rows.iloc[0].first_unresolved_terminal_valuation==cal[4]


def test_same_issuer_code_change_does_not_end_open_successor():
    cal=pd.bdate_range('2020-01-01',periods=5)
    prices=pd.DataFrame({'symbol':['A']*4,'date':cal[:4]})
    lives=pd.DataFrame([dict(symbol='A',listing_date=cal[0],terminal_date=cal[2]),
                        dict(symbol='A',listing_date=cal[2],terminal_date=pd.NaT)])
    _, summary=audit_lifetime_coverage(prices,lives,cal)
    assert summary['missing_lifetime_quotes']==1
    assert summary['unresolved_post_terminal_valuations']==0


@pytest.mark.parametrize('fault',['no_master','duplicate_quote','bad_lifetime','unordered_calendar'])
def test_ambiguous_lifetime_diagnostics_fail_closed(fault):
    cal=pd.bdate_range('2020-01-01',periods=3)
    prices=pd.DataFrame({'symbol':['A']*3,'date':cal})
    lives=pd.DataFrame([dict(symbol='A',listing_date=cal[0],terminal_date=pd.NaT)])
    if fault=='no_master':lives['symbol']='B'
    if fault=='duplicate_quote':prices=pd.concat([prices,prices.iloc[:1]],ignore_index=True)
    if fault=='bad_lifetime':lives['terminal_date']=cal[0]-pd.Timedelta(days=1)
    if fault=='unordered_calendar':cal=cal[::-1]
    with pytest.raises(ValueError):audit_lifetime_coverage(prices,lives,cal)

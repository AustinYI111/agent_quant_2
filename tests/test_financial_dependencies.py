import pandas as pd
import pytest
from ashare_mvp.financial_dependencies import combine_financial_dependencies
from ashare_mvp.market_alignment import financial_events


def test_late_dependency_and_late_older_period_never_backfilled():
    income=pd.DataFrame({'symbol':['S']*3,'report_date':['2019-12-31','2019-12-31','2020-03-31'],
                         'available_date':['2020-04-30','2020-06-10','2020-08-01'],'roe':[.1,.2,.3]})
    debt=pd.DataFrame({'symbol':['S']*2,'report_date':['2019-12-31']*2,
                       'available_date':['2020-05-10','2020-09-01'],'debt_ratio':[.4,.5]})
    result=combine_financial_dependencies(income,debt)
    old=result[result.report_date.eq(pd.Timestamp('2019-12-31'))]
    assert old.available_date.dt.strftime('%Y-%m-%d').tolist()==['2020-04-30','2020-05-10','2020-06-10','2020-09-01']
    assert pd.isna(old.debt_ratio.iloc[0]) and old.debt_ratio.iloc[1]==.4
    assert old.roe.tolist()==[.1,.1,.2,.2]
    latest=financial_events(result)
    assert latest.report_date.iloc[-1]==pd.Timestamp('2020-03-31')
    assert pd.isna(latest.debt_ratio.iloc[-1])  # old debt cannot fill a new report period


def test_balance_only_events_never_create_earnings_and_duplicate_versions_rejected():
    income=pd.DataFrame({'symbol':['S'],'report_date':['2019-12-31'],'available_date':['2020-06-01'],'roe':[.1]})
    debt=pd.DataFrame({'symbol':['S'],'report_date':['2019-12-31'],'available_date':['2020-05-01'],'debt_ratio':[.4]})
    assert len(combine_financial_dependencies(income,debt))==1
    with pytest.raises(ValueError,match='ambiguous'):
        combine_financial_dependencies(pd.concat([income,income]),debt)


def test_out_of_scope_provider_records_not_used_as_visible_versions():
    income=pd.DataFrame({'symbol':['S',None,'S','S'],'report_date':['2019-12-31']*4,
                         'available_date':['2020-06-01','2020-05-01',None,None],'roe':[.1,.9,.8,.7]})
    debt=pd.DataFrame({'symbol':['S'],'report_date':['2019-12-31'],'available_date':['2020-05-01'],'debt_ratio':[.4]})
    result=combine_financial_dependencies(income,debt)
    assert len(result)==1 and result.roe.iloc[0]==.1

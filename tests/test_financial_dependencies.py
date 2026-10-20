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


def aliased_inputs():
    income=pd.DataFrame(dict(symbol=['sz.302132']*2,report_date=['2019-12-31','2020-03-31'],available_date=['2020-04-30','2020-08-01'],roe=[.1,.3]))
    debt=pd.DataFrame(dict(symbol=['sz.300114']*2,report_date=['2019-12-31']*2,available_date=['2020-05-10','2020-09-01'],debt_ratio=[.4,.5]))
    return income,debt


def test_same_issuer_financial_join_preserves_source_symbols_and_late_clocks():
    from test_dividend_identity import changes
    income,debt=aliased_inputs()
    assert combine_financial_dependencies(income,debt).debt_ratio.isna().all()
    result=combine_financial_dependencies(income,debt,changes())
    assert result.symbol.eq('sz.300114').all() and result.earnings_provider_symbol.eq('sz.302132').all()
    before=result[result.available_date.eq(pd.Timestamp('2020-04-30'))].iloc[0]
    assert pd.isna(before.debt_ratio) and pd.isna(before.balance_provider_symbol)
    known=result[result.available_date.eq(pd.Timestamp('2020-05-10'))].iloc[0]
    assert known.debt_ratio==.4 and known.balance_provider_symbol=='sz.300114'
    assert known.balance_available_date==known.available_date
    latest=financial_events(result)
    assert latest.report_date.iloc[-1]==pd.Timestamp('2020-03-31') and pd.isna(latest.debt_ratio.iloc[-1])
    assert result.earnings_available_date.le(result.available_date).all()
    known=result.balance_available_date.notna();assert result.loc[known,'balance_available_date'].le(result.loc[known,'available_date']).all()


@pytest.mark.parametrize('source',['income','debt'])
def test_old_new_financial_duplicate_versions_are_not_silently_deduplicated(source):
    from test_dividend_identity import changes
    income,debt=aliased_inputs()
    if source=='income':
        duplicate=income.iloc[:1].copy();duplicate['symbol']='sz.300114';duplicate['roe']=.9;income=pd.concat([income,duplicate],ignore_index=True)
    else:
        duplicate=debt.iloc[:1].copy();duplicate['symbol']='sz.302132';duplicate['debt_ratio']=.9;debt=pd.concat([debt,duplicate],ignore_index=True)
    with pytest.raises(ValueError,match='ambiguous financial dependency version'):
        combine_financial_dependencies(income,debt,changes())


def test_financial_files_seal_identity_policy_and_preserve_original_inputs(tmp_path):
    import hashlib,json
    from test_dividend_identity import changes
    from ashare_mvp.financial_dependencies import combine_financial_files
    income,debt=aliased_inputs();paths=[];original=[]
    for name,filename,frame in [('earnings','financial_panel.csv',income),('balances','balance_panel.csv',debt)]:
        path=tmp_path/name;path.mkdir();frame.to_csv(path/filename,index=False);raw=(path/filename).read_bytes();original.append(raw)
        (path/'summary.json').write_text(json.dumps({'panel_sha256':hashlib.sha256(raw).hexdigest(),'partial_source':True}));paths.append(path)
    result=combine_financial_files(*paths,tmp_path/'out',changes())
    assert result['code_changes_sha256']==hashlib.sha256((tmp_path/'out/input_code_changes.json').read_bytes()).hexdigest()
    assert result['partial_source'] and not result['model_ready'] and result['debt_records']==2
    for path,filename,raw in zip(paths,['financial_panel.csv','balance_panel.csv'],original):assert (path/filename).read_bytes()==raw


def test_equal_cross_alias_versions_are_coalesced_with_both_sources_and_audit():
    import json
    from test_dividend_identity import changes
    income,debt=aliased_inputs();duplicate=debt.iloc[:1].copy();duplicate['symbol']='sz.302132'
    result=combine_financial_dependencies(income,pd.concat([debt,duplicate],ignore_index=True),changes())
    known=result[result.available_date.eq(pd.Timestamp('2020-05-10'))].iloc[0]
    assert known.debt_ratio==.4 and pd.isna(known.balance_provider_symbol)
    assert json.loads(known.balance_provider_symbols)==['sz.300114','sz.302132']
    audit=result.attrs['identity_alias_coalescing']
    assert len(audit)==1 and audit[0]['duplicate_rows_removed']==1 and audit[0]['dependency']=='balance'


def test_equal_values_with_different_publication_clocks_remain_a_conflict():
    from test_dividend_identity import changes
    income,debt=aliased_inputs();debt['publication_date']='2020-04-29'
    duplicate=debt.iloc[:1].copy();duplicate['symbol']='sz.302132';duplicate['publication_date']='2020-04-30'
    with pytest.raises(ValueError,match='ambiguous financial dependency version'):
        combine_financial_dependencies(income,pd.concat([debt,duplicate],ignore_index=True),changes())

from copy import deepcopy
import pandas as pd
import pytest
from ashare_mvp.reviewed_payment_groups import reconcile_reviewed_groups


def inputs():
    cal=pd.bdate_range('2024-06-03','2024-06-14')
    raw=pd.DataFrame([dict(SECUCODE='601966.SH',source_period=period,EX_DIVIDEND_DATE='2024-06-14',
        ASSIGN_PROGRESS='实施分配',PRETAX_BONUS_RMB=cash*10,BONUS_RATIO=0.,IT_RATIO=0.,IMPL_PLAN_PROFILE=description)
        for period,cash,description in [('2023-12-31',.286,'10派2.86元'),('2024-03-31',.091,'10派0.91元')]])
    coverage=pd.DataFrame([dict(security='601966.SH',source_period=row.source_period,ex_date='2024-06-14 00:00:00',
        status='matched_payment_evidence' if index==0 else 'missing_payment_evidence',cash_per_share=row.PRETAX_BONUS_RMB/10,
        share_multiplier=1.,available_date='2024-06-11') for index,row in raw.iterrows()])
    actions=pd.DataFrame([dict(symbol='sh.601966',source_period='2023-12-31',ex_date='2024-06-14',available_date='2024-06-11',
        pay_date='2024-06-14',cash_per_share=.286,share_multiplier=1.)])
    group=dict(symbol='sh.601966',ex_date='2024-06-14',announcement_date='2024-06-07',available_date='2024-06-11',pay_date='2024-06-14',
        cash_per_share=.377,reference_cash_per_share=.37444,share_multiplier=1.,source_url='https://example.org/official.pdf',source_sha256='a'*64,
        source_page=1,payment_source_page=1,reference_source_page=2,reviewed=True,
        expected_existing=dict(cash_per_share=.286,source_period='2023-12-31',pay_date='2024-06-14'),
        components=[dict(source_period=row.source_period,expected_cash_per_share=row.PRETAX_BONUS_RMB/10,
            expected_description=row.IMPL_PLAN_PROFILE,expected_status=coverage.iloc[index].status) for index,row in raw.iterrows()])
    return actions,coverage,raw,[group],cal


def test_notice_combines_two_periods_once_and_replaces_anchored_partial_cash():
    a,c,raw,e,cal=inputs();original=a.copy(deep=True)
    result,coverage,audit=reconcile_reviewed_groups(a,c,raw,e,cal)
    assert len(result)==1 and result.cash_per_share.iloc[0]==.377
    assert result.reference_cash_per_share.iloc[0]==.37444
    assert result.share_multiplier.iloc[0]==1.
    assert result.source_periods_json.iloc[0]=='["2023-12-31", "2024-03-31"]'
    assert coverage.status.eq('matched_reviewed_notice').all()
    assert coverage.cash_per_share.tolist()==pytest.approx([.286,.091])
    assert audit.previous_cash_per_share.iloc[0]==.286
    pd.testing.assert_frame_equal(a,original)
    with pytest.raises(ValueError):reconcile_reviewed_groups(result,coverage,raw,e,cal)


def test_all_missing_components_create_one_combined_event():
    a,c,raw,e,cal=inputs();a=a.iloc[:0];c['status']='missing_payment_evidence'
    e[0]['expected_existing']=None
    for component in e[0]['components']:component['expected_status']='missing_payment_evidence'
    result,_,_=reconcile_reviewed_groups(a,c,raw,e,cal)
    assert len(result)==1 and result.cash_per_share.iloc[0]==.377


def test_unrelated_existing_event_and_coverage_remain_unchanged():
    a,c,raw,e,cal=inputs();extra=a.iloc[:1].copy();extra['symbol']='sh.600000'
    other=c.iloc[:1].copy();other['security']='600000.SH'
    result,coverage,_=reconcile_reviewed_groups(pd.concat([a,extra],ignore_index=True),pd.concat([c,other],ignore_index=True),raw,e,cal)
    pd.testing.assert_frame_equal(result[result.symbol.eq('sh.600000')][extra.columns].reset_index(drop=True),extra.reset_index(drop=True),check_dtype=False)
    pd.testing.assert_frame_equal(coverage[coverage.security.eq('600000.SH')][other.columns].reset_index(drop=True),other.reset_index(drop=True),check_dtype=False)


@pytest.mark.parametrize('fault',['omit_period','wrong_total','wrong_coverage_cash','wrong_description','unreviewed',
    'late_notice','late_available','wrong_existing_cash','unexpected_existing','missing_existing','bad_reference_page','bad_payment_page','duplicate_group'])
def test_reviewed_groups_reject_incomplete_or_conflicting_anchors(fault):
    a,c,raw,e,cal=inputs();g=e[0]
    if fault=='omit_period':g['components']=g['components'][:1]
    if fault=='wrong_total':g['cash_per_share']=.378
    if fault=='wrong_coverage_cash':c.loc[1,'cash_per_share']=.092
    if fault=='wrong_description':g['components'][0]['expected_description']='proposal'
    if fault=='unreviewed':g['reviewed']=False
    if fault=='late_notice':g['announcement_date']='2024-06-14'
    if fault=='late_available':g['available_date']='2024-06-17'
    if fault=='wrong_existing_cash':g['expected_existing']['cash_per_share']=.287
    if fault=='unexpected_existing':g['expected_existing']=None
    if fault=='missing_existing':a=a.iloc[:0]
    if fault=='bad_reference_page':g['reference_source_page']=0
    if fault=='bad_payment_page':g['payment_source_page']=0
    if fault=='duplicate_group':e.append(deepcopy(g))
    with pytest.raises(ValueError):reconcile_reviewed_groups(a,c,raw,e,cal)


def test_combined_actual_cash_is_used_by_economic_return_ledger_once():
    from ashare_mvp.corporate_actions import economic_return_prices
    a,c,raw,e,cal=inputs();result,_,_=reconcile_reviewed_groups(a,c,raw,e,cal)
    prices=pd.DataFrame(dict(symbol='sh.601966',date=cal,open=[10.]*(len(cal)-1)+[9.63],
        close=[10.]*(len(cal)-1)+[9.63],preclose=[10.]*(len(cal)-1)+[9.63],tradestatus='1'))
    ledger=economic_return_prices(prices,result,cal,reference_tick_size=.01)
    assert ledger.iloc[-1].dividend_receivable==.377
    assert ledger.iloc[-1].total_return_open==pytest.approx(9.63+.377)


@pytest.mark.parametrize('tamper',[False,True])
def test_file_reconciliation_seals_lineage_and_rejects_tampered_parent(tmp_path,tamper):
    import hashlib,json
    from ashare_mvp.reviewed_payment_groups import reconcile_reviewed_payment_files
    a,c,raw,e,cal=inputs();data=tmp_path/'data';market=tmp_path/'market'
    data.mkdir();market.mkdir()
    def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
    calendar=tmp_path/'calendar.csv'
    pd.DataFrame({'calendar_date':cal,'is_trading_day':'1'}).to_csv(calendar,index=False)
    periods={}
    for period in raw.source_period.unique():
        day=pd.Timestamp(period);directory=market/f'{day.year}Q{day.quarter}';directory.mkdir()
        f=raw[raw.source_period.eq(period)].copy();f['REPORT_DATE']=period
        f.to_csv(directory/'records.csv',index=False)
        periods[period]=dict(status='ok',rows=len(f),hashes={'records.csv':digest(directory/'records.csv')})
    (market/'manifest.json').write_text(json.dumps(dict(status='downloaded_raw',periods=periods)))
    a.to_csv(data/'actions.csv',index=False);c.to_csv(data/'coverage.csv',index=False)
    meta=dict(market_manifest_sha256=digest(market/'manifest.json'),input_hashes={'calendar.csv':digest(calendar)},
        matched_events=len(a),status_counts=c.status.value_counts().to_dict(),
        actions_sha256=digest(data/'actions.csv'),coverage_sha256=digest(data/'coverage.csv'))
    (data/'summary.json').write_text(json.dumps(meta));notices=tmp_path/'notices.json'
    notices.write_text(json.dumps(e));out=tmp_path/'out'
    if tamper:
        with (data/'actions.csv').open('a') as file:file.write('tampered')
        with pytest.raises(ValueError,match='hash mismatch'):
            reconcile_reviewed_payment_files(data,market,calendar,notices,out)
        assert not out.exists()
    else:
        summary=reconcile_reviewed_payment_files(data,market,calendar,notices,out)
        assert summary['matched_events']==1 and not summary['model_ready']
        assert summary['actions_sha256']==digest(out/'actions.csv')
        assert summary['coverage_sha256']==digest(out/'coverage.csv')
        assert (out/'input_reviewed_groups.json').read_bytes()==notices.read_bytes()

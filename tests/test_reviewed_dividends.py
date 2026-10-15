import hashlib,json
import pandas as pd
import pytest
from test_dividend_reconciliation import inputs
from ashare_mvp.dividend_reconciliation import reconcile_dividend_sources


def evidence(symbol='sh.600001',cash=.15):
    return pd.DataFrame([dict(symbol=symbol,source_period='2017-12-31',ex_date='2018-07-13',announcement_date='2018-07-06',available_date='2018-07-09',pay_date='2018-07-16',cash_per_share=cash,share_multiplier=1.,ordinary_cash_per_share=.1,special_cash_per_share=cash-.1,expected_provider_cash_per_share=.1,expected_provider_share_multiplier=1.,expected_provider_description='10派1元',source_url='https://example.test/implementation.pdf',source_sha256='a'*64,source_page=1,reviewed=True)])


def test_reviewed_special_cash_and_payment_date_are_used_once(tmp_path):
    args=inputs(tmp_path)
    result=reconcile_dividend_sources(*args,reviewed_events=evidence())
    assert result['matched_events']==2 and result['reviewed_notice_events']==1
    assert result['status_counts']['matched_reviewed_notice']==1 and 'missing_payment_evidence' not in result['status_counts']
    actions=pd.read_csv(args[3]/'actions.csv');row=actions[actions.symbol=='sh.600001'].iloc[0]
    assert row.cash_per_share==.15 and row.ordinary_cash_per_share==.1 and row.special_cash_per_share==pytest.approx(.05)
    assert row.pay_date=='2018-07-16' and row.source_notice_sha256=='a'*64
    assert result['coverage_sha256']==hashlib.sha256((args[3]/'coverage.csv').read_bytes()).hexdigest()
    assert (args[3]/'input_reviewed_distributions.json').exists()


@pytest.mark.parametrize('field,value,message',[
    ('reviewed',False,'unreviewed'),('expected_provider_cash_per_share',.2,'original provider terms'),
    ('expected_provider_description','10派2元','original provider terms'),
    ('available_date','2018-07-17','not visible'),('source_sha256','bad','URL and hash')])
def test_changed_source_unreviewed_or_future_notice_is_rejected_before_output(tmp_path,field,value,message):
    args=inputs(tmp_path);e=evidence();e.loc[0,field]=value
    with pytest.raises(ValueError,match=message):reconcile_dividend_sources(*args,reviewed_events=e)
    assert not args[3].exists()


def test_review_cannot_silently_override_an_independent_payment_conflict(tmp_path):
    args=inputs(tmp_path)
    with pytest.raises(ValueError,match='conflicts with independent payment'):
        reconcile_dividend_sources(*args,reviewed_events=evidence(symbol='sh.600000'))
    assert not args[3].exists()


def test_ordinary_special_components_must_match_total_payout(tmp_path):
    args=inputs(tmp_path);e=evidence();e['special_cash_per_share']=.2
    with pytest.raises(ValueError,match='components do not sum'):
        reconcile_dividend_sources(*args,reviewed_events=e)
    assert not args[3].exists()


def test_provider_total_already_includes_special_cash_without_double_count(tmp_path):
    args=inputs(tmp_path);e=evidence(cash=.1)
    e['ordinary_cash_per_share']=.06;e['special_cash_per_share']=.04
    result=reconcile_dividend_sources(*args,reviewed_events=e)
    actions=pd.read_csv(args[3]/'actions.csv').set_index('symbol')
    assert result['reviewed_notice_events']==1
    assert actions.loc['sh.600001','cash_per_share']==pytest.approx(.1)
    assert actions.loc['sh.600001','special_cash_per_share']==pytest.approx(.04)

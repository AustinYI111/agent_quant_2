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


def public_holder_evidence():
    e=evidence(symbol='sh.600000')
    e['ordinary_cash_per_share']=.15;e['special_cash_per_share']=0.
    e['payout_scope']='public_a_share';e['independent_conflict_reviewed']=True
    e['expected_independent_cash_per_share']=.1;e['expected_independent_share_multiplier']=1.
    e['expected_independent_pay_date']='2018-07-16'
    return e


def test_reviewed_holder_class_payout_preserves_prior_provider_value(tmp_path):
    args=inputs(tmp_path);result=reconcile_dividend_sources(*args,reviewed_events=public_holder_evidence())
    row=pd.read_csv(args[3]/'actions.csv').set_index('symbol').loc['sh.600000']
    assert row.cash_per_share==pytest.approx(.15) and row.independent_provider_cash_before_review==pytest.approx(.1)
    assert row.reviewed_payout_scope=='public_a_share' and result['reviewed_notice_events']==1


@pytest.mark.parametrize('field,value',[('expected_independent_cash_per_share',.09),('expected_independent_share_multiplier',1.1),('expected_independent_pay_date','2018-07-17')])
def test_holder_class_review_cannot_accept_changed_independent_source(tmp_path,field,value):
    args=inputs(tmp_path);e=public_holder_evidence();e[field]=value
    with pytest.raises(ValueError,match='anchor changed'):reconcile_dividend_sources(*args,reviewed_events=e)
    assert not args[3].exists()


def test_reviewed_conflict_requires_public_holder_scope(tmp_path):
    args=inputs(tmp_path);e=public_holder_evidence();e['payout_scope']='controlling_holder'
    with pytest.raises(ValueError,match='public-holder scope'):
        reconcile_dividend_sources(*args,reviewed_events=e)
    assert not args[3].exists()


def test_reviewed_conflict_cannot_ignore_missing_independent_source_event(tmp_path):
    args=inputs(tmp_path);e=public_holder_evidence();e['symbol']='sh.600001'
    with pytest.raises(ValueError,match='anchor has no matching event'):
        reconcile_dividend_sources(*args,reviewed_events=e)
    assert not args[3].exists()


def test_known_total_can_preserve_unknown_component_split_in_mixed_batch(tmp_path):
    args=inputs(tmp_path)
    known=evidence(symbol='sh.600000',cash=.1)
    unknown=evidence();unknown[['ordinary_cash_per_share','special_cash_per_share']]=float('nan')
    result=reconcile_dividend_sources(*args,reviewed_events=pd.concat([known,unknown],ignore_index=True))
    frame=pd.read_csv(args[3]/'actions.csv').set_index('symbol')
    assert result['reviewed_notice_events']==2 and frame.loc['sh.600001','cash_per_share']==pytest.approx(.15)
    assert pd.isna(frame.loc['sh.600001','ordinary_cash_per_share']) and pd.isna(frame.loc['sh.600001','special_cash_per_share'])


def test_half_known_cash_component_pair_is_not_silently_filled(tmp_path):
    args=inputs(tmp_path);e=evidence();e['special_cash_per_share']=float('nan')
    with pytest.raises(ValueError,match='both be provided or both absent'):
        reconcile_dividend_sources(*args,reviewed_events=e)
    assert not args[3].exists()

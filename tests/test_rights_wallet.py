from copy import deepcopy
from decimal import Decimal

import pandas as pd
import pytest

from ashare_mvp.rights_terms import normalize_rights_case, rights_reference
from ashare_mvp.rights_wallet import RightsWallet, UnresolvedRightsOutcome


CAL = pd.bdate_range('2024-01-02', '2024-02-29')


def event(**changes):
    spec = dict(event_id='rights-a', symbol='sz.002142',
                offer_publication_date='2024-01-02', record_date='2024-01-03',
                subscription_start='2024-01-04', subscription_end='2024-01-10',
                expected_result_date='2024-01-12', offer_ratio='0.1',
                subscription_price='10', outcome='success',
                result_publication_date='2024-01-12', ex_date='2024-01-12',
                registered_ordinary_shares=1000, issued_ordinary_shares=100,
                quoted_exchange_ratio_per_10='1', aggregate_subscription_cash='1000',
                post_issue_ordinary_shares=1100,
                listing_publication_date='2024-01-16', tradable_from='2024-01-18')
    spec.update(changes)
    return normalize_rights_case(spec, CAL)


def registered(events=None, policy='cash_limited_subscribe', cash=2000., shares=1000.):
    wallet = RightsWallet(events or [event()], policy)
    holdings = {'sz.002142': shares}
    wallet.open('2024-01-03', cash, holdings, {})
    wallet.close('2024-01-03', holdings)
    return wallet, holdings


def test_paid_subscription_conserves_wealth_except_explicit_fee():
    wallet, holdings = registered()
    cash, claim, fees, rows = wallet.open('2024-01-04', 2000., holdings, {})
    assert cash == pytest.approx(999.7)
    assert claim == 1000.
    assert fees == pytest.approx(.3)
    assert cash + claim == pytest.approx(2000. - fees)
    assert holdings == {'sz.002142': 1000.}
    assert rows[0]['cash_delta'] == pytest.approx(-1000.3)


@pytest.mark.parametrize('cash', [0., 1., 500., 1000., 1000.3, 10000.])
def test_subscription_never_borrows_or_exceeds_entitlement(cash):
    wallet, holdings = registered(cash=cash)
    remaining, claim, fees, _ = wallet.open('2024-01-04', cash, holdings, {})
    assert remaining >= 0
    assert wallet.subscribed.get('rights-a', 0.) <= 100.
    assert remaining + claim + fees == pytest.approx(cash)


def test_cash_arriving_later_can_fund_remaining_rights_only():
    wallet, holdings = registered(cash=0.)
    wallet.open('2024-01-04', 0., holdings, {})
    wallet.open('2024-01-05', 500., holdings, {})
    cash, claim, _, _ = wallet.open('2024-01-08', 1000., holdings, {})
    assert wallet.subscribed['rights-a'] == pytest.approx(100.)
    assert claim == pytest.approx(1000.)
    assert cash == pytest.approx(499.7)


def test_multiple_issues_share_available_cash_proportionately():
    second = event(event_id='rights-b', symbol='sh.600030', subscription_price='20',
                   aggregate_subscription_cash='2000')
    wallet = RightsWallet([event(), second], 'cash_limited_subscribe', 0.)
    holdings = {'sz.002142': 1000., 'sh.600030': 1000.}
    wallet.open('2024-01-03', 1500., holdings, {})
    wallet.close('2024-01-03', holdings)
    cash, claim, _, _ = wallet.open('2024-01-04', 1500., holdings, {})
    assert cash == 0.
    assert claim == 1500.
    assert list(wallet.subscribed.values()) == [50., 50.]


def test_decline_preserves_cash_and_original_shares():
    wallet, holdings = registered(policy='decline')
    cash, claim, fees, _ = wallet.open('2024-01-04', 2000., holdings, {})
    assert (cash, claim, fees) == (2000., 0., 0.)
    _, _, _, rows = wallet.open('2024-01-11', cash, holdings, {})
    assert rows[0]['kind'] == 'rights_lapsed'
    assert rows[0]['quantity'] == 100.
    assert holdings == {'sz.002142': 1000.}


def test_registry_uses_close_holdings_and_is_not_repeated():
    wallet = RightsWallet([event()], 'cash_limited_subscribe')
    holdings = {'sz.002142': 1000.}
    wallet.open('2024-01-03', 0., holdings, {})
    holdings['sz.002142'] = 400.
    wallet.close('2024-01-03', holdings)
    assert wallet.entitlements['rights-a'] == 40.
    with pytest.raises(ValueError, match='already recorded'):
        wallet.close('2024-01-03', holdings)


def test_no_registry_ownership_gets_no_free_shares():
    wallet, holdings = registered(shares=0.)
    _, claim, _, rows = wallet.open('2024-01-04', 2000., holdings, {})
    assert claim == 0.
    assert rows == []


def test_result_and_listing_publication_clocks_are_distinct():
    wallet, holdings = registered()
    cash, _, _, _ = wallet.open('2024-01-04', 2000., holdings, {})
    # Result is dated Friday; the account cannot use it until Monday.
    _, claim, _, _ = wallet.open('2024-01-12', cash, holdings, {})
    assert claim == 1000.
    _, claim, _, _ = wallet.open('2024-01-15', cash, holdings, {'sz.002142': 20.})
    assert claim == 2000.
    assert holdings['sz.002142'] == 1000.
    wallet.open('2024-01-17', cash, holdings, {'sz.002142': 21.})
    assert holdings['sz.002142'] == 1000.
    _, claim, _, rows = wallet.open('2024-01-18', cash, holdings, {'sz.002142': 21.})
    assert claim == 0.
    assert holdings['sz.002142'] == 1100.
    assert rows[-1]['kind'] == 'shares_unlocked'


@pytest.mark.parametrize('outcome', ['failed', 'canceled'])
def test_failed_or_canceled_principal_refund_requires_disclosed_clock(outcome):
    wallet, holdings = registered([event(outcome=outcome, ex_date=None,
                                        tradable_from=None, refund_date='2024-01-16')])
    cash, _, fees, _ = wallet.open('2024-01-04', 2000., holdings, {})
    _, claim, _, _ = wallet.open('2024-01-16', cash, holdings, {})
    assert claim == 1000.
    cash, claim, _, rows = wallet.open('2024-01-17', cash, holdings, {})
    assert cash == pytest.approx(2000. - fees)
    assert claim == 0.
    assert rows[-1]['kind'] == 'refund'
    assert holdings['sz.002142'] == 1000.


@pytest.mark.parametrize('change', [dict(outcome=None, result_publication_date=None),
                                   dict(outcome='failed', refund_date=None)])
def test_unknown_positive_claim_stops_without_mutation(change):
    wallet, holdings = registered([event(**change)])
    cash, _, _, _ = wallet.open('2024-01-04', 2000., holdings, {})
    before = deepcopy(wallet.snapshot())
    with pytest.raises(UnresolvedRightsOutcome):
        wallet.open('2024-01-15', cash, holdings, {})
    assert wallet.snapshot() == before
    assert holdings['sz.002142'] == 1000.


def test_unknown_unowned_issue_does_not_stop_account():
    wallet, holdings = registered([event(outcome=None, result_publication_date=None)], shares=0.)
    assert wallet.open('2024-01-15', 2000., holdings, {})[:3] == (2000., 0., 0.)


def test_subscription_does_not_read_future_outcome():
    a, ha = registered([event()])
    b, hb = registered([event(outcome='failed', refund_date='2024-01-16')])
    assert a.open('2024-01-04', 2000., ha, {}) == b.open('2024-01-04', 2000., hb, {})


def test_reference_uses_actual_aggregate_issue_ratio_and_strict_rounding():
    e = event(issued_ordinary_shares=99, quoted_exchange_ratio_per_10='.99',
              aggregate_subscription_cash='990', post_issue_ordinary_shares=1099)
    assert e['exchange_ratio'] == Decimal('.099')
    assert rights_reference('20', '19.10', e)['matches'] is True
    assert rights_reference('20', '19.09', e)['matches'] is False


@pytest.mark.parametrize('change', [dict(aggregate_subscription_cash='999'),
                                   dict(post_issue_ordinary_shares=1101),
                                   dict(quoted_exchange_ratio_per_10='.9'),
                                   dict(offer_publication_date='2024-01-03'),
                                   dict(issued_ordinary_shares=101)])
def test_inconsistent_source_terms_rejected(change):
    with pytest.raises(ValueError):
        event(**change)


def test_invalid_policy_rejected():
    with pytest.raises(ValueError):
        RightsWallet([event()], 'choose_best_return')


def test_missing_mark_does_not_partially_confirm_claim():
    wallet, holdings = registered()
    cash, _, _, _ = wallet.open('2024-01-04', 2000., holdings, {})
    before = deepcopy(wallet.__dict__)
    with pytest.raises(ValueError, match='valuation'):
        wallet.open('2024-01-15', cash, holdings, {})
    assert wallet.__dict__ == before


@pytest.mark.parametrize('change', [dict(refund_date='2024-01-03', outcome='failed'),
                                   dict(refund_date='2024-01-16'),
                                   dict(result_publication_date='2024-01-10')])
def test_impossible_settlement_order_rejected(change):
    with pytest.raises(ValueError):
        event(**change)


def test_visible_cancellation_prevents_payment_but_future_failure_does_not():
    wallet, holdings = registered([event(outcome='canceled', result_publication_date='2024-01-03',
                                        refund_date=None)])
    assert wallet.open('2024-01-04', 2000., holdings, {})[:3] == (2000., 0., 0.)


def test_source_evidence_loader_checks_primary_identity_and_tampering(tmp_path):
    import json
    from ashare_mvp.phase2_protocol import sha256
    from ashare_mvp.rights_terms import load_rights_evidence, serialize_case
    root = tmp_path/'project'
    root.mkdir()
    sources = tmp_path/'sources'
    sources.mkdir()
    notices = []
    records = {}
    roles = dict(offer=('2024-01-02', '配股发行公告',
                        ['record_date','subscription_start','subscription_end','offer_ratio','subscription_price','expected_result_date']),
                 result=('2024-01-12', '配股发行结果公告',
                         ['outcome','ex_date','registered_ordinary_shares','issued_ordinary_shares','aggregate_subscription_cash']),
                 listing=('2024-01-16', '配股股份变动及获配股票上市公告书',
                          ['tradable_from','post_issue_ordinary_shares']))
    for i, (role, (date, title, parameters)) in enumerate(roles.items(), 1):
        file = sources/(role+'.PDF')
        file.write_bytes(b'%PDF-fixture-only-not-a-real-notice')
        url = f'https://static.cninfo.com.cn/finalpage/{date}/{i}.PDF'
        notices.append(dict(url=url, sha256=sha256(file), observed_at_utc='2024-02-01T00:00:00Z', title=title))
        records[role] = dict(source_file=file.name, source_sha256=sha256(file), source_url=url,
                             capture_file='capture.json', observed_at_utc='2024-02-01T00:00:00Z',
                             reviewed_at_utc='2024-02-01T01:00:00Z', reviewed=True,
                             reviewed_pages=[1], parameter_pages={p:1 for p in parameters})
    capture = sources/'capture.json'
    capture.write_text(json.dumps(dict(code='002142', notices=notices)), encoding='utf8')
    for record in records.values():
        record['capture_sha256'] = sha256(capture)
    config = dict(schema='ordinary_rights_evidence_v1', original_vintages_verified=False,
                  result_preopen_timestamp_verified=False,
                  events=[dict(terms=serialize_case(event()), sources=records)])
    file = root/'rights.json'
    def write():
        file.write_text(json.dumps(config), encoding='utf8')
    write()
    events, proof = load_rights_evidence('rights.json', root, sources, CAL)
    assert len(events) == 1 and len(proof['evidence_source_hashes']) == 4
    records['result']['source_url'] = records['offer']['source_url']
    write()
    with pytest.raises(ValueError, match='publication identity'):
        load_rights_evidence('rights.json', root, sources, CAL)
    records['result']['source_url'] = notices[1]['url']
    write()
    (sources/'offer.PDF').write_bytes(b'%PDF-changed')
    with pytest.raises(ValueError, match='changed'):
        load_rights_evidence('rights.json', root, sources, CAL)

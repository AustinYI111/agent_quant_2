import hashlib,json
import pandas as pd
import pytest
from test_dividend_reconciliation import inputs
from ashare_mvp.dividend_reconciliation import reconcile_dividend_sources
from ashare_mvp.security_identity import stitch_code_prices
from ashare_mvp.public_data import is_ashare


def changes():
    return pd.DataFrame([dict(old_symbol='sz.300114',new_symbol='sz.302132',announcement_date='2025-02-15',effective_date='2025-02-17',share_ratio=1,source_url='https://static.cninfo.com.cn/test-fixture.pdf')])


def rename_fixture(args,market_alias=True,payment_alias=False,duplicate_payment=False):
    file=args[0]/'2017Q4/records.csv';raw=pd.read_csv(file)
    raw.loc[raw.SECUCODE.eq('600000.SH'),'SECUCODE']='302132.SZ' if market_alias else '300114.SZ'
    raw.to_csv(file,index=False);path=args[0]/'manifest.json';m=json.loads(path.read_text());m['periods']['2017-12-31']['hashes']['records.csv']=hashlib.sha256(file.read_bytes()).hexdigest();path.write_text(json.dumps(m))
    file=args[1]/'actions.csv';a=pd.read_csv(file);a['symbol']='sz.302132' if payment_alias else 'sz.300114'
    if duplicate_payment:
        copy=a.copy();copy['symbol']='sz.300114';a=pd.concat([a,copy],ignore_index=True)
    a.to_csv(file,index=False);(args[1]/'summary.json').write_text(json.dumps({'actions_sha256':hashlib.sha256(file.read_bytes()).hexdigest()}))


@pytest.mark.parametrize('market_alias,payment_alias',[(True,False),(False,True),(True,True)])
def test_latest_code_backfill_matches_old_issuer_without_changing_event_clock(tmp_path,market_alias,payment_alias):
    args=inputs(tmp_path);rename_fixture(args,market_alias,payment_alias)
    result=reconcile_dividend_sources(*args,code_changes=changes())
    row=pd.read_csv(args[3]/'actions.csv').iloc[0]
    assert result['matched_events']==1 and row.symbol=='sz.300114'
    assert row.provider_symbol==('sz.302132' if market_alias else 'sz.300114')
    assert row.payment_provider_symbol==('sz.302132' if payment_alias else 'sz.300114')
    assert row.available_date=='2018-07-09' and row.pay_date=='2018-07-16' and row.cash_per_share==.1
    assert result['code_changes_sha256']==hashlib.sha256((args[3]/'input_code_changes.json').read_bytes()).hexdigest()
    # Source snapshots still contain the untouched independent provider bytes.
    assert (args[3]/'input_payment_actions.csv').read_bytes()==(args[1]/'actions.csv').read_bytes()


def test_no_evidence_does_not_guess_a_code_mapping(tmp_path):
    args=inputs(tmp_path);rename_fixture(args)
    result=reconcile_dividend_sources(*args)
    assert result['matched_events']==0 and result['status_counts']['missing_payment_evidence']==2


def test_alias_collision_in_payment_history_is_rejected_before_export(tmp_path):
    args=inputs(tmp_path);rename_fixture(args,payment_alias=True,duplicate_payment=True)
    with pytest.raises(ValueError,match='ambiguous same-issuer'):
        reconcile_dividend_sources(*args,code_changes=changes())
    assert not args[3].exists()


def test_non_one_for_one_change_is_not_a_plain_identity_fix(tmp_path):
    args=inputs(tmp_path);rename_fixture(args);c=changes();c['share_ratio']=2
    with pytest.raises(ValueError,match='separate corporate action'):
        reconcile_dividend_sources(*args,code_changes=c)
    assert not args[3].exists()


def test_new_chinext_code_still_uses_dated_trading_aliases():
    assert is_ashare('sz.302132') and not is_ashare('bj.920001')
    p=pd.DataFrame(dict(date=['2024-01-02','2024-01-02','2025-02-17'],symbol=['sz.300114','sz.302132','sz.302132'],close=[10,0,12]))
    result,rejected=stitch_code_prices(p,changes())
    assert result.symbol.tolist()==['sz.300114','sz.300114']
    assert result.trading_symbol.tolist()==['sz.300114','sz.302132']
    assert result.close.tolist()==[10,12] and len(rejected)==1


def test_reviewed_notice_uses_old_official_code_and_exact_latest_provider_anchor(tmp_path):
    from test_reviewed_dividends import evidence
    args=inputs(tmp_path);rename_fixture(args)
    e=evidence(symbol='sz.300114',cash=.1);e['provider_symbol']='sz.302132'
    result=reconcile_dividend_sources(*args,reviewed_events=e,code_changes=changes())
    row=pd.read_csv(args[3]/'actions.csv').iloc[0]
    assert result['reviewed_notice_events']==1 and row.symbol=='sz.300114' and row.provider_symbol=='sz.302132'
    assert row.cash_per_share==.1 and row.pay_date=='2018-07-16'


def test_reviewed_provider_alias_cannot_be_guessed_without_code_change_evidence(tmp_path):
    from test_reviewed_dividends import evidence
    args=inputs(tmp_path);rename_fixture(args)
    e=evidence(symbol='sz.300114',cash=.1);e['provider_symbol']='sz.302132'
    with pytest.raises(ValueError,match='matching verified issuer evidence'):
        reconcile_dividend_sources(*args,reviewed_events=e)
    assert not args[3].exists()

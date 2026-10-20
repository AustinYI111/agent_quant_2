import hashlib
import json

import pandas as pd
import pytest

from ashare_mvp.dividend_union import merge_dividend_events
from ashare_mvp.security_identity import code_change_payload


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path):
    base, extra = tmp_path / 'base', tmp_path / 'extra'
    base.mkdir(); extra.mkdir()
    calendar = tmp_path / 'calendar.csv'
    pd.DataFrame({'date': pd.bdate_range('2020-01-02', '2020-01-31')}).to_csv(calendar, index=False)
    row = dict(symbol='sh.600000', ex_date='2020-01-15', available_date='2020-01-13',
        pay_date='2020-01-16', cash_per_share=.1, share_multiplier=1.,
        announcement_date='2020-01-10', event_vintage_certified=False)
    pd.DataFrame([row]).to_csv(base / 'actions.csv', index=False)
    pd.DataFrame([dict(security='600000.SH', ex_date='2020-01-15', status='matched_payment_evidence'),
        dict(security='600002.SH', ex_date='2020-01-15', status='unresolved')]).to_csv(base / 'coverage.csv', index=False)
    meta = dict(evaluation_start='2020-01-02', evaluation_end='2020-01-31',
        matched_events=1, status_counts={'matched_payment_evidence': 1, 'unresolved': 1},
        actions_sha256=digest(base / 'actions.csv'), coverage_sha256=digest(base / 'coverage.csv'),
        partial_market_source=False)
    (base / 'summary.json').write_text(json.dumps(meta))
    row.update(symbol='sh.600001', source_file='sh.600001_2020.csv', source_row=0)
    pd.DataFrame([row]).to_csv(extra / 'actions.csv', index=False)
    manifest = dict(schema='dividend_raw_v1', status='downloaded_raw',
        request={'year_type': 'operate'}, files={'sh.600001_2020.csv': {'rows': 1, 'sha256': 'a'*64}})
    (extra / 'input_manifest.json').write_text(json.dumps(manifest))
    (extra / 'input_calendar.csv').write_bytes(calendar.read_bytes())
    meta = dict(status='normalized', rejected=0, accepted=1, calendar_sha256=digest(calendar),
        input_manifest_sha256=digest(extra / 'input_manifest.json'), actions_sha256=digest(extra / 'actions.csv'))
    (extra / 'summary.json').write_text(json.dumps(meta))
    return base, [extra], calendar, tmp_path / 'out'


def change_extra(args, **changes):
    path=args[1][0]; frame=pd.read_csv(path/'actions.csv')
    for field,value in changes.items(): frame[field]=value
    frame.to_csv(path/'actions.csv',index=False)
    meta=json.loads((path/'summary.json').read_text());meta['actions_sha256']=digest(path/'actions.csv')
    (path/'summary.json').write_text(json.dumps(meta))


def test_add_missing_history_but_preserve_plan_failures_and_seal_all_inputs(tmp_path):
    args=fixture(tmp_path);result=merge_dividend_events(*args)
    assert result['matched_events']==2 and result['supplemental_events_added']==1
    assert result['in_scope_records']==3
    assert result['status_counts']=={'matched_payment_evidence':1,'unresolved':1,'supplemental_payment_evidence':1}
    assert result['certified'] is result['model_ready'] is False
    assert result['partial_market_source'] is False
    for name,value in result['input_hashes'].items(): assert digest(args[3]/('input_'+name))==value
    assert digest(args[3]/'actions.csv')==result['actions_sha256']
    assert digest(args[3]/'coverage.csv')==result['coverage_sha256']
    assert pd.read_csv(args[3]/'supplement_audit.csv').status.iloc[0]=='added_historical_distribution'


def test_duplicate_events_are_counted_once_with_later_visibility(tmp_path):
    args=fixture(tmp_path);change_extra(args,symbol='sh.600000',available_date='2020-01-14')
    result=merge_dividend_events(*args)
    assert result['matched_events']==1 and result['supplemental_events_added']==0
    assert pd.read_csv(args[3]/'actions.csv').available_date.iloc[0]=='2020-01-14'


@pytest.mark.parametrize('changes,message',[
    ({'cash_per_share':.2,'symbol':'sh.600000'},'conflicting distribution entitlements'),
    ({'pay_date':'2020-01-17','symbol':'sh.600000'},'conflicting distribution settlement'),
    ({'available_date':'2020-01-16'},'visibility'),
    ({'announcement_date':'2020-01-13'},'announcement'),
    ({'cash_per_share':float('nan')},'terms'),
    ({'share_multiplier':0},'shares'),
    ({'pay_date':None},'payment clock'),
    ({'symbol':'bj.920001'},'security'),
    ({'source_row':1},'provenance'),
    ({'event_vintage_certified':True},'certification'),
    ({'symbol':'sh.600002'},'unresolved market plan'),
])
def test_invalid_or_conflicting_supplement_never_exports(tmp_path,changes,message):
    args=fixture(tmp_path);change_extra(args,**changes)
    with pytest.raises(ValueError,match=message):merge_dividend_events(*args)
    assert not args[3].exists()


def test_hash_tampering_rejected(tmp_path):
    args=fixture(tmp_path);(args[1][0]/'input_manifest.json').write_text('{}')
    with pytest.raises(ValueError,match='hash mismatch'):merge_dividend_events(*args)


def test_same_issuer_alias_is_not_double_counted_and_policy_required(tmp_path):
    args=fixture(tmp_path)
    policy=pd.DataFrame([dict(old_symbol='sh.600000',new_symbol='sh.600001',announcement_date='2019-01-01',
        effective_date='2019-01-02',share_ratio=1,source_url='https://example.org/notice')])
    raw=code_change_payload(policy).encode();(args[0]/'input_code_changes.json').write_bytes(raw)
    meta=json.loads((args[0]/'summary.json').read_text());meta['code_changes_sha256']=hashlib.sha256(raw).hexdigest()
    (args[0]/'summary.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError,match='identity policy'):merge_dividend_events(*args)
    result=merge_dividend_events(*args,code_changes=policy)
    assert result['matched_events']==1 and result['supplemental_records_checked']==1
    assert digest(args[3]/'input_code_changes.json')==result['code_changes_sha256']
    from ashare_mvp.security_identity import canonical_event_symbols
    exported=pd.read_csv(args[3]/'actions.csv')
    assert 'source_symbol' not in exported
    assert len(canonical_event_symbols(exported,policy))==1


def test_calendar_mismatch_is_not_silently_reinterpreted(tmp_path):
    args=fixture(tmp_path);args[2].write_text(args[2].read_text()+'2020-02-03\n')
    with pytest.raises(ValueError,match='span calendar'):merge_dividend_events(*args)


@pytest.mark.parametrize('name,key',[
    ('duplicate_audit.csv','duplicate_audit_sha256'),('precision_audit.csv','precision_audit_sha256'),
    ('input_reviewed_versions.json','reviewed_versions_sha256'),
])
def test_supplemental_review_evidence_is_verified_and_sealed(tmp_path,name,key):
    args=fixture(tmp_path);extra=args[1][0]
    (extra/name).write_text('sealed evidence',encoding='utf8')
    meta=json.loads((extra/'summary.json').read_text());meta[key]=digest(extra/name)
    (extra/'summary.json').write_text(json.dumps(meta))
    result=merge_dividend_events(*args)
    saved='supplement_0_'+name
    assert digest(args[3]/('input_'+saved))==result['input_hashes'][saved]==meta[key]
    (extra/name).write_text('changed',encoding='utf8')
    with pytest.raises(ValueError,match='hash mismatch'):
        merge_dividend_events(args[0],args[1],args[2],tmp_path/'bad')
    assert not (tmp_path/'bad').exists()

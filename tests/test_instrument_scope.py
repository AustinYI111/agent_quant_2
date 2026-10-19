import json
import pandas as pd
import pytest
from ashare_mvp.instrument_scope import classify_instrument_scope
from ashare_mvp.public_conversion import load_public_data
from test_market_dataset import fixture,prepare,digest


def evidence():
    return pd.DataFrame([dict(symbol='sh.689009',instrument_type='cdr',effective_from='2017-10-29',valid_through='2025-12-31',
        announcement_date='2017-10-28',source_url='https://example.org/initial-listing.pdf',source_sha256='a'*64,
        source_page=20,source_locator='initial listing approval, security code and CDR instrument type',reviewed=True)])


def inputs():
    cal=pd.bdate_range('2024-01-02','2024-01-08')
    coverage=pd.DataFrame([dict(security='689009.SH',status='unresolved',ex_date='2024-01-04'),
        dict(security='689999.SH',status='unresolved',ex_date='2024-01-04'),
        dict(security='600000.SH',status='missing_payment_evidence',ex_date='2024-01-04')])
    return coverage,pd.DataFrame({'symbol':['sh.600000']}),evidence(),cal


def test_only_explicit_reviewed_type_and_dates_are_classified_without_mutating_source():
    c,a,e,cal=inputs();original=c.copy(deep=True)
    scoped,audit,policy=classify_instrument_scope(c,a,e,cal)
    assert scoped.status.tolist()==['excluded_instrument','unresolved','missing_payment_evidence']
    assert len(audit)==1 and audit.original_status.iloc[0]=='unresolved'
    assert policy['excluded_event_records']==1
    pd.testing.assert_frame_equal(c,original)


@pytest.mark.parametrize('date',['2017-10-27','2026-01-05',None])
def test_before_effective_after_bound_or_unknown_date_never_excluded(date):
    c,a,e,_=inputs();c=c.iloc[:1].copy();c['ex_date']=date
    cal=pd.bdate_range('2017-10-20','2026-01-06')
    scoped,audit,_=classify_instrument_scope(c,a,e,cal)
    assert scoped.status.eq('unresolved').all() and audit.empty


@pytest.mark.parametrize('field,value',[
    ('symbol','sh.600000'),('instrument_type','ordinary_a_share'),('reviewed',False),
    ('source_sha256','bad'),('source_url','http://example.org/source'),('source_page',0),
    ('source_locator',''),('announcement_date','2017-10-29'),('valid_through','2017-01-01')])
def test_invalid_or_ordinary_share_exclusion_evidence_rejected(field,value):
    c,a,e,cal=inputs();e.loc[0,field]=value
    with pytest.raises(ValueError):classify_instrument_scope(c,a,e,cal)


def test_existing_payment_assets_are_never_removed_by_scope_policy():
    c,a,e,cal=inputs();a.loc[0,'symbol']='sh.689009'
    with pytest.raises(ValueError,match='existing payment assets'):classify_instrument_scope(c,a,e,cal)


def add_pending_cdr(paths,cal,security='689009.SH'):
    events=paths[3];file=events/'coverage.csv';coverage=pd.read_csv(file)
    coverage=pd.concat([coverage,pd.DataFrame([dict(security=security,status='unresolved',ex_date=str(cal[2].date()))])],ignore_index=True)
    coverage.to_csv(file,index=False);meta=json.loads((events/'summary.json').read_text())
    meta['coverage_sha256']=digest(file);meta['status_counts']=coverage.status.value_counts().to_dict()
    (events/'summary.json').write_text(json.dumps(meta))


def test_preparation_records_reviewed_cdr_scope_and_keeps_ordinary_prices(tmp_path):
    paths,cal=fixture(tmp_path);add_pending_cdr(paths,cal)
    result=prepare(tmp_path,paths,instrument_evidence=evidence())
    assert result['status']=='prepared_exploratory'
    assert result['instrument_scope_policy']['excluded_event_records']==1
    assert result['instrument_scope_audit_sha256']==digest(tmp_path/'audit/instrument_scope.csv')
    assert (tmp_path/'audit/input_instrument_evidence.json').exists()
    data=load_public_data(tmp_path/'dataset')
    assert len(data.prices)==8 and set(data.prices.symbol)=={'sh.600000','sz.000001'}
    assert data.metadata['instrument_scope_policy']['excluded_event_records']==1
    assert pd.read_csv(paths[3]/'coverage.csv').status.eq('unresolved').sum()==1


@pytest.mark.parametrize('reviewed',['none','unknown_code'])
def test_unreviewed_unknown_codes_still_block_preparation(tmp_path,reviewed):
    paths,cal=fixture(tmp_path);add_pending_cdr(paths,cal,'689999.SH' if reviewed=='unknown_code' else '689009.SH')
    result=prepare(tmp_path,paths,instrument_evidence=evidence() if reviewed=='unknown_code' else None)
    assert result['status']=='blocked' and result['issue_counts']['unresolved_event_identity']==1
    assert not (tmp_path/'dataset').exists()

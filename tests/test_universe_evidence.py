import json
import pandas as pd
import pytest
from ashare_mvp.universe_evidence import reviewed_non_listing_bounds
from ashare_mvp.public_conversion import load_public_data
from test_market_dataset import fixture,prepare,digest


def evidence(symbol='sh.600001',through='2026-10-15'):
    return pd.DataFrame([dict(symbol=symbol,evidence_kind='pre_ipo_status',pre_ipo_stage='waiting_listing',not_listed_through=through,source_observed_date=through,source_url='https://example.test/official-ipo',source_sha256='a'*64,source_locator='waiting[code='+symbol+']',reviewed=True)])


def test_reviewed_bound_covers_only_known_non_listing_interval():
    e=evidence();master=pd.DataFrame({'code':['sh.600000'],'ipoDate':['2010-01-01']})
    excluded,policy=reviewed_non_listing_bounds(e,'2024-01-31',['sh.600001'],master)
    assert excluded=={'sh.600001'} and policy['excluded_through_calendar_end']==['sh.600001']
    excluded,_=reviewed_non_listing_bounds(e,'2027-01-01',['sh.600001'],master)
    assert not excluded


@pytest.mark.parametrize('field,value,message',[
    ('reviewed',False,'unreviewed'),('source_sha256','bad','source hash'),
    ('evidence_kind','empty_api_response','explicit first-listing'),
    ('pre_ipo_stage',None,'null listing date is insufficient')])
def test_empty_or_unreviewed_api_evidence_cannot_remove_missing_identity(field,value,message):
    e=evidence();e.loc[0,field]=value
    master=pd.DataFrame({'code':[],'ipoDate':[]})
    with pytest.raises(ValueError,match=message):reviewed_non_listing_bounds(e,'2024-01-31',['sh.600001'],master)


def test_reviewed_non_listing_cannot_contradict_known_listing_history():
    with pytest.raises(ValueError,match='contradicts known listing'):
        reviewed_non_listing_bounds(evidence(),'2024-01-31',['sh.600001'],pd.DataFrame({'code':['sh.600001'],'ipoDate':['2019-01-01']}))


def add_target(paths):
    master=paths[2];p=master/'input_targets.csv'
    pd.DataFrame({'code':['sh.600000','sz.000001','sh.600001']}).to_csv(p,index=False)
    p=master/'manifest.json';m=json.loads(p.read_text());m['targets_sha256']=digest(master/'input_targets.csv');p.write_text(json.dumps(m))


def test_proven_future_ipo_unblocks_identity_without_fabricating_price_history(tmp_path):
    paths,_=fixture(tmp_path);add_target(paths)
    result=prepare(tmp_path,paths,non_listing_evidence=evidence())
    assert result['status']=='prepared_exploratory'
    assert result['reviewed_non_listing_policy']['excluded_through_calendar_end']==['sh.600001']
    data=load_public_data(tmp_path/'dataset')
    assert set(data.prices.symbol)=={'sh.600000','sz.000001'}
    assert data.metadata['reviewed_non_listing_policy']['bounds'][0]['source_sha256']=='a'*64
    assert 'non_listing_evidence' in data.metadata['input_hashes']


def test_expired_non_listing_bound_leaves_identity_unresolved(tmp_path):
    paths,_=fixture(tmp_path);add_target(paths)
    result=prepare(tmp_path,paths,non_listing_evidence=evidence(through='2018-01-02'))
    assert result['status']=='blocked' and result['issue_counts']['unresolved_security_identity']==1
    assert not (tmp_path/'dataset').exists()


def test_real_historical_quote_overrides_no_reviewed_exclusion(tmp_path):
    paths,cal=fixture(tmp_path);add_target(paths);market=paths[0]
    p=market/'manifest.json';manifest=json.loads(p.read_text())
    for date in cal:
        name=f'daily_{date:%Y%m%d}.csv';file=market/name;f=pd.read_csv(file)
        row=f[f.code=='sz.000001'].copy();row['code']='sh.600001'
        f=pd.concat([f,row],ignore_index=True);f.to_csv(file,index=False)
        manifest['files'][name].update(sha256=digest(file),rows=len(f))
    p.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='quote contradicts reviewed'):
        prepare(tmp_path,paths,non_listing_evidence=evidence())
    assert not (tmp_path/'dataset').exists()
    assert pd.read_csv(market/f'daily_{cal[0]:%Y%m%d}.csv').code.eq('sh.600001').any()


def test_first_listing_date_proves_only_strictly_earlier_absence():
    e=evidence();e['evidence_kind']='first_listing';e['listing_date']='2026-10-16'
    master=pd.DataFrame({'code':[],'ipoDate':[]})
    excluded,_=reviewed_non_listing_bounds(e,'2024-01-31',['sh.600001'],master)
    assert excluded=={'sh.600001'}
    e['not_listed_through']='2026-10-16'
    e['source_observed_date']='2026-10-16'
    with pytest.raises(ValueError,match='first-listing date must bound'):
        reviewed_non_listing_bounds(e,'2024-01-31',['sh.600001'],master)

import copy
import hashlib
import json
import pandas as pd
import pytest
from ashare_mvp.ordinary_share_settlement import OrdinaryListingBook, OrdinaryShareWallet, OrdinarySettlementStop


SYMBOL='sh.600001'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def fixture(tmp_path,change=None):
    project=tmp_path/'project';work=tmp_path/'work';project.mkdir();work.mkdir()
    calendar=pd.bdate_range('2024-01-02',periods=7)
    source=work/'notice.PDF';source.write_bytes(b'%PDF-1.4\nsynthetic fixture, not investment evidence')
    url='https://static.cninfo.com.cn/finalpage/2024-01-01/fixture.PDF'
    observation='2025-01-01T01:00:00Z'
    raw=dict(schema='original_candidate_notice_capture_v1',code='600001',ex_date='2024-01-04',notices=[dict(url=url,file='notice.PDF',sha256=digest(source),observed_at_utc=observation,source_announcement_time=int(pd.Timestamp('2024-01-01',tz='Asia/Shanghai').timestamp()*1000))])
    capture=work/'capture.json';capture.write_text(json.dumps(raw),encoding='utf8')
    row=dict(symbol=SYMBOL,record_date='2024-01-03',ex_date='2024-01-04',tradable_from='2024-01-05',announcement_date='2024-01-01',available_date='2024-01-02',expected_cash_per_share=.2,expected_share_multiplier=1.3,reviewed=True,source_file='@work/notice.PDF',source_capture_file='@work/capture.json',source_url=url,source_sha256=digest(source),source_capture_sha256=digest(capture),observed_at_utc=observation,reviewed_at_utc='2025-01-01T02:00:00Z',parameter_pages={k:[1] for k in ['record_date','ex_date','tradable_from','share_multiplier','cash_per_share']})
    if change:change(row,raw,source,capture)
    path=project/'listing.json';path.write_text(json.dumps([row]),encoding='utf8')
    return project,work,calendar,row,raw


def load(args):
    project,work,calendar,*_=args
    return OrdinaryListingBook('listing.json',project,work,calendar)


def empty():return pd.DataFrame(columns=['symbol','ex_date','share_multiplier','cash_per_share'])
def action(mult=1.3,cash=.2,symbol=SYMBOL):return pd.DataFrame([dict(symbol=symbol,ex_date='2024-01-04',share_multiplier=mult,cash_per_share=cash)])
def registered(book,holdings=None):
    wallet=OrdinaryShareWallet(book);holdings={SYMBOL:100.} if holdings is None else holdings
    for date in ['2024-01-02','2024-01-03']:
        wallet.open(date,holdings,{SYMBOL:13.},empty());wallet.close(date,holdings)
    return wallet,holdings


def test_delayed_listing_separates_nav_claim_from_sellable_shares(tmp_path):
    wallet,holdings=registered(load(fixture(tmp_path)))
    rows=wallet.open('2024-01-04',holdings,{SYMBOL:10.},action())
    assert holdings=={SYMBOL:100.}
    assert rows[0]['kind']=='bonus_claim_recognized'
    assert wallet.locked_exposure({SYMBOL:10.})==pytest.approx({SYMBOL:300.})
    assert holdings[SYMBOL]*10+wallet.value({SYMBOL:10.})==pytest.approx(1300.)
    # A target of zero can sell only the 100 existing shares: the separate
    # claim survives and the remaining equity is 1000 cash plus a 300 claim.
    cash=holdings.pop(SYMBOL)*10
    assert cash+wallet.value({SYMBOL:10.})==pytest.approx(1300.)
    wallet.close('2024-01-04',holdings)
    rows=wallet.open('2024-01-05',holdings,{SYMBOL:10.},empty())
    assert holdings[SYMBOL]==pytest.approx(30.)
    assert rows[0]['kind']=='bonus_shares_unlocked' and wallet.claims=={}
    assert cash+holdings[SYMBOL]*10==pytest.approx(1300.)


def test_explicit_same_day_listing_can_unlock(tmp_path):
    book=load(fixture(tmp_path,lambda r,*_:r.update(tradable_from='2024-01-04')))
    wallet,holdings=registered(book)
    rows=wallet.open('2024-01-04',holdings,{SYMBOL:10.},action())
    assert [r['kind'] for r in rows]==['bonus_claim_recognized','bonus_shares_unlocked']
    assert holdings[SYMBOL]==pytest.approx(130.) and not wallet.claims


@pytest.mark.parametrize('kind',['missing_listing','economics','multiplier_downgrade','reduction','missing_action','registry_changed','missing_price','paid_rights_overlap','duplicate'])
def test_bad_ex_date_preserves_holdings_registry_and_claims(tmp_path,kind):
    args=fixture(tmp_path);book=load(args)
    if kind=='missing_listing':
        (args[0]/'listing.json').write_text('[]');book=load(args)
    wallet,holdings=registered(book);actions=action();prices={SYMBOL:10.};other=[]
    if kind=='economics':actions=action(cash=.21)
    if kind=='multiplier_downgrade':actions=action(mult=1.)
    if kind=='reduction':actions=action(mult=.5)
    if kind=='missing_action':actions=empty()
    if kind=='registry_changed':holdings[SYMBOL]=99.
    if kind=='missing_price':prices={}
    if kind=='paid_rights_overlap':other=[SYMBOL]
    if kind=='duplicate':actions=pd.concat([actions,actions])
    before=copy.deepcopy(wallet.snapshot());old=dict(holdings);last=wallet.last_open
    with pytest.raises((OrdinarySettlementStop,ValueError)):
        wallet.open('2024-01-04',holdings,prices,actions,other)
    assert wallet.snapshot()==before and holdings==old and wallet.last_open==last


def test_unknown_positive_bonus_later_in_batch_cannot_partially_apply(tmp_path):
    wallet,holdings=registered(load(fixture(tmp_path)));holdings['sz.000001']=50.
    actions=pd.concat([action(),action(symbol='sz.000001')],ignore_index=True)
    before=wallet.snapshot();old=dict(holdings)
    with pytest.raises(OrdinarySettlementStop):wallet.open('2024-01-04',holdings,{SYMBOL:10.,'sz.000001':10.},actions)
    assert wallet.snapshot()==before and holdings==old


def test_missing_locked_mark_and_overlapping_action_do_not_erase_claim(tmp_path):
    wallet,holdings=registered(load(fixture(tmp_path)))
    wallet.open('2024-01-04',holdings,{SYMBOL:10.},action());wallet.close('2024-01-04',holdings)
    before=wallet.snapshot();old=dict(holdings)
    with pytest.raises(ValueError):wallet.open('2024-01-05',holdings,{},empty())
    overlap=action(mult=1.);overlap['ex_date']='2024-01-05'
    with pytest.raises(OrdinarySettlementStop,match='overlap'):wallet.open('2024-01-05',holdings,{SYMBOL:10.},overlap)
    assert wallet.snapshot()==before and holdings==old


@pytest.mark.parametrize('change',[
    lambda r,*_:r.update(reviewed=False),
    lambda r,*_:r.update(tradable_from='2024-01-03'),
    lambda r,*_:r.update(available_date='2024-01-03'),
    lambda r,*_:r.update(expected_share_multiplier=1.),
    lambda r,*_:r.update(source_url='https://untrusted.invalid/notice.PDF'),
    lambda r,*_:r.update(source_file='@work/../notice.PDF'),
    lambda r,*_:r.update(reviewed_at_utc='2099-01-01T00:00:00Z'),
    lambda r,*_:r.update(reviewed_at_utc='2024-01-01T00:00:00Z'),
    lambda r,*_:r.update(parameter_pages={'tradable_from':[1]}),
    lambda r,raw,s,c:s.write_bytes(b'tampered'),
    lambda r,raw,s,c:c.write_text('{}'),
])
def test_source_contract_rejects_incomplete_or_tampered_evidence(tmp_path,change):
    with pytest.raises(ValueError):load(fixture(tmp_path,change))


@pytest.mark.parametrize('field,value',[('code','600002'),('ex_date','2024-01-05'),('source_announcement_time',1704240000000),('observed_at_utc','2025-01-02T01:00:00Z')])
def test_capture_identity_checked_even_when_capture_hash_is_updated(tmp_path,field,value):
    def change(r,raw,s,c):
        if field in ['code','ex_date']:raw[field]=value
        else:raw['notices'][0][field]=value
        c.write_text(json.dumps(raw));r['source_capture_sha256']=digest(c)
    with pytest.raises(ValueError):load(fixture(tmp_path,change))


def test_source_book_does_not_claim_physical_credit_or_historical_vintage(tmp_path):
    book=load(fixture(tmp_path))
    assert book.historical_vintages_certified is False and book.physical_credit_dates_inferred is False
    with pytest.raises(TypeError):book.events[(SYMBOL,pd.Timestamp('2024-01-04'))]=None
    with pytest.raises(ValueError):OrdinaryShareWallet([])


def test_source_change_after_load_is_detected(tmp_path):
    args=fixture(tmp_path);book=load(args);book.verify_sources()
    (args[1]/'notice.PDF').write_bytes(b'%PDF-1.4 tampered after load')
    with pytest.raises(ValueError,match='changed after load'):book.verify_sources()


@pytest.mark.parametrize('layout',['legacy','paged'])
@pytest.mark.parametrize('fault',[None,'tamper_before','tamper_after','escape','hash_disagreement','omitted_input','late_clock','failed_capture'])
def test_query_evidence_is_bound_to_reviewed_capture(tmp_path,layout,fault):
    def change(row,raw,source,capture):
        query=source.parent/'query.json';query.write_text('{"announcements":[]}',encoding='utf8')
        record=dict(file=query.name,sha256=digest(query),observed_at_utc=row['observed_at_utc'])
        raw['query' if layout=='legacy' else 'query_pages']=record if layout=='legacy' else [record]
        if layout=='paged':
            raw['search_complete']=True
            raw['input_hashes']={source.name:digest(source),query.name:digest(query)}
        if fault=='tamper_before':query.write_text('changed',encoding='utf8')
        if fault=='escape':record['file']='../query.json'
        if fault=='hash_disagreement':raw['input_hashes']={query.name:'0'*64}
        if fault=='omitted_input':raw['input_hashes']={source.name:digest(source)}
        if fault=='late_clock':record['observed_at_utc']='2099-01-01T00:00:00Z'
        if fault=='failed_capture':raw['status']='failed'
        capture.write_text(json.dumps(raw),encoding='utf8');row['source_capture_sha256']=digest(capture)
    args=fixture(tmp_path,change)
    if fault and fault!='tamper_after':
        with pytest.raises(ValueError):load(args)
    else:
        book=load(args);book.verify_sources()
        assert '@work/query.json' in book.source_hashes
        if fault=='tamper_after':
            (args[1]/'query.json').write_text('changed',encoding='utf8')
            with pytest.raises(ValueError,match='changed after load'):book.verify_sources()


@pytest.mark.parametrize('fault',[None,'no_review','bad_hash','bad_host','late_observation'])
def test_captured_correction_is_also_bound_and_reviewed(tmp_path,fault):
    def change(r,raw,source,capture):
        extra=source.parent/'correction.PDF';extra.write_bytes(b'%PDF-1.4 synthetic correction')
        notice=dict(raw['notices'][0],file=extra.name,sha256=digest(extra))
        raw['notices'].append(notice)
        r['reviewed_captured_notice_pages']={'notice.PDF':[1],'correction.PDF':[1,2]}
        if fault=='no_review':del r['reviewed_captured_notice_pages']['correction.PDF']
        if fault=='bad_hash':extra.write_bytes(b'%PDF-1.4 tampered correction')
        if fault=='bad_host':notice['url']='https://untrusted.invalid/correction.PDF'
        if fault=='late_observation':notice['observed_at_utc']='2099-01-01T00:00:00Z'
        capture.write_text(json.dumps(raw));r['source_capture_sha256']=digest(capture)
    args=fixture(tmp_path,change)
    if fault:
        with pytest.raises(ValueError):load(args)
    else:
        book=load(args);assert '@work/correction.PDF' in book.source_hashes
        (args[1]/'correction.PDF').write_bytes(b'%PDF-1.4 changed later')
        with pytest.raises(ValueError):book.verify_sources()

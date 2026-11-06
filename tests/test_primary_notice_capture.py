import json
from urllib.parse import parse_qs
import pytest
from ashare_mvp.primary_notice_capture import capture,implementation_title


PDF=b'%PDF-1.4 synthetic original; not research market evidence'
def entry(identifier='1211130230',title='2021年半年度权益分派暨转增股本实施公告'):
    return dict(secCode='000951',announcementId=identifier,announcementTitle=title,adjunctUrl=f'finalpage/2021-09-23/{identifier}.PDF',announcementTime=1632326400000)


def transport(pages,issuer=None,change=None):
    def fetch(url,data,maximum):
        if 'topSearch' in url:body=json.dumps([dict(code='000951',orgId='issuer')] if issuer is None else issuer).encode()
        elif 'hisAnnouncement' in url:
            page=int(parse_qs(data.decode())['pageNum'][0]);body=json.dumps(pages[page-1]).encode()
        else:body=PDF
        return change(url,data,body) if change else (body,url)
    return fetch


def invoke(tmp_path,fetch,**kwargs):
    project=tmp_path/'project';work=tmp_path/'work';project.mkdir(exist_ok=True);work.mkdir(exist_ok=True)
    return capture(project,work,'000951','2021-09-29','@work/capture',fetch=fetch,**kwargs)


@pytest.mark.parametrize('title',[
    '公司2021年半年度权益分派暨转增股本实施公告',
    '2021年半年度权益分派暨资本公积金转增股本实施公告',
    '2021年度利润分配及资本公积转增股本实施公告',
    '关于2026年中期权益分派实施的公告',
    '2020年年度A股权益分派实施公告的更正公告',
    '2020年年度分红派息实施公告',
])
def test_real_title_variants_remain_candidates(title):assert implementation_title(title)


def test_all_pages_and_correction_are_retained_without_admission(tmp_path):
    pages=[dict(totalAnnouncement=2,hasMore=True,announcements=[entry()]),dict(totalAnnouncement=2,hasMore=False,announcements=[entry('1211130231','权益分派实施公告的更正公告')])]
    proof=invoke(tmp_path,transport(pages),page_size=1)
    assert proof['search_complete'] and proof['query_rows']==2 and len(proof['notices'])==2
    assert proof['reviewed'] is False and proof['candidates_applied'] is False
    assert len(proof['query_pages'])==2 and len(proof['input_hashes'])==5
    assert proof['schema']=='original_candidate_notice_capture_v1'
    assert (tmp_path/'work/capture/1211130230.PDF').read_bytes()==PDF


@pytest.mark.parametrize('kind',['duplicate','changed_total','truncated','wrong_flag','wrong_issuer','invalid_total','invalid_flag','malformed_announcement','malformed_title'])
def test_incomplete_or_wrong_identity_query_cannot_look_complete(tmp_path,kind):
    pages=[dict(totalAnnouncement=2,hasMore=True,announcements=[entry()]),dict(totalAnnouncement=2,hasMore=False,announcements=[entry('1211130231')])]
    if kind=='duplicate':pages[1]['announcements']=[entry()]
    if kind=='changed_total':pages[1]['totalAnnouncement']=3
    if kind=='truncated':pages[1]['announcements']=[]
    if kind=='wrong_flag':pages[0]['hasMore']=False
    if kind=='wrong_issuer':pages[0]['announcements'][0]['secCode']='000952'
    if kind=='invalid_total':pages[0]['totalAnnouncement']=True
    if kind=='invalid_flag':pages[0]['hasMore']='true'
    if kind=='malformed_announcement':pages[0]['announcements'][0]['announcementId']='../escape'
    if kind=='malformed_title':pages[0]['announcements'][0]['announcementTitle']=None
    with pytest.raises(ValueError):invoke(tmp_path,transport(pages),page_size=1)
    saved=json.loads((tmp_path/'work/capture/manifest.json').read_text(encoding='utf8'))
    assert saved['status']=='failed' and not saved['search_complete'] and not saved['candidates_applied']


@pytest.mark.parametrize('fault',['redirect','offsite_pdf','not_pdf','oversized_pdf','ambiguous_issuer','lookup_wrong_code'])
def test_unsafe_or_invalid_sources_are_rejected_and_saved_failed(tmp_path,fault):
    row=entry();pages=[dict(totalAnnouncement=1,hasMore=False,announcements=[row])];issuer=None
    if fault=='offsite_pdf':row['adjunctUrl']='https://untrusted.invalid/1211130230.PDF'
    if fault=='ambiguous_issuer':issuer=[dict(code='000951',orgId='a'),dict(code='000951',orgId='b')]
    if fault=='lookup_wrong_code':issuer=[dict(code='000952',orgId='a')]
    def change(url,data,body):
        if data is None:
            if fault=='redirect':return body,'https://untrusted.invalid/file.PDF'
            if fault=='not_pdf':return b'<html>temporary unavailable</html>',url
            if fault=='oversized_pdf':return b'x'*100,url
        return body,url
    with pytest.raises(ValueError):invoke(tmp_path,transport(pages,issuer,change),max_pdf_bytes=80)
    proof=json.loads((tmp_path/'work/capture/manifest.json').read_text(encoding='utf8'));assert proof['status']=='failed'


def test_empty_query_is_explicit_and_not_certified_absence(tmp_path):
    result=invoke(tmp_path,transport([dict(totalAnnouncement=0,hasMore=False,announcements=None)]))
    assert result['status']=='no_implementation_notice_found' and result['search_complete'] and not result['notices']
    assert any('absence' in s for s in result['limitations'])


def test_non_distribution_implementation_notice_is_not_a_candidate(tmp_path):
    result=invoke(tmp_path,transport([dict(totalAnnouncement=1,hasMore=False,announcements=[entry(title='股权激励实施公告')])]))
    assert result['query_rows']==1 and result['notices']==[]


@pytest.mark.parametrize('limit',['pages','documents'])
def test_resource_bounds_fail_instead_of_returning_partial_success(tmp_path,limit):
    pages=[dict(totalAnnouncement=2,hasMore=True,announcements=[entry()]),dict(totalAnnouncement=2,hasMore=False,announcements=[entry('1211130231')])]
    args=dict(page_size=1)
    args['max_pages' if limit=='pages' else 'max_notices']=1
    with pytest.raises(ValueError):invoke(tmp_path,transport(pages),**args)
    result=json.loads((tmp_path/'work/capture/manifest.json').read_text(encoding='utf8'));assert not result['search_complete'] and result['status']=='failed'


def test_existing_receipt_is_never_overwritten(tmp_path):
    fetch=transport([dict(totalAnnouncement=0,hasMore=False,announcements=[])])
    invoke(tmp_path,fetch);p=tmp_path/'work/capture/manifest.json';before=p.read_bytes()
    with pytest.raises(ValueError):invoke(tmp_path,fetch)
    assert p.read_bytes()==before


def test_notice_hash_tampering_during_query_is_detected(tmp_path):
    pages=[dict(totalAnnouncement=2,hasMore=True,announcements=[entry()]),dict(totalAnnouncement=2,hasMore=False,announcements=[entry('1211130231')])]
    def change(url,data,body):
        if '1211130231.PDF' in url:(tmp_path/'work/capture/1211130230.PDF').write_bytes(b'%PDF-1.4 tampered')
        return body,url
    with pytest.raises(ValueError,match='changed during acquisition'):invoke(tmp_path,transport(pages,change=change),page_size=1)

    proof=json.loads((tmp_path/'work/capture/manifest.json').read_text(encoding='utf8'));assert proof['status']=='failed' and not proof['search_complete']


def test_new_capture_contract_loads_in_existing_reviewed_listing_book(tmp_path):
    from datetime import datetime,timezone
    import hashlib
    import pandas as pd
    from ashare_mvp.ordinary_share_settlement import OrdinaryListingBook
    proof=invoke(tmp_path,transport([dict(totalAnnouncement=1,hasMore=False,announcements=[entry()])]))
    project=tmp_path/'project';work=tmp_path/'work';manifest=work/'capture/manifest.json';notice=proof['notices'][0]
    row=dict(symbol='sz.000951',record_date='2021-09-28',ex_date='2021-09-29',tradable_from='2021-09-29',announcement_date='2021-09-23',available_date='2021-09-24',expected_cash_per_share=0.,expected_share_multiplier=1.4,reviewed=True,source_file='@work/capture/1211130230.PDF',source_capture_file='@work/capture/manifest.json',source_url=notice['url'],source_sha256=notice['sha256'],source_capture_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),observed_at_utc=notice['observed_at_utc'],reviewed_at_utc=datetime.now(timezone.utc).isoformat(),parameter_pages={k:[1] for k in ['record_date','ex_date','tradable_from','share_multiplier','cash_per_share']})
    (project/'review.json').write_text(json.dumps([row]),encoding='utf8')
    book=OrdinaryListingBook('review.json',project,work,pd.bdate_range('2021-09-20','2021-10-01'))
    book.verify_sources();assert len(book.events)==1

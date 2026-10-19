from pathlib import Path
from urllib.parse import urlsplit,parse_qs
import hashlib,json
import pandas as pd
from ashare_mvp.universe_evidence import reviewed_non_listing_bounds


def test_reviewed_sse_nonlisting_records_match_positive_source_snapshots():
    root=Path(__file__).resolve().parents[1]
    e=json.loads((root/'config/reviewed_non_listing_bounds.json').read_text(encoding='utf8'))
    selected=[x for x in e if x['symbol'] in ['sh.603302','sh.603361','sh.688688']]
    assert len(selected)==3
    for record in selected:
        raw=(root/'config'/record['source_response']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==record['source_sha256']
        source=json.loads(raw);assert not source['actionErrors'] and not source['fieldErrors']
        assert len(source['result'])==1 and source['pageHelp']['total']==1
        row=source['result'][0]
        assert row['SECURITY_CODE']==record['symbol'][3:] and row['IPO_OVERALL_STATUS']=='99' and row['LISTED_DATE']=='-'
        query=parse_qs(urlsplit(record['source_url']).query)
        assert query['sqlId']==['COMMON_SSE_IPO_IPO_LIST_L'] and query['isIssue']==['1'] and query['stockCode']==[row['SECURITY_CODE']]
    master=pd.DataFrame({'code':[x['symbol'] for x in selected],'ipoDate':[None]*3})
    excluded,_=reviewed_non_listing_bounds(pd.DataFrame(selected),'2025-12-31',master.code,master)
    assert excluded==set(master.code)
    excluded,_=reviewed_non_listing_bounds(pd.DataFrame(selected),'2027-01-01',master.code,master)
    assert not excluded # evidence is bounded, not a permanent never-list assumption


def test_sse_positive_listed_control_is_not_classified_as_pending_ipo():
    root=Path(__file__).resolve().parents[1]/'config/evidence/sse_ipo_20261019'
    for name in ['listed_control','listed_control_issuing']:
        source=json.loads((root/(name+'_source.json')).read_text(encoding='utf8'))
        assert hashlib.sha256((root/(name+'.json')).read_bytes()).hexdigest()==source['sha256']
    listed=json.loads((root/'listed_control.json').read_text(encoding='utf8'))['result']
    assert len(listed)==1 and listed[0]['IPO_OVERALL_STATUS']=='2' and listed[0]['LISTED_DATE'] not in ['',None,'-']
    assert json.loads((root/'listed_control_issuing.json').read_text(encoding='utf8'))['result']==[]

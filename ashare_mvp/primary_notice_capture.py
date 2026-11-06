"""Capture bounded issuer notice candidates, never admit account events.

Searches include alternative implementation titles and correction notices.
An empty result is an observed query result, never proof of no corporate action.
"""
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen
import argparse
import hashlib
import json
import re
import pandas as pd
from .phase2_protocol import day
from .workspace_paths import WorkspacePaths
from .public_data import is_ashare


def implementation_title(title):
    text=re.sub(r'<[^>]+>','',title)
    return '实施' in text and any(term in text for term in ['权益分派','权益分配','分红派息','利润分配','资本公积','转增股本'])


def fetch_public(url, data, maximum):
    request=Request(url,data=data,headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.cninfo.com.cn/','Content-Type':'application/x-www-form-urlencoded; charset=UTF-8'})
    with urlopen(request,timeout=20) as response:
        body=response.read(maximum+1);final=response.url
    return body,final


def capture(project,work_root,code,ex_date,out,keyword='实施',window_days=45,page_size=30,max_pages=20,max_pdf_bytes=20*1024*1024,max_notices=20,fetch=None):
    """Keep originals and actual observation clocks in a fresh external folder.

    Provider totals and hasMore must agree across all pages. Partial, duplicate,
    changing and off-issuer results fail with an explicit saved failed receipt.
    The existing original_candidate_notice_capture_v1 source contract is kept.
    """
    symbol=('sh.' if isinstance(code,str) and code.startswith('6') else 'sz.')+str(code)
    if not isinstance(code,str) or not re.fullmatch(r'\d{6}',code) or not is_ashare(symbol):raise ValueError('ordinary Shanghai/Shenzhen A-share code required')
    ex=day(ex_date)
    for name,value,upper in [('window_days',window_days,366),('page_size',page_size,30),('max_pages',max_pages,100),('max_pdf_bytes',max_pdf_bytes,50*1024*1024),('max_notices',max_notices,50)]:
        if type(value) is not int or not 1<=value<=upper:raise ValueError('bounded positive '+name+' required')
    if not isinstance(keyword,str) or not keyword.strip() or len(keyword)>40:raise ValueError('bounded nonempty notice keyword required')
    paths=WorkspacePaths(project,work_root);target=paths.resolve(out)
    if not target.is_relative_to(paths.work) or target.exists():raise ValueError('fresh external notice directory required')
    target.mkdir(parents=True)
    transport=fetch or fetch_public
    proof=dict(schema='original_candidate_notice_capture_v1',status='capturing',code=code,ex_date=str(ex.date()),started_at_utc=datetime.now(timezone.utc).isoformat(),notices=[],reviewed=False,query_pages=[],input_hashes={},candidates_applied=False,search_complete=False,
               limitations=['Raw implementation candidates require independent primary-page review; not account events.','Empty query results do not certify absence; original historical byte vintages are not verified.'])
    def save():
        tmp=target/'manifest.pending.json';tmp.write_text(json.dumps(proof,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8');tmp.replace(target/'manifest.json')
    def retrieve(url,data,name,maximum):
        body,final=transport(url,data,maximum)
        if not isinstance(body,bytes) or len(body)>maximum:raise ValueError('source exceeds bounded response size')
        expected=urlparse(url);actual=urlparse(final)
        if actual.scheme!='https' or actual.hostname!=expected.hostname:raise ValueError('off-host source redirect')
        file=target/name
        with file.open('xb') as stream:stream.write(body)
        meta=dict(url=url,response_url=final,file=name,sha256=hashlib.sha256(body).hexdigest(),bytes=len(body),observed_at_utc=datetime.now(timezone.utc).isoformat())
        proof['input_hashes'][name]=meta['sha256'];return body,meta
    def post(url,payload,name):
        body,meta=retrieve(url,urlencode(payload).encode(),name,5*1024*1024)
        return json.loads(body),meta
    save()
    try:
        top,meta=post('https://www.cninfo.com.cn/new/information/topSearch/query',{'keyWord':code,'maxNum':10},'issuer_search.json');proof['issuer_search']=meta
        if not isinstance(top,list):raise ValueError('issuer lookup must be a list')
        issuers=[r for r in top if isinstance(r,dict) and r.get('code')==code]
        if len(issuers)!=1 or not isinstance(issuers[0].get('orgId'),str) or not issuers[0]['orgId']:raise ValueError('unique exact issuer lookup required')
        issuer=issuers[0];seen=set();total=None;rows_read=0
        for page in range(1,max_pages+1):
            payload=dict(pageNum=page,pageSize=page_size,column='sse' if code.startswith('6') else 'szse',tabName='fulltext',stock=code+','+issuer['orgId'],searchkey=keyword,secid='',category='',trade='',seDate=str((ex-pd.Timedelta(days=window_days)).date())+'~'+str((ex+pd.Timedelta(days=window_days)).date()),sortName='',sortType='',isHLtitle='true')
            result,meta=post('https://www.cninfo.com.cn/new/hisAnnouncement/query',payload,f'announcement_search_{page:03}.json')
            proof['query_pages'].append(dict(payload=payload,**meta));save()
            if not isinstance(result,dict) or type(result.get('totalAnnouncement')) is not int or result['totalAnnouncement']<0 or type(result.get('hasMore')) is not bool:raise ValueError('explicit query total and pagination flag required')
            if total is None:total=result['totalAnnouncement']
            elif total!=result['totalAnnouncement']:raise ValueError('notice query changed during pagination')
            entries=result.get('announcements')
            if entries is None and total==0:entries=[]
            if not isinstance(entries,list) or len(entries)>page_size or not entries and total>rows_read:raise ValueError('missing or oversized query page')
            for row in entries:
                if not isinstance(row,dict) or row.get('secCode')!=code:raise ValueError('query security identity differs')
                identifier=str(row.get('announcementId',''))
                if not re.fullmatch(r'\d{1,40}',identifier) or identifier in seen:raise ValueError('duplicate or invalid announcement identity')
                seen.add(identifier);rows_read+=1
                title=row.get('announcementTitle')
                if not isinstance(title,str):raise ValueError('notice title must be explicit')
                if not implementation_title(title):continue
                if len(proof['notices'])>=max_notices:raise ValueError('notice candidates exceed explicit document bound')
                url=urljoin('https://static.cninfo.com.cn/',row.get('adjunctUrl',''))
                if not re.fullmatch(r'https://static\.cninfo\.com\.cn/finalpage/\d{4}-\d{2}-\d{2}/'+identifier+r'\.PDF',url,re.I):raise ValueError('official original PDF identity required')
                body,notice=retrieve(url,None,identifier+'.PDF',max_pdf_bytes)
                if not body.startswith(b'%PDF-'):raise ValueError('notice response is not PDF')
                stamp=row.get('announcementTime')
                if type(stamp) is not int:raise ValueError('announcement publication timestamp required')
                notice.update(title=re.sub(r'<[^>]+>','',title),source_announcement_time=stamp)
                proof['notices'].append(notice);save()
            if rows_read>total or result['hasMore']!=(rows_read<total):raise ValueError('notice query pagination does not reconcile')
            if not result['hasMore']:
                proof.update(status='raw_captured_review_pending' if proof['notices'] else 'no_implementation_notice_found',completed_at_utc=datetime.now(timezone.utc).isoformat(),search_complete=True,query_rows=rows_read,reported_query_rows=total)
                for name,digest in proof['input_hashes'].items():
                    if hashlib.sha256((target/name).read_bytes()).hexdigest()!=digest:raise ValueError('captured source changed during acquisition')
                save();return proof
        raise ValueError('notice pagination exceeds explicit page bound')
    except BaseException as error:
        proof.update(status='failed',search_complete=False,error=type(error).__name__+': '+str(error),completed_at_utc=datetime.now(timezone.utc).isoformat());save();raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project',default='.')
    for name in ['work-root','code','ex-date','out']:parser.add_argument('--'+name,required=True)
    parser.add_argument('--keyword',default='实施');parser.add_argument('--max-pages',type=int,default=20)
    parser.add_argument('--max-notices',type=int,default=20);parser.add_argument('--max-pdf-bytes',type=int,default=20*1024*1024)
    result=capture(**vars(parser.parse_args()))
    print(json.dumps(dict(status=result['status'],notices=len(result['notices']),search_complete=result['search_complete'],query_rows=result['query_rows'],candidates_applied=False),ensure_ascii=False))

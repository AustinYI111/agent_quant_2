"""GitHub private Release backups using the user's existing Git credential login."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import subprocess
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b''): value.update(block)
    return value.hexdigest()


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != 'https' or urlsplit(newurl).hostname not in ['api.github.com','release-assets.githubusercontent.com','objects.githubusercontent.com']:
            raise ValueError('unexpected GitHub asset redirect')
        redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
        redirected.remove_header('Authorization')
        return redirected


class GitHub:
    def __init__(self, owner):
        if not re.fullmatch(r'[A-Za-z0-9-]+',owner): raise ValueError('invalid GitHub owner')
        self.owner=owner
        env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='never')
        result=subprocess.run(['git','credential','fill'],input=f'protocol=https\nhost=github.com\nusername={owner}\n\n',
                              capture_output=True,text=True,env=env,check=False)
        if result.returncode: raise ValueError('GitHub login unavailable; use Git Credential Manager login first')
        fields=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
        self._token=fields.get('password')
        if not self._token: raise ValueError('GitHub credential missing')
        if self.request('GET','/user')['login'].lower()!=owner.lower(): raise ValueError('GitHub account differs from requested owner')

    def request(self, method, path, body=None, asset=None):
        host='https://uploads.github.com' if asset else 'https://api.github.com'
        headers={'Authorization':'Bearer '+self._token,'Accept':'application/vnd.github+json',
                 'User-Agent':'Personal-Quant-Research','Content-Type':'application/json','X-GitHub-Api-Version':'2022-11-28'}
        data=json.dumps(body).encode() if body is not None else None
        if asset:
            with Path(asset).open('rb') as stream:
                headers.update({'Content-Type':'application/octet-stream','Content-Length':str(Path(asset).stat().st_size)})
                with urlopen(Request(host+path,data=stream,headers=headers,method=method),timeout=60) as response:
                    return json.load(response)
        with urlopen(Request(host+path,data=data,headers=headers,method=method),timeout=60) as response:
            return json.load(response)

    def download(self, path, out):
        headers={'Authorization':'Bearer '+self._token,'Accept':'application/octet-stream','User-Agent':'Personal-Quant-Research'}
        with build_opener(SafeRedirect()).open(Request('https://api.github.com'+path,headers=headers),timeout=60) as response,Path(out).open('xb') as writer:
            for block in iter(lambda:response.read(8*1024*1024),b''): writer.write(block)


def private_repo(client, repository, create=False):
    if not re.fullmatch(re.escape(client.owner)+r'/[A-Za-z0-9._-]+',repository): raise ValueError('backup repository must belong to authenticated owner')
    try: repo=client.request('GET','/repos/'+repository)
    except HTTPError as exc:
        if exc.code!=404 or not create: raise
        repo=client.request('POST','/user/repos',dict(name=repository.split('/')[1],private=True,auto_init=True,
                            description='Encrypted private snapshots for Personal Quant Research. No database commits.'))
    if repo.get('private') is not True or repo.get('full_name','').lower()!=repository.lower():
        raise ValueError('backup destination is not verified private; upload refused')
    return repo


def upload_backup(client, repository, archive, tag):
    private_repo(client,repository)
    archive=Path(archive)
    with archive.open('rb') as stream: header=stream.read(8)
    if archive.suffix!='.pqr' or header!=b'PQRBACK1': raise ValueError('only encrypted PQR snapshots may be uploaded')
    if not re.fullmatch(r'backup-[A-Za-z0-9._-]+',tag): raise ValueError('backup-* tag required')
    if archive.stat().st_size>=2*1024**3: raise ValueError('split archive into private assets under 2 GiB before upload')
    try: release=client.request('GET','/repos/'+repository+'/releases/tags/'+quote(tag,safe=''))
    except HTTPError as exc:
        if exc.code!=404: raise
        release=client.request('POST','/repos/'+repository+'/releases',dict(tag_name=tag,name=tag,draft=False,
                               body='Encrypted private snapshot. Password is stored separately. Verify SHA-256 before restoring.'))
    existing=next((a for a in release.get('assets',[]) if a['name']==archive.name),None)
    checksum=digest(archive)
    if existing:
        if existing.get('digest')!='sha256:'+checksum: raise ValueError('existing backup asset differs; use a new version tag')
        return dict(repository=repository,tag=tag,asset=existing['name'],sha256=checksum,url=release['html_url'])
    asset=client.request('POST','/repos/'+repository+'/releases/'+str(release['id'])+'/assets?name='+quote(archive.name,safe=''),asset=archive)
    if asset.get('size')!=archive.stat().st_size or asset.get('digest')!='sha256:'+checksum:
        raise ValueError('uploaded backup digest/size verification failed')
    return dict(repository=repository,tag=tag,asset=asset['name'],sha256=checksum,url=release['html_url'])


def download_backup(client, repository, tag, asset_name, out):
    private_repo(client,repository)
    release=client.request('GET','/repos/'+repository+'/releases/tags/'+quote(tag,safe=''))
    asset=next(a for a in release['assets'] if a['name']==asset_name)
    target=Path(out)
    if target.exists():
        if target.stat().st_size == asset['size'] and 'sha256:'+digest(target) == asset.get('digest'):
            return dict(file=str(target),sha256=digest(target),verified=True,cached=True)
        raise ValueError('download output exists with different content')
    target.parent.mkdir(parents=True,exist_ok=True)
    try:
        client.download('/repos/'+repository+'/releases/assets/'+str(asset['id']),target)
        if 'sha256:'+digest(target)!=asset.get('digest') or target.stat().st_size!=asset['size']:
            raise ValueError('downloaded backup checksum/size mismatch')
    except Exception:
        target.unlink(missing_ok=True); raise
    return dict(file=str(target),sha256=digest(target),verified=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository',required=True)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('create')
    upload=sub.add_parser('upload'); upload.add_argument('--archive',required=True); upload.add_argument('--tag',required=True)
    download=sub.add_parser('download'); download.add_argument('--tag',required=True); download.add_argument('--asset',required=True); download.add_argument('--out',required=True)
    args=parser.parse_args(); client=GitHub(args.repository.split('/')[0])
    if args.command=='create':
        repo=private_repo(client,args.repository,True); result={'repository':repo['full_name'],'private':repo['private'],'url':repo['html_url']}
    elif args.command=='upload': result=upload_backup(client,args.repository,args.archive,args.tag)
    else: result=download_backup(client,args.repository,args.tag,args.asset,args.out)
    print(json.dumps(result))

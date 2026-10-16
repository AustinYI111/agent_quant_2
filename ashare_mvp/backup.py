"""Private, chunk-encrypted portable snapshots. No network upload is performed."""
from pathlib import Path, PurePosixPath
import argparse
import getpass
import hashlib
import json
import os
import struct
import tempfile
import zipfile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

CHUNK = 8 * 1024 * 1024
MAGIC = b"PQRBACK1"


def cipher(password, salt):
    if len(password) < 12: raise ValueError("backup password needs at least 12 characters")
    return AESGCM(PBKDF2HMAC(algorithm=hashes.SHA256(),length=32,salt=salt,iterations=600000).derive(password.encode()))


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(CHUNK),b''): value.update(block)
    return value.hexdigest()


def crypt(source, target, password, decrypt=False):
    with Path(source).open('rb') as reader, Path(target).open('xb') as writer:
        header=reader.read(32) if decrypt else MAGIC+os.urandom(16)+os.urandom(8)
        if len(header)!=32 or not header.startswith(MAGIC): raise ValueError('invalid backup header')
        aes=cipher(password,header[8:24])
        if not decrypt: writer.write(header)
        index=0
        while True:
            counter=struct.pack('>I',index); nonce=header[24:]+counter
            if decrypt:
                size=reader.read(4)
                if len(size)!=4: raise ValueError('truncated encrypted backup')
                size=struct.unpack('>I',size)[0]
                if not 16<=size<=CHUNK+16: raise ValueError('invalid encrypted frame')
                block=reader.read(size)
                if len(block)!=size: raise ValueError('truncated encrypted backup')
                block=aes.decrypt(nonce,block,header+counter)
                if not block:
                    if reader.read(1): raise ValueError('trailing backup data')
                    break
                writer.write(block)
            else:
                block=reader.read(CHUNK); encrypted=aes.encrypt(nonce,block,header+counter)
                writer.write(struct.pack('>I',len(encrypted))); writer.write(encrypted)
                if not block: break
            index+=1


def backup(root, includes, out, password):
    root, output=Path(root).resolve(),Path(out).resolve()
    if output.exists(): raise ValueError('backup output exists')
    files={}
    for item in includes:
        selected=(root/item).resolve()
        if not selected.is_relative_to(root) or not selected.exists(): raise ValueError('backup includes must exist inside project')
        for file in ([selected] if selected.is_file() else selected.rglob('*')):
            if file.is_symlink(): raise ValueError('backup symlinks unsupported')
            if not file.is_file(): continue
            relative=file.relative_to(root)
            if any(p in ['.git','.venv','.aws'] or p.startswith('.env') for p in relative.parts) or file.suffix in ['.pem','.key'] or file.name in ['backup-password.txt','backup-key.txt']:
                raise ValueError('credential or environment paths cannot be backed up')
            if file.resolve()==output: raise ValueError('output cannot be a backup input')
            files[relative.as_posix()]=file
    if not files: raise ValueError('backup selection empty')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pqr-private-",dir=output.parent) as temporary:
        archive=Path(temporary)/'private.zip'; manifest={}
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,allowZip64=True) as zipped:
            for name,file in sorted(files.items()):
                before=sha(file); written=hashlib.sha256()
                with file.open("rb") as reader,zipped.open(name,"w",force_zip64=True) as writer:
                    for block in iter(lambda:reader.read(CHUNK),b""):
                        written.update(block); writer.write(block)
                if written.hexdigest()!=before or sha(file)!=before: raise ValueError('source changed during backup; use sealed snapshots')
                manifest[name]=dict(sha256=before,size=file.stat().st_size)
            for name,file in files.items():
                if sha(file)!=manifest[name]['sha256']: raise ValueError('source changed during backup')
            zipped.writestr('_backup_manifest.json',json.dumps(dict(schema='pqr_backup_v1',files=manifest)))
        try: crypt(archive,output,password)
        except Exception:
            output.unlink(missing_ok=True); raise
    return dict(files=len(files),archive_sha256=sha(output),encrypted=True)


def restore(archive, out, password):
    target=Path(out).resolve()
    if target.exists() and any(target.iterdir()): raise ValueError('restore output must be empty')
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pqr-private-",dir=target.parent) as temporary:
        plain=Path(temporary)/'private.zip'; crypt(archive,plain,password,decrypt=True)
        with zipfile.ZipFile(plain) as zipped:
            names=zipped.namelist()
            if len(names)!=len(set(names)): raise ValueError('duplicate backup paths')
            meta=json.loads(zipped.read('_backup_manifest.json'))
            if meta.get('schema')!='pqr_backup_v1' or set(names)!=set(meta['files'])|{'_backup_manifest.json'}: raise ValueError('invalid backup manifest')
            staging=Path(temporary)/'verified'; staging.mkdir()
            for name,info in meta['files'].items():
                path=PurePosixPath(name)
                if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name: raise ValueError('unsafe backup path')
                file=(staging/name).resolve()
                if not file.is_relative_to(staging): raise ValueError('unsafe backup path')
                if zipped.getinfo(name).file_size!=info['size']: raise ValueError('backup size mismatch')
                file.parent.mkdir(parents=True,exist_ok=True)
                with zipped.open(name) as reader,file.open('xb') as writer:
                    for block in iter(lambda:reader.read(CHUNK),b''): writer.write(block)
                if sha(file)!=info['sha256']: raise ValueError('backup checksum mismatch')
            import shutil
            target.mkdir(parents=True,exist_ok=True)
            shutil.copytree(staging,target,dirs_exist_ok=True)
    return dict(restored_files=len(meta['files']),verified=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    create=sub.add_parser('backup'); create.add_argument('--root',default='.'); create.add_argument('--include',nargs='+',required=True); create.add_argument('--out',required=True)
    recover=sub.add_parser('restore'); recover.add_argument('--archive',required=True); recover.add_argument('--out',required=True)
    args=parser.parse_args(); password=getpass.getpass('Private backup password: ')
    if args.command=='backup':
        if password!=getpass.getpass('Confirm password: '): raise ValueError('passwords differ')
        result=backup(args.root,args.include,args.out,password)
    else: result=restore(args.archive,args.out,password)
    print(json.dumps(result))

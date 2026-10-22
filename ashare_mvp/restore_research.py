"""Restore verified base + current research state into a clone without overwrites."""
from pathlib import Path
import argparse,getpass,json,re,shutil,tempfile
from .backup import restore,sha
from .github_backup import GitHub,download_backup


def restore_artifact_layers(archives,project,password):
    original=Path(project)
    if original.is_symlink():raise ValueError('research project cannot be a symlink')
    root=original.resolve()
    if not root.is_dir():raise ValueError('existing project directory required')
    archives=[Path(p).resolve() for p in archives]
    if not archives or len(set(archives))!=len(archives):raise ValueError('distinct research archives required')
    digests={str(p):sha(p) for p in archives}
    with tempfile.TemporaryDirectory(prefix='pqr-layers-',dir=root.parent) as temporary:
        files={};temporary=Path(temporary)
        # Authenticate and verify every archive before any installation.
        for index,archive in enumerate(archives):
            part=temporary/str(index);restore(archive,part,password)
            for source in part.rglob('*'):
                if not source.is_file():continue
                relative=source.relative_to(part)
                if relative.parts[0]!='artifacts' or len(relative.parts)<2:
                    raise ValueError('research restore accepts only artifacts; code and credentials stay outside this installer')
                digest=sha(source)
                if relative in files and files[relative][1]!=digest:
                    raise ValueError('conflicting archive layers; use the fixed base plus one current phase2 snapshot')
                files[relative]=(source,digest)
        installs=[];identical=0
        for relative,(source,digest) in files.items():
            destination=root/relative
            for parent in [destination,*destination.parents]:
                if parent==root:break
                if parent.is_symlink():raise ValueError('research destination contains symlink')
                if parent!=destination and parent.exists() and not parent.is_dir():raise ValueError('research destination parent is a file')
            if not destination.resolve().is_relative_to(root):raise ValueError('research destination escapes project')
            if destination.exists():
                if not destination.is_file() or sha(destination)!=digest:
                    raise ValueError('existing research content differs; restore into a fresh clone')
                identical+=1
            else:installs.append((source,destination,digest))
        for archive,digest in digests.items():
            if sha(archive)!=digest:raise ValueError('research archive changed during verification')
        for source,destination,digest in installs:
            destination.parent.mkdir(parents=True,exist_ok=True)
            # Exclusive creation preserves a file created after preflight too.
            with source.open('rb') as reader,destination.open('xb') as writer:
                shutil.copyfileobj(reader,writer,length=8*1024*1024)
            if sha(destination)!=digest:raise ValueError('installed research file checksum differs')
    return dict(schema='verified_research_layers_restore_v1',archives=len(archives),verified_files=len(files),
        installed_files=len(installs),identical_files_preserved=identical,code_or_credentials_modified=False,
        archive_sha256=[digests[str(p)] for p in archives],verified=True)


def restore_cloud_plan(plan,project,password):
    root=Path(project).resolve();p=Path(plan)
    if not p.is_absolute():p=root/p
    recipe=json.loads(p.read_text(encoding='utf8'))
    if recipe.get('schema')!='private_research_restore_plan_v1' or not recipe.get('layers'):
        raise ValueError('explicit verified research restore plan required')
    repository=recipe['repository'];owner=repository.split('/')[0]
    # Validate the complete recipe before authentication or network calls.
    for layer in recipe['layers']:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*\.pqr',layer['asset']) or not re.fullmatch(r'[0-9a-f]{64}',layer['sha256']) or not re.fullmatch(r'backup-[A-Za-z0-9._-]+',layer['tag']):
            raise ValueError('invalid pinned research asset, tag or hash')
    client=GitHub(owner);paths=[]
    for layer in recipe['layers']:
        archive=root/'backups'/layer['asset']
        download_backup(client,repository,layer['tag'],layer['asset'],archive)
        if sha(archive)!=layer['sha256']:raise ValueError('research asset differs from pinned restore recipe')
        paths.append(archive)
    return restore_artifact_layers(paths,root,password)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',default='.')
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--archive',nargs='+');group.add_argument('--plan')
    args=p.parse_args();password=getpass.getpass('Research backup password: ')
    result=(restore_cloud_plan(args.plan,args.project,password) if args.plan
        else restore_artifact_layers(args.archive,args.project,password))
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()

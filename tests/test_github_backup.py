import pytest
from ashare_mvp.github_backup import upload_backup,digest
class Fake:
    owner='owner'
    def __init__(self,private): self.private=private; self.uploads=0
    def request(self,method,path,body=None,asset=None):
        if path=='/repos/owner/backup': return {'private':self.private,'full_name':'owner/backup'}
        if method=='GET': return {'id':1,'assets':[],'html_url':'https://github.com/owner/backup/releases/tag/backup-test'}
        self.uploads+=1
        return {'name':asset.name,'size':asset.stat().st_size,'digest':'sha256:'+digest(asset)}
def test_public_destination_refused_before_upload(tmp_path):
    client=Fake(False)
    with pytest.raises(ValueError,match='verified private'): upload_backup(client,'owner/backup',tmp_path/'private.pqr','backup-test')
    assert client.uploads==0
def test_private_asset_and_plain_database_refused(tmp_path):
    archive=tmp_path/'state.pqr'; archive.write_bytes(b'PQRBACK1'+b'encrypted-test-fixture')
    client=Fake(True); result=upload_backup(client,'owner/backup',archive,'backup-test')
    assert result['sha256']==digest(archive) and client.uploads==1
    plain=tmp_path/'data.csv'; plain.write_bytes(b'plain data')
    with pytest.raises(ValueError,match='encrypted'): upload_backup(client,'owner/backup',plain,'backup-test')
    assert client.uploads==1

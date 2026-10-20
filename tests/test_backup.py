from pathlib import Path
import pytest
from cryptography.exceptions import InvalidTag
from ashare_mvp.backup import backup,restore,crypt

PASSWORD='a-private-test-password'


def test_encrypted_snapshot_roundtrip_and_no_overwrite(tmp_path):
    root=tmp_path/'project'; (root/'private').mkdir(parents=True)
    (root/'private/holdings.csv').write_text('symbol,shares\nS,100\n')
    archive=tmp_path/'backup.pqr'
    result=backup(root,['private'],archive,PASSWORD)
    assert result['encrypted'] and b'holdings.csv' not in archive.read_bytes()
    target=tmp_path/'recovered'
    assert restore(archive,target,PASSWORD)['verified']
    assert (target/'private/holdings.csv').read_bytes()==(root/'private/holdings.csv').read_bytes()
    with pytest.raises(ValueError,match='empty'): restore(archive,target,PASSWORD)


def test_wrong_password_truncation_and_credentials_rejected(tmp_path):
    root=tmp_path/'root'; root.mkdir(); (root/'data.csv').write_text('value\n1\n')
    archive=tmp_path/'backup.pqr'; backup(root,['data.csv'],archive,PASSWORD)
    with pytest.raises(InvalidTag): restore(archive,tmp_path/'bad','another-invalid-password')
    assert not (tmp_path/'bad').exists()
    truncated=tmp_path/'truncated.pqr'; truncated.write_bytes(archive.read_bytes()[:-1])
    with pytest.raises(ValueError,match='truncated'): restore(truncated,tmp_path/'truncated-out',PASSWORD)
    (root/'.env').write_text('secret')
    with pytest.raises(ValueError,match='credential'): backup(root,['.env'],tmp_path/'secret.pqr',PASSWORD)


def test_restore_rejects_path_traversal_before_copy(tmp_path):
    import json,zipfile,hashlib
    raw=tmp_path/'unsafe.zip'
    with zipfile.ZipFile(raw,'w') as z:
        z.writestr('../escape.txt','bad')
        z.writestr('_backup_manifest.json',json.dumps({'schema':'pqr_backup_v1','files':{'../escape.txt':{'size':3,'sha256':hashlib.sha256(b'bad').hexdigest()}}}))
    encrypted=tmp_path/'unsafe.pqr'; crypt(raw,encrypted,PASSWORD)
    with pytest.raises(ValueError,match='unsafe'): restore(encrypted,tmp_path/'restored',PASSWORD)
    assert not (tmp_path/'escape.txt').exists()

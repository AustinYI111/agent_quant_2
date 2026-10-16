import pytest
from ashare_mvp import checkpoint


def test_transient_lock_preserves_previous_checkpoint_until_atomic_replace(tmp_path, monkeypatch):
    target=tmp_path/'manifest.json'; source=tmp_path/'pending.json'
    target.write_text('old'); source.write_text('new')
    original=checkpoint.os.replace; calls=[]
    def locked(src,dst):
        calls.append(1)
        if len(calls)<3:
            assert target.read_text()=='old' and source.read_text()=='new'
            raise PermissionError('sharing conflict')
        original(src,dst)
    monkeypatch.setattr(checkpoint.os,'replace',locked)
    monkeypatch.setattr(checkpoint.time,'sleep',lambda _:None)
    checkpoint.replace_checkpoint(source,target)
    assert target.read_text()=='new' and not source.exists() and len(calls)==3


def test_permanent_permission_error_is_bounded_and_keeps_recovery_file(tmp_path,monkeypatch):
    source=tmp_path/'pending.json'; source.write_text('recoverable'); calls=[]
    def denied(*args):
        calls.append(1); raise PermissionError('denied')
    monkeypatch.setattr(checkpoint.os,'replace',denied)
    monkeypatch.setattr(checkpoint.time,'sleep',lambda _:None)
    with pytest.raises(PermissionError): checkpoint.replace_checkpoint(source,tmp_path/'manifest.json')
    assert len(calls)==6 and source.read_text()=='recoverable'

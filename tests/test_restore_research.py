import json
import pytest
from ashare_mvp.backup import backup
from ashare_mvp.restore_research import restore_artifact_layers,restore_cloud_plan


def archive(tmp_path,name,files):
    source=tmp_path/(name+'-source');source.mkdir()
    for relative,value in files.items():
        p=source/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value,encoding='utf8')
    out=tmp_path/(name+'.pqr')
    backup(source,list(files),out,'fixture-only-password')
    return out


def test_base_and_current_research_restore_into_clone_without_touching_code(tmp_path):
    a=archive(tmp_path,'base',{'artifacts/base/model.txt':'original-model'})
    b=archive(tmp_path,'phase2',{'artifacts/phase2/model.txt':'candidate-model'})
    clone=tmp_path/'clone';clone.mkdir();(clone/'README.md').write_text('current source')
    proof=restore_artifact_layers([a,b],clone,'fixture-only-password')
    assert proof['verified_files']==2 and proof['installed_files']==2
    assert (clone/'README.md').read_text()=='current source'
    assert (clone/'artifacts/phase2/model.txt').read_text()=='candidate-model'
    again=restore_artifact_layers([a,b],clone,'fixture-only-password')
    assert again['installed_files']==0 and again['identical_files_preserved']==2


def test_conflicting_layers_fail_before_any_project_file_is_written(tmp_path):
    a=archive(tmp_path,'base',{'artifacts/base/model.txt':'one'})
    b=archive(tmp_path,'changed',{'artifacts/base/model.txt':'two'})
    clone=tmp_path/'clone';clone.mkdir()
    with pytest.raises(ValueError,match='conflicting archive layers'):
        restore_artifact_layers([a,b],clone,'fixture-only-password')
    assert not (clone/'artifacts').exists()


def test_existing_different_state_is_preserved_and_preflight_is_complete(tmp_path):
    a=archive(tmp_path,'base',{'artifacts/new.txt':'new','artifacts/existing.txt':'replacement'})
    clone=tmp_path/'clone';(clone/'artifacts').mkdir(parents=True)
    (clone/'artifacts/existing.txt').write_text('user state')
    with pytest.raises(ValueError,match='existing research content differs'):
        restore_artifact_layers([a],clone,'fixture-only-password')
    assert not (clone/'artifacts/new.txt').exists()
    assert (clone/'artifacts/existing.txt').read_text()=='user state'


def test_archive_outside_research_scope_cannot_install_source_code(tmp_path):
    a=archive(tmp_path,'source',{'app.py':'unwanted source'})
    clone=tmp_path/'clone';clone.mkdir()
    with pytest.raises(ValueError,match='only artifacts'):
        restore_artifact_layers([a],clone,'fixture-only-password')
    assert not (clone/'app.py').exists()


def test_malicious_cloud_asset_path_is_rejected_before_authentication(tmp_path,monkeypatch):
    from ashare_mvp import restore_research
    monkeypatch.setattr(restore_research,'GitHub',lambda *a:pytest.fail('authentication must not run'))
    plan=dict(schema='private_research_restore_plan_v1',repository='owner/private',layers=[dict(tag='backup-fixture',asset='../state.pqr',sha256='a'*64)])
    (tmp_path/'plan.json').write_text(json.dumps(plan))
    with pytest.raises(ValueError,match='invalid pinned'):
        restore_cloud_plan('plan.json',tmp_path,'fixture-only-password')

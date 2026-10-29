import json,shutil
from pathlib import Path
import pandas as pd
import pytest

from test_prediction_ledger import arguments
from test_prediction_archive import snapshots,counts
from test_paper_tracking import book
from ashare_mvp import prediction_ledger as old
from ashare_mvp import workspace_prediction_ledger as ledger
from ashare_mvp.workspace_paths import WorkspacePaths
from ashare_mvp.workspace_prediction_archive import archive_daily_snapshot
from ashare_mvp.frozen_daily_workspace import run_frozen_daily
from ashare_mvp.phase2_protocol import sha256


def external(root):
    work=root.parent/(root.name+'_external');work.mkdir()
    return work


@pytest.mark.parametrize('name',['../escape','@work/../escape','Z:/escape','/private','@work','@work/a\\b',''])
def test_escape_and_ambiguous_paths_rejected(tmp_path,name):
    paths=WorkspacePaths(tmp_path,external(tmp_path))
    with pytest.raises(ValueError):paths.resolve(name)


def test_paths_roundtrip_and_roots_do_not_depend_on_drive_name(tmp_path):
    work=external(tmp_path);paths=WorkspacePaths(tmp_path,work)
    assert paths.resolve('@work/data/panel.csv')==work/'data/panel.csv'
    assert paths.name(work/'data/panel.csv')=='@work/data/panel.csv'
    assert paths.name(tmp_path/'models/model.txt')=='models/model.txt'
    with pytest.raises(ValueError):WorkspacePaths(tmp_path,tmp_path/'nested')
    with pytest.raises(ValueError):paths.name(tmp_path.parent/'outside')


def test_project_only_prediction_contract_matches_original_exactly(arguments,monkeypatch):
    monkeypatch.setattr(ledger,'_now',old._now)
    work=external(arguments['project'])
    expected=old.record_predictions(**arguments)
    actual=ledger.record_predictions(**dict(arguments,database=work/'predictions.sqlite'),work_root=work)
    assert actual==expected
    pd.testing.assert_frame_equal(old.read_predictions(arguments['database'],actual['batch_id'])[1],
                                  old.read_predictions(work/'predictions.sqlite',actual['batch_id'])[1],check_exact=True)


def test_external_feature_hash_visibility_and_immutable_retry(arguments,monkeypatch):
    monkeypatch.setattr(ledger,'_now',old._now)
    work=external(arguments['project']);(work/'features.csv').write_text('synthetic external features')
    arguments.update(database=work/'predictions.sqlite',sources=[dict(path='@work/features.csv',sha256=sha256(work/'features.csv'),observed_at_utc='2026-10-20T09:30:00Z')])
    result=ledger.record_predictions(**arguments,work_root=work)
    assert result['sources'][0]['path']=='@work/features.csv'
    assert ledger.record_predictions(**arguments,work_root=work)==result
    arguments['scores'].loc[0,'score']=999
    with pytest.raises(ValueError,match='revised'):ledger.record_predictions(**arguments,work_root=work)
    (work/'features.csv').write_text('tampered')
    with pytest.raises(ValueError,match='hash'):ledger.record_predictions(**arguments,work_root=work)


def test_external_archive_preserves_receipts_and_idempotency(snapshots):
    args,_,create,_=snapshots;source,m=create('original');work=external(args['project'])
    shutil.copytree(source,work/'snapshot');source=work/'snapshot'
    target=work/'archive/predictions.sqlite'
    def ingest():return archive_daily_snapshot(source,sha256(source/'manifest.json'),target,args['project'],args['seal_directory'],args['calendar'],work_root=work)
    result=ingest();before=sha256(target)
    assert result['new_batches']==3 and counts(target)==(3,60,1)
    assert ingest()==result and sha256(target)==before
    for receipt in m['receipts']:assert old.read_predictions(target,receipt['batch_id'])[0]==receipt
    (args['project']/'features.csv').write_text('source changed')
    with pytest.raises(ValueError,match='source changed'):ingest()
    assert sha256(target)==before


def test_external_archive_cannot_write_into_source_or_foreign_tree(snapshots):
    args,_,create,_=snapshots;source,m=create('original');work=external(args['project'])
    with pytest.raises(ValueError,match='separate'):
        archive_daily_snapshot(source,sha256(source/'manifest.json'),source/'archive.sqlite',args['project'],args['seal_directory'],args['calendar'],work_root=work)
    with pytest.raises(ValueError,match='outside'):
        archive_daily_snapshot(source,sha256(source/'manifest.json'),work.parent/'outside.sqlite',args['project'],args['seal_directory'],args['calendar'],work_root=work)


@pytest.mark.parametrize('fault',['future','existing','project_output','nonresearch'])
def test_daily_workspace_gates_before_creating_output(tmp_path,fault):
    work=external(tmp_path)
    args=dict(project=tmp_path,work_root=work,inputs='@work/absent.json',signal='2026-10-28',out='@work/snapshot',exploratory=True)
    if fault=='future':args['signal']='2099-01-01'
    if fault=='existing':(work/'snapshot').mkdir();(work/'snapshot/marker').write_text('keep')
    if fault=='project_output':args['out']='snapshot'
    if fault=='nonresearch':args['exploratory']=False
    with pytest.raises(ValueError):run_frozen_daily(**args)
    if fault=='existing':assert (work/'snapshot/marker').read_text()=='keep'
    else:assert not (work/'snapshot').exists()

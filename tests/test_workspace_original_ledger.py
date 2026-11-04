import json,shutil
import pandas as pd
import pytest
from test_prediction_ledger import arguments
from test_paper_tracking import book
from test_prediction_archive import snapshots,counts
from ashare_mvp.workspace_prediction_archive import archive_daily_snapshot
from ashare_mvp.prediction_ledger import read_predictions
from ashare_mvp.phase2_protocol import sha256


def original_source(snapshots):
    args,_,create,_=snapshots;fixture,m=create('fixture_source')
    source=fixture.parent/'original_only';source.mkdir()
    shutil.copyfile(fixture/'predictions.sqlite',source/'predictions.sqlite')
    m['ledger_backup']=dict(status='skipped_by_request',created=False)
    (source/'manifest.json').write_text(json.dumps(m),encoding='utf8')
    target=args['project']/'archive/predictions.sqlite'
    work=args['project'].parent/(args['project'].name+'_work');work.mkdir()
    call=dict(snapshot=source,expected_manifest_sha256=sha256(source/'manifest.json'),archive=target,
              project=args['project'],work_root=work,seal_directory=args['seal_directory'],calendar=args['calendar'],expected_ledger_sha256=sha256(source/'predictions.sqlite'))
    return call,source,m,target


def test_original_ledger_import_preserves_all_receipts_scores_and_idempotency(snapshots):
    call,source,m,target=original_source(snapshots);before=sha256(source/'predictions.sqlite')
    result=archive_daily_snapshot(**call);after=sha256(target)
    assert counts(target)==(3,60,1) and result['new_batches']==3
    assert archive_daily_snapshot(**call)==result and sha256(target)==after
    for receipt in m['receipts']:
        actual,rows=read_predictions(target,receipt['batch_id']);saved,original=read_predictions(source/'predictions.sqlite',receipt['batch_id'])
        assert actual==saved==receipt;pd.testing.assert_frame_equal(rows,original,check_exact=True)
    assert sha256(source/'predictions.sqlite')==before and not (source/'prediction_backup.sqlite').exists()


@pytest.mark.parametrize('fault',['no_hash','wrong_hash','changed_ledger','live_wal','unexpected_copy'])
def test_unbound_changed_or_live_original_cannot_create_archive(snapshots,fault):
    call,source,m,target=original_source(snapshots)
    if fault=='no_hash':call['expected_ledger_sha256']=None
    elif fault=='wrong_hash':call['expected_ledger_sha256']='0'*64
    elif fault=='changed_ledger':
        with (source/'predictions.sqlite').open('ab') as stream:stream.write(b'changed')
    elif fault=='live_wal':(source/'predictions.sqlite-wal').write_bytes(b'live uncheckpointed content')
    elif fault=='unexpected_copy':(source/'prediction_backup.sqlite').write_bytes(b'ambiguous copy')
    message={'no_hash':'explicit verified original ledger','wrong_hash':'daily snapshot changed','changed_ledger':'daily snapshot changed','live_wal':'live WAL','unexpected_copy':'unexpectedly contains a backup'}[fault]
    with pytest.raises(ValueError,match=message):archive_daily_snapshot(**call)
    assert not target.exists()

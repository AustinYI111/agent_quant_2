import json,sqlite3,sys
from datetime import timedelta
import pandas as pd
import pytest
from test_paper_tracking import book
from ashare_mvp import prediction_ledger as ledger
from ashare_mvp.prediction_archive import archive_daily_snapshot
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.paper_tracking import replay_paper_books


@pytest.fixture
def snapshots(book,monkeypatch):
    args,_,cal=book;root=args['project']
    _,scores=ledger.read_predictions(args['database'],args['batch_ids'][0])
    scores=scores.drop(columns='rank').assign(is_st=False,eligible=True)
    def create(name,signal=None,shift=0,clock_minutes=0,synthetic=False):
        signal=cal[0] if signal is None else signal
        source=root/name;source.mkdir();database=source/'predictions.sqlite'
        created=(signal+pd.Timedelta(hours=18,minutes=clock_minutes)).tz_localize('Asia/Shanghai').tz_convert('UTC')
        observed=(signal+pd.Timedelta(hours=17,minutes=30)).tz_localize('Asia/Shanghai').tz_convert('UTC')
        monkeypatch.setattr(ledger,'_now',lambda:created)
        receipts=[]
        for horizon in ['5d','20d','12m']:
            frame=scores.copy();frame['score']+=shift
            receipts.append(ledger.record_predictions(database,project=root,seal_directory=args['seal_directory'],horizon=horizon,signal_date=signal,scores=frame,expected_symbols=list(frame.symbol),sources=[dict(path='features.csv',sha256=sha256(root/'features.csv'),observed_at_utc=observed.isoformat())],calendar=args['calendar'],synthetic=synthetic))
        backup=ledger.backup_ledger(database,source/'prediction_backup.sqlite')
        manifest=dict(schema='frozen_daily_research_snapshot_v1',status='research_snapshot_saved',model_selection_performed=False,protocol_sha256=receipts[0]['protocol_sha256'],signal_date=str(signal.date()),eligible=20,started_at_utc=(created-timedelta(seconds=1)).isoformat(),completed_at_utc=(created+timedelta(seconds=1)).isoformat(),receipts=receipts,ledger_backup=backup)
        (source/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')
        return source,manifest
    def ingest(source,synthetic=False):
        return archive_daily_snapshot(source,sha256(source/'manifest.json'),root/'archive/predictions.sqlite',root,args['seal_directory'],args['calendar'],synthetic)
    return args,cal,create,ingest


def counts(database):
    with sqlite3.connect(database) as c:return tuple(c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ['batches','scores','archive_imports'])


def test_original_clocks_roles_and_full_scores_survive_import_retry_and_restore(snapshots):
    args,_,create,ingest=snapshots;source,m=create('first');result=ingest(source);target=args['project']/'archive/predictions.sqlite'
    assert result['new_batches']==3 and result['new_scores']==60
    assert result['original_clocks_preserved'] and result['forecast_roles_preserved']
    assert ingest(source)==result and counts(target)==(3,60,1)
    restored=args['project']/'restored.sqlite';ledger.backup_ledger(target,restored)
    for expected in m['receipts']:
        actual,rows=ledger.read_predictions(restored,expected['batch_id'])
        original,original_rows=ledger.read_predictions(source/'prediction_backup.sqlite',expected['batch_id'])
        assert actual==original==expected and actual['role']=='prospective_paper_forecast'
        pd.testing.assert_frame_equal(rows,original_rows,check_exact=True)
    with sqlite3.connect(restored) as c:
        for statement in ['UPDATE archive_imports SET source_backup_sha256=\'bad\'','DELETE FROM archive_contract','UPDATE scores SET score=0']:
            with pytest.raises(sqlite3.IntegrityError,match='append-only'):c.execute(statement)


def test_multiple_days_are_readable_by_existing_paper_account(snapshots):
    args,cal,create,ingest=snapshots;first,m1=create('first');r1=ingest(first);second,m2=create('second',cal[5]);r2=ingest(second)
    target=args['project']/'archive/predictions.sqlite';assert counts(target)==(6,120,2)
    args.update(database=target,batch_ids=r1['batch_ids']+r2['batch_ids'])
    result=replay_paper_books(**args)
    assert result['books']['5d']['status']=='valued_research_prefix'
    assert result['books']['5d']['nav_rows']==5 and not result['excluded']
    for expected in m1['receipts']+m2['receipts']:assert ledger.read_predictions(target,expected['batch_id'])[0]==expected


@pytest.mark.parametrize('change',['score','clock'])
def test_existing_forecast_cannot_be_revised_or_reclocked(snapshots,change):
    args,_,create,ingest=snapshots;source,_=create('first');ingest(source)
    revision,_=create('revision',shift=.1 if change=='score' else 0,clock_minutes=1 if change=='clock' else 0)
    with pytest.raises(ValueError,match='revised|reclocked'):ingest(revision)
    assert counts(args['project']/'archive/predictions.sqlite')==(3,60,1)


def test_sqlite_failure_rolls_back_all_three_horizons(snapshots):
    args,cal,create,ingest=snapshots;source,_=create('first');ingest(source);second,m=create('second',cal[5]);target=args['project']/'archive/predictions.sqlite'
    failure=m['receipts'][1]['batch_id']
    with sqlite3.connect(target) as c:c.execute(f"CREATE TRIGGER fixture_failure BEFORE INSERT ON batches WHEN NEW.batch_id='{failure}' BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
    with pytest.raises(sqlite3.IntegrityError,match='fixture failure'):ingest(second)
    assert counts(target)==(3,60,1)
    with sqlite3.connect(target) as c:c.execute('DROP TRIGGER fixture_failure')
    assert ingest(second)['new_batches']==3 and counts(target)==(6,120,2)


def test_wrong_manifest_hash_or_mutated_backup_is_rejected_before_creation(snapshots):
    args,_,create,ingest=snapshots;source,_=create('first');target=args['project']/'archive/predictions.sqlite'
    with pytest.raises(ValueError,match='manifest hash'):archive_daily_snapshot(source,'0'*64,target,args['project'],args['seal_directory'],args['calendar'])
    with (source/'prediction_backup.sqlite').open('ab') as stream:stream.write(b'changed')
    with pytest.raises(ValueError,match='expected hash'):ingest(source)
    assert not target.exists()


def test_live_wal_is_not_treated_as_a_closed_backup(snapshots):
    args,_,create,ingest=snapshots;source,_=create('first');(source/'prediction_backup.sqlite-wal').write_bytes(b'nonempty')
    with pytest.raises(ValueError,match='live WAL'):ingest(source)
    assert not (args['project']/'archive/predictions.sqlite').exists()


@pytest.mark.parametrize('fault',['partial','unlisted_batch','future_completion'])
def test_partial_or_inconsistent_daily_source_is_not_imported(snapshots,fault):
    args,_,create,ingest=snapshots;source,m=create('first')
    if fault=='partial':m['status']='building'
    if fault=='unlisted_batch':m['receipts']=m['receipts'][:-1]
    if fault=='future_completion':m['completed_at_utc']='2099-01-01T00:00:00Z'
    (source/'manifest.json').write_text(json.dumps(m),encoding='utf8')
    with pytest.raises(ValueError,match='complete|exactly once|future'):ingest(source)
    assert not (args['project']/'archive/predictions.sqlite').exists()


def test_synthetic_and_real_namespaces_cannot_mix(snapshots):
    args,_,create,ingest=snapshots;source,_=create('first');ingest(source);synthetic,_=create('synthetic',synthetic=True)
    with pytest.raises(ValueError,match='dataset kind'):ingest(synthetic,True)
    assert counts(args['project']/'archive/predictions.sqlite')==(3,60,1)


def test_archive_cannot_modify_its_immutable_source_directory(snapshots):
    args,_,create,_=snapshots;source,_=create('first');before=sha256(source/'manifest.json')
    with pytest.raises(ValueError,match='separate'):archive_daily_snapshot(source,before,source/'archive.sqlite',args['project'],args['seal_directory'],args['calendar'])
    assert sha256(source/'manifest.json')==before and not (source/'archive.sqlite').exists()


@pytest.mark.parametrize('kind',['raw_database','text_file'])
def test_existing_nonarchive_data_is_never_modified(snapshots,kind):
    args,_,create,_=snapshots;source,_=create('first');target=args['project']/'important_data'
    if kind=='raw_database':
        with sqlite3.connect(target) as c:c.execute('CREATE TABLE quotes (price REAL)');c.execute('INSERT INTO quotes VALUES (10)')
    else:target.write_text('preserve original data')
    before=sha256(target)
    with pytest.raises(ValueError,match='not a prediction archive'):archive_daily_snapshot(source,sha256(source/'manifest.json'),target,args['project'],args['seal_directory'],args['calendar'])
    assert sha256(target)==before


def test_daily_cli_archives_after_success_and_keeps_snapshot_immutable(snapshots,monkeypatch):
    from ashare_mvp import frozen_daily
    args,_,create,_=snapshots;source,m=create('first');root=args['project']
    pd.DataFrame({'date':args['calendar']}).to_csv(source/'forecast_calendar.csv',index=False)
    (root/'inputs.json').write_text(json.dumps({'seal':'seal'}))
    before=sha256(source/'manifest.json')
    monkeypatch.setattr(frozen_daily,'run_frozen_daily',lambda **kwargs:m)
    monkeypatch.setattr(sys,'argv',['daily','--project',str(root),'--inputs','inputs.json','--signal',m['signal_date'],'--out','first','--exploratory','--archive','archive/predictions.sqlite'])
    frozen_daily.main()
    assert counts(root/'archive/predictions.sqlite')==(3,60,1) and sha256(source/'manifest.json')==before


def test_daily_delivery_conflict_does_not_change_successful_snapshot(snapshots,monkeypatch):
    from ashare_mvp import frozen_daily
    args,_,create,ingest=snapshots;source,_=create('first');ingest(source);revision,m=create('revision',shift=.1);root=args['project']
    pd.DataFrame({'date':args['calendar']}).to_csv(revision/'forecast_calendar.csv',index=False)
    (root/'inputs.json').write_text(json.dumps({'seal':'seal'}));before=sha256(revision/'manifest.json')
    monkeypatch.setattr(frozen_daily,'run_frozen_daily',lambda **kwargs:m)
    monkeypatch.setattr(sys,'argv',['daily','--project',str(root),'--inputs','inputs.json','--signal',m['signal_date'],'--out','revision','--exploratory','--archive','archive/predictions.sqlite'])
    with pytest.raises(ValueError,match='revised'):frozen_daily.main()
    assert sha256(revision/'manifest.json')==before and json.loads((revision/'manifest.json').read_text())['status']=='research_snapshot_saved'
    assert counts(root/'archive/predictions.sqlite')==(3,60,1)


def test_daily_archive_path_is_validated_before_expensive_inference(snapshots,monkeypatch):
    from ashare_mvp import frozen_daily
    args,_,_,_=snapshots;calls=[]
    monkeypatch.setattr(frozen_daily,'run_frozen_daily',lambda **kwargs:calls.append(kwargs))
    monkeypatch.setattr(sys,'argv',['daily','--project',str(args['project']),'--inputs','inputs.json','--signal','2026-01-05','--out','first','--exploratory','--archive','../outside'])
    with pytest.raises(ValueError,match='relative'):frozen_daily.main()
    assert not calls

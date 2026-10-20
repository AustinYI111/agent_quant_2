import json
import sqlite3
import pandas as pd
import pytest

from ashare_mvp import prediction_ledger as ledger
from ashare_mvp.phase2_protocol import seal, sha256


@pytest.fixture
def arguments(tmp_path, monkeypatch):
    models = {}
    for horizon in ['5d', '20d', '12m']:
        folder = tmp_path / 'models' / horizon
        folder.mkdir(parents=True)
        (folder/'model.txt').write_text('synthetic fixture only', encoding='utf8')
        (folder/'metadata.json').write_text(json.dumps(dict(horizon=dict(key=horizon), trained_asof='2025-01-02')), encoding='utf8')
        for name in ['feature_input_policy.json', 'training_dataset_metadata.json']:
            (folder/name).write_text('{}', encoding='utf8')
        models[horizon] = dict(relative_path='models/'+horizon, trained_asof='2025-01-02', files_sha256={path.name: sha256(path) for path in folder.iterdir()})
    protocol = dict(schema='phase2_research_protocol_v1', frozen_at_utc='2026-10-20T02:00:00Z', research_end='2025-12-31', independent_start='2026-01-01', prospective_start='2026-10-20', models=models, candidate_groups=[dict(id=name) for name in ['industry', 'market_state', 'oneil_box', 'combined']], validation=dict(independent_data_may_select_parameters=False, unknown_outcomes='unmatured_or_unresolved_never_zero'))
    source = tmp_path/'protocol.json'
    source.write_text(json.dumps(protocol), encoding='utf8')
    seal(source, tmp_path/'seal', tmp_path)
    (tmp_path/'features.csv').write_text('explicit synthetic fixture', encoding='utf8')
    monkeypatch.setattr(ledger, '_now', lambda: pd.Timestamp('2026-10-20T10:00:00Z'))
    frame = pd.DataFrame(dict(symbol=['issuer_'+str(index) for index in range(25)], trading_symbol=[f'sh.{600000+index:06d}' for index in range(25)], score=[index/10 for index in range(25)], is_st=False, eligible=True))
    return dict(database=tmp_path/'predictions.sqlite', project=tmp_path, seal_directory=tmp_path/'seal', horizon='5d', signal_date='2026-10-20', scores=frame, expected_symbols=list(frame.symbol), sources=[dict(path='features.csv', sha256=sha256(tmp_path/'features.csv'), observed_at_utc='2026-10-20T09:30:00Z')], calendar=pd.to_datetime(['2026-10-20', '2026-10-21', '2026-10-22']), synthetic=True)


def test_full_universe_is_saved_and_top20_is_a_view(arguments):
    result = ledger.record_predictions(**arguments)
    receipt, all_scores = ledger.read_predictions(arguments['database'], result['batch_id'])
    assert len(all_scores) == 25 and receipt == result
    assert result['role'] == 'synthetic_test' and not result['prospective']
    assert result['candidate_count'] == 25 and not result['score_is_expected_return']
    assert list(all_scores['rank']) == list(range(1, 26))
    assert all_scores.iloc[0].symbol == 'issuer_24'
    assert len(ledger.read_predictions(arguments['database'], result['batch_id'], 20)[1]) == 20


def test_idempotent_retry_keeps_original_clock_and_role(arguments, monkeypatch):
    arguments['synthetic'] = False
    first = ledger.record_predictions(**arguments)
    assert first['prospective']
    monkeypatch.setattr(ledger, '_now', lambda: pd.Timestamp('2026-10-22T10:00:00Z'))
    assert ledger.record_predictions(**arguments) == first
    arguments['scores'].loc[0, 'score'] = 999
    with pytest.raises(ValueError, match='cannot be revised'):
        ledger.record_predictions(**arguments)


def test_late_first_publication_is_never_prospective(arguments, monkeypatch):
    arguments['synthetic'] = False
    monkeypatch.setattr(ledger, '_now', lambda: pd.Timestamp('2026-10-21T03:00:00Z'))
    result = ledger.record_predictions(**arguments)
    assert not result['prospective'] and result['role'] == 'retrospective_independent_snapshot'


@pytest.mark.parametrize('bad_code', ['bj.830001', 'sz.200001', 'sh.900901', 'sh.000001', 'sh.689009', 'sz.009001', 'sh.680001', None])
def test_beijing_bshares_and_unknown_trading_identity_are_blocked(arguments, bad_code):
    arguments['scores'].loc[0, 'trading_symbol'] = bad_code
    with pytest.raises(ValueError, match='A-share identities'):
        ledger.record_predictions(**arguments)
    assert not arguments['database'].exists()


@pytest.mark.parametrize('column,value', [('is_st', True), ('is_st', None), ('eligible', False), ('score', float('nan')), ('score', float('inf'))])
def test_st_unknown_or_nonfinite_score_cannot_publish(arguments, column, value):
    if value is None:
        arguments['scores'][column] = arguments['scores'][column].astype(object)
    arguments['scores'].loc[0, column] = value
    with pytest.raises(ValueError):
        ledger.record_predictions(**arguments)
    assert not arguments['database'].exists()


def test_missing_candidate_or_duplicate_issuer_cannot_publish(arguments):
    arguments['scores'] = arguments['scores'].iloc[:-1]
    with pytest.raises(ValueError, match='complete eligible universe'):
        ledger.record_predictions(**arguments)


def test_hash_and_observation_clock_are_checked_before_publish(arguments):
    arguments['sources'][0]['observed_at_utc'] = '2026-10-21T03:00:00Z'
    with pytest.raises(ValueError, match='future'):
        ledger.record_predictions(**arguments)
    arguments['sources'][0]['observed_at_utc'] = '2026-10-20T09:30:00Z'
    (arguments['project']/'features.csv').write_text('tampered source', encoding='utf8')
    with pytest.raises(ValueError, match='source hash'):
        ledger.record_predictions(**arguments)


def test_database_guards_both_receipts_and_scores_against_rewriting(arguments):
    result = ledger.record_predictions(**arguments)
    with sqlite3.connect(arguments['database']) as connection:
        for statement in ['UPDATE scores SET score=0', 'DELETE FROM scores', "UPDATE batches SET receipt_json='{}'", 'DELETE FROM batches', 'INSERT OR REPLACE INTO batches SELECT * FROM batches', 'INSERT OR REPLACE INTO scores SELECT * FROM scores']:
            with pytest.raises(sqlite3.IntegrityError, match='append-only'):
                connection.execute(statement)
    assert ledger.read_predictions(arguments['database'], result['batch_id'])[0] == result


def test_consistent_backup_restores_predictions_and_rejects_overwrite(arguments):
    result = ledger.record_predictions(**arguments)
    # Deliberately keep an active WAL connection: copying only the .sqlite file
    # would omit committed contents; the backup API must include them.
    with sqlite3.connect(arguments['database']) as connection:
        connection.execute('PRAGMA journal_mode=WAL')
        arguments['horizon'] = '20d'
        second = ledger.record_predictions(**arguments)
        target = arguments['project']/'backup.sqlite'
        evidence = ledger.backup_ledger(arguments['database'], target)
        assert evidence['sha256'] == sha256(target)
        assert ledger.read_predictions(target, result['batch_id'])[0] == result
        assert ledger.read_predictions(target, second['batch_id'])[0] == second
        with pytest.raises(ValueError, match='must be new'):
            ledger.backup_ledger(arguments['database'], target)


def test_missing_read_does_not_create_database(arguments):
    with pytest.raises(sqlite3.OperationalError):
        ledger.read_predictions(arguments['database'], 'unknown')
    assert not arguments['database'].exists()

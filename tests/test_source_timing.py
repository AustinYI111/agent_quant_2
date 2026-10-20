import json
import pytest
from ashare_mvp.phase2_protocol import sha256
from ashare_mvp.source_timing import audit_market_observations


def capture(tmp_path,clock):
    source=tmp_path/'source';source.mkdir()
    (source/'daily_20261020.csv').write_text('explicit synthetic fixture; only clock gate under test',encoding='utf8')
    manifest=dict(schema='market_daily_raw_v1',status='downloaded_raw',files={'daily_20261020.csv':dict(sha256=sha256(source/'daily_20261020.csv'),retrieved_at=clock)})
    (source/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')
    return source


def test_intraday_capture_is_not_relabelled_after_close(tmp_path):
    source=capture(tmp_path,'2026-10-20T07:00:00Z')
    with pytest.raises(ValueError,match='before the close release'):
        audit_market_observations([source],tmp_path/'audit.json')
    assert not (tmp_path/'audit.json').exists()


def test_post_close_observation_and_historical_backfill_are_explicit(tmp_path):
    source=capture(tmp_path,'2026-10-21T02:00:00Z')
    proof=audit_market_observations([source],tmp_path/'audit.json')
    assert proof['sessions']==1 and proof['records'][0]['date']=='2026-10-20'
    assert proof['latest_input_observed_at_utc']=='2026-10-21T02:00:00+00:00'
    assert not proof['certified']


def test_missing_timezone_and_changed_raw_source_fail_closed(tmp_path):
    source=capture(tmp_path,'2026-10-20 18:00:00')
    with pytest.raises(ValueError,match='timezone'):
        audit_market_observations([source],tmp_path/'audit.json')
    (source/'daily_20261020.csv').write_text('changed',encoding='utf8')
    with pytest.raises(ValueError,match='evidence changed'):
        audit_market_observations([source],tmp_path/'audit.json')

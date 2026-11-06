import pytest
from ashare_mvp.frozen_daily import run_frozen_daily


def test_unverified_sources_require_explicit_research_mode(tmp_path):
    with pytest.raises(ValueError,match='exploratory'):
        run_frozen_daily(tmp_path,'missing.json','2026-10-20','snapshot')
    assert not (tmp_path/'snapshot').exists()


def test_existing_snapshot_is_preserved_even_when_recipe_is_missing(tmp_path):
    target=tmp_path/'snapshot';target.mkdir()
    marker=target/'manifest.json';marker.write_text('immutable earlier result')
    with pytest.raises(ValueError,match='new daily'):
        run_frozen_daily(tmp_path,'missing.json','2026-10-20','snapshot',True)
    assert marker.read_text()=='immutable earlier result'


@pytest.mark.parametrize('argument',['inputs','out'])
def test_path_escape_cannot_read_or_write_outside_project(tmp_path,argument):
    args=dict(project=tmp_path,inputs='missing.json',signal='2026-10-20',out='snapshot',exploratory=True)
    args[argument]='../outside'
    with pytest.raises(ValueError,match='relative'):
        run_frozen_daily(**args)
    assert not (tmp_path/'snapshot').exists()


def test_future_signal_is_rejected_before_reading_recipe(tmp_path):
    with pytest.raises(ValueError,match='cutoff'):
        run_frozen_daily(tmp_path,'missing.json','2099-01-01','snapshot',True)
    assert not (tmp_path/'snapshot').exists()

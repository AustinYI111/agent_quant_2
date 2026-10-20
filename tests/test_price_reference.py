import pytest
from ashare_mvp.price_reference import reference_matches,validate_reference_tick


def test_explicit_half_up_quote_precision_preserves_large_error_rejection():
    assert not reference_matches(9.67,10,.335)
    assert reference_matches(9.67,10,.335,tick=.01)
    assert not reference_matches(9.68,10,.335,tick=.01)
    assert reference_matches(1.51,2,.495,tick=.01)
    assert not reference_matches(1.50,2,.495,tick=.01)
    assert not reference_matches(10000.02,10000,tick=.01)


@pytest.mark.parametrize("tick",[0,-.01,float("nan"),float("inf")])
def test_invalid_precision_rejected(tick):
    with pytest.raises(ValueError,match="positive and finite"): validate_reference_tick(tick)


def test_invalid_previous_valuation_cannot_be_explained_by_quote_rounding():
    assert not reference_matches(10,float("inf"),tick=.01)
    assert not reference_matches(10,10,multiplier=0,tick=.01)

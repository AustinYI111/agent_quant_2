"""Explicit quote precision; never infer missing corporate-action terms."""
from decimal import Decimal, ROUND_HALF_UP
import numpy as np


def validate_reference_tick(tick):
    if tick is None: return None
    value=Decimal(str(tick))
    if not value.is_finite() or value<=0: raise ValueError("reference tick must be positive and finite")
    return value


def reference_matches(observed, previous_close, cash=0.0, multiplier=1.0, tick=None):
    precision=validate_reference_tick(tick)
    if not np.isfinite([observed,previous_close,cash,multiplier]).all() or previous_close<=0 or observed<=0 or cash<0 or multiplier<=0:
        return False
    expected=(previous_close-cash)/multiplier
    if precision is None:
        return bool(np.isclose(observed,expected,rtol=1e-5,atol=1e-4))
    if np.isclose(observed,expected,rtol=0,atol=1e-8): return True
    exact=(Decimal(str(previous_close))-Decimal(str(cash)))/Decimal(str(multiplier))
    rounded=(exact/precision).quantize(Decimal("1"),rounding=ROUND_HALF_UP)*precision
    return bool(np.isclose(observed,float(rounded),rtol=0,atol=1e-8))

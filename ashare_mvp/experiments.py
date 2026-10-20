"""Matched walk-forward factor ablations; descriptive, not proof of alpha."""
from dataclasses import replace, asdict
from pathlib import Path
import json

import pandas as pd

from .model import Config
from .pipeline import run


def compare_oneil(data, out, config=Config(), asof=None, evaluation_start=None):
    path = Path(out)
    if path.exists() and any(path.iterdir()):
        raise ValueError("comparison output must be empty")
    end = pd.Timestamp(asof) if asof is not None else data.calendar[-1]
    start = pd.Timestamp(evaluation_start) if evaluation_start else end - pd.DateOffset(months=24)
    if start > end:
        raise ValueError("evaluation_start after asof")
    # Both variants use exactly the same label, train window and fees.
    baseline = run(data, path / "baseline", replace(config, oneil=False), end)
    variant = run(data, path / "oneil", replace(config, oneil=True), end)
    a = pd.read_csv(path / "baseline" / "cohort_performance.csv", parse_dates=["date"])
    b = pd.read_csv(path / "oneil" / "cohort_performance.csv", parse_dates=["date"])
    paired = a.merge(b, on=["date", "horizon_months"], suffixes=("_baseline", "_oneil"), validate="one_to_one")
    paired = paired[(paired.date >= start) & paired.complete_baseline & paired.complete_oneil].copy()
    paired["gross_return_delta"] = paired.gross_return_oneil - paired.gross_return_baseline
    paired["indicative_net_return_delta"] = paired.indicative_net_return_oneil - paired.indicative_net_return_baseline
    paired.to_csv(path / "paired_cohorts.csv", index=False)
    result = dict(synthetic=data.metadata["synthetic"], asof=str(end.date()), evaluation_start=str(start.date()),
                  common_config=asdict(replace(config, oneil=False)),
                  baseline_months=baseline["monthly_predictions"], oneil_months=variant["monthly_predictions"],
                  descriptive_deltas={str(h): dict(paired_cohorts=len(g), mean_gross_return_delta=float(g.gross_return_delta.mean()),
                                                    mean_indicative_net_return_delta=float(g.indicative_net_return_delta.mean()))
                                      for h, g in paired.groupby("horizon_months")},
                  interpretation="Matched observed evaluation; overlapping cohorts, no significance claim or automatic factor promotion")
    (path / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return result

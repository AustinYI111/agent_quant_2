"""Reproducible offline release exercise; all prices and holdings are synthetic."""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from .demo import make_demo, save
from .pipeline import run
from .model import Config
from .research import daily_report, load_holdings


def release_check(out):
    root = Path(out)
    if root.exists() and any(root.iterdir()):
        raise ValueError("release output must be empty")
    root.mkdir(parents=True, exist_ok=True)
    data = make_demo(seed=42, stocks=40, periods=1512)
    save(data, root / "synthetic_input")
    summary = run(data, root / "baseline")
    picks = pd.read_csv(root / "baseline/monthly_top20.csv", parse_dates=["date"])
    audit = pd.read_csv(root / "baseline/training_audit.csv", parse_dates=["date", "cutoff", "max_label_end"])
    cohorts = pd.read_csv(root / "baseline/cohort_performance.csv")
    trained = audit[audit.status.eq("trained")]
    checks = {
        "monthly_top20": bool(not picks.empty and picks.groupby("date").size().eq(20).all()
                              and not picks.duplicated(["date", "symbol"]).any()),
        "mature_training_labels": bool(not trained.empty and trained.max_label_end.notna().all()
                                        and (trained.max_label_end < trained.cutoff).all()
                                        and (trained.cutoff < trained.date).all()),
        "mature_3_6_12_month_cohorts": all(bool(cohorts.loc[cohorts.horizon_months.eq(h), "complete"].any())
                                            for h in (3, 6, 12)),
        "persisted_models": bool(list((root / "baseline/models").glob("*.txt"))),
    }
    variant = run(data, root / "oneil", Config(oneil=True))
    checks["optional_oneil"] = (variant["latest_selection_count"] == 20
                                and "earnings_revenue_growth_spread" in variant["features"]
                                and "earnings_revenue_growth_spread" not in summary["features"])
    holdings_path = root / "synthetic_holdings.csv"
    pd.DataFrame({"symbol": ["SYN0000"], "shares": [100], "average_cost": [10]}).to_csv(holdings_path, index=False)
    latest = pd.read_csv(root / "baseline/latest_top20.csv", parse_dates=["date"])
    ledger = root / "prediction_ledger.csv"
    report = daily_report(data, latest, load_holdings(holdings_path), root / "report", data.calendar[-1], ledger)
    ledger_before = ledger.read_bytes()
    daily_report(data, latest, load_holdings(holdings_path), root / "report_repeat", data.calendar[-1], ledger)
    checks["ledger_idempotent"] = ledger.read_bytes() == ledger_before
    checks["report_current_and_valued"] = bool(report["valuation_complete"] and not report["stale_prediction"])
    checks["latest_top20"] = summary["latest_selection_count"] == 20
    result = {"status": "passed" if all(checks.values()) else "failed", "synthetic": True,
              "checks": checks, "monthly_predictions": summary["monthly_predictions"],
              "cohorts": summary["cohorts"],
              "limitations": ["Synthetic smoke exercise verifies execution, not real A-share investment performance",
                              "Synthetic holdings are unrelated to the user's pending holdings CSV"],
              "output_hashes": {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in sorted(root.rglob("*")) if p.is_file()}}
    (root / "release_check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    if result["status"] != "passed":
        raise RuntimeError(f"release checks failed: {checks}")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = release_check(args.out)
    print(json.dumps({k: v for k, v in result.items() if k != "output_hashes"}, ensure_ascii=False, indent=2))

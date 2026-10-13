"""Combine same-period financial dependencies without backfilling later revisions."""
from pathlib import Path
from io import BytesIO
import hashlib
import json
import pandas as pd


def combine_financial_dependencies(earnings, balances):
    keys = ["symbol", "report_date"]
    earnings, balances = earnings.copy(), balances.copy()
    for frame in [earnings, balances]:
        if not set(keys + ["available_date"]).issubset(frame):
            raise ValueError("financial dependency keys missing")
        for col in ["report_date", "available_date"]:
            frame[col] = pd.to_datetime(frame[col])
            if frame[col].dt.tz is not None or not frame[col].dropna().eq(frame[col].dropna().dt.normalize()).all():
                raise ValueError("normalized Shanghai financial dates required")
        visible = frame[frame.symbol.notna() & frame.available_date.notna()]
        if visible[keys].isna().any().any() or visible.duplicated(keys + ["available_date"]).any():
            raise ValueError("ambiguous financial dependency version")
        if frame.available_date.dropna().le(frame.loc[frame.available_date.notna(), "report_date"]).any():
            raise ValueError("dependency visible before report end")
    earnings = earnings[earnings.symbol.notna() & earnings.available_date.notna()]
    balances = balances[balances.symbol.notna() & balances.available_date.notna()]
    if earnings.empty:
        raise ValueError("no visible earnings records")
    events = pd.concat([earnings[keys + ["available_date"]], balances[keys + ["available_date"]]]).drop_duplicates()
    events = events.sort_values("available_date")
    income = earnings.drop(columns=["debt_ratio"], errors="ignore").rename(columns={"available_date": "earnings_available_date"})
    panel = pd.merge_asof(events, income.sort_values("earnings_available_date"), by=keys,
                          left_on="available_date", right_on="earnings_available_date", direction="backward")
    panel = panel[panel.earnings_available_date.notna()].copy()
    if balances.empty:
        panel["balance_available_date"] = pd.NaT
        panel["debt_ratio"] = float("nan")
    else:
        debt = balances[keys + ["available_date", "debt_ratio"]].rename(columns={"available_date": "balance_available_date"})
        panel = pd.merge_asof(panel.sort_values("available_date"), debt.sort_values("balance_available_date"), by=keys,
                              left_on="available_date", right_on="balance_available_date", direction="backward")
    if not panel.earnings_available_date.le(panel.available_date).all() or not panel.balance_available_date.dropna().le(panel.loc[panel.balance_available_date.notna(), "available_date"]).all():
        raise AssertionError("future financial dependency")
    return panel.sort_values(keys + ["available_date"]).reset_index(drop=True)


def combine_financial_files(earnings_directory, balance_directory, out):
    target = Path(out)
    if target.exists() and any(target.iterdir()):
        raise ValueError("combined financial output must be empty")
    panels, sources = [], []
    for directory, filename in [(earnings_directory, "financial_panel.csv"), (balance_directory, "balance_panel.csv")]:
        path = Path(directory)
        summary_bytes = (path / "summary.json").read_bytes()
        summary = json.loads(summary_bytes)
        raw = (path / filename).read_bytes()
        if hashlib.sha256(raw).hexdigest() != summary.get("panel_sha256"):
            raise ValueError("financial dependency panel hash mismatch")
        panels.append(pd.read_csv(BytesIO(raw), dtype={"symbol": str}, parse_dates=["report_date", "available_date"]))
        sources.append(dict(directory=str(path), summary_sha256=hashlib.sha256(summary_bytes).hexdigest(), summary=summary))
    panel = combine_financial_dependencies(*panels)
    target.mkdir(parents=True, exist_ok=True)
    panel.to_csv(target / "financial_panel.csv", index=False)
    result = dict(records=len(panel), symbols=int(panel.symbol.nunique()), debt_records=int(panel.debt_ratio.notna().sum()),
                  partial_source=any(s["summary"]["partial_source"] for s in sources), certified=False, model_ready=False,
                  sources=sources, excluded_unidentified_or_invisible={name:int((frame.symbol.isna() | frame.available_date.isna()).sum()) for name,frame in zip(["earnings", "balances"],panels)}, panel_sha256=hashlib.sha256((target / "financial_panel.csv").read_bytes()).hexdigest(),
                  limitations=["Same-period native missing dependencies retained", "Original provider financial vintages remain unverified"])
    (target / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result

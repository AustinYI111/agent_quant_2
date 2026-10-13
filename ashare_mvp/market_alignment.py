"""Point-in-time joins of verified downloaded price files and financial clocks."""
from pathlib import Path
import hashlib
import json

import pandas as pd

from .public_data import is_ashare


def financial_events(panel):
    known = panel[panel.symbol.notna() & panel.available_date.notna()].copy()
    required = ["symbol", "report_date", "available_date"]
    if known[required].isna().any().any() or known.duplicated(required).any():
        raise ValueError("ambiguous normalized financial version")
    events = []
    for symbol, records in known.groupby("symbol", sort=False):
        latest_period = pd.NaT
        for date, group in records.sort_values(["available_date", "report_date"]).groupby("available_date", sort=True):
            row = group.sort_values("report_date").iloc[-1]
            if pd.isna(latest_period) or row.report_date >= latest_period:
                latest_period = row.report_date
                events.append(row)
            # A late correction to an older period must not replace a newer one.
    return pd.DataFrame(events, columns=panel.columns)


def align_market_financials(market_directory, financial_directory, out):
    market, financial, target = Path(market_directory), Path(financial_directory), Path(out)
    if target.exists() and any(target.iterdir()):
        raise ValueError("alignment output must be empty")
    manifest_bytes = (market / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    summary = json.loads((financial / "summary.json").read_text(encoding="utf-8"))
    file = financial / "financial_panel.csv"
    if hashlib.sha256(file.read_bytes()).hexdigest() != summary.get("panel_sha256"):
        raise ValueError("normalized financial panel hash mismatch")
    fundamentals = pd.read_csv(file, dtype={"symbol": str}, parse_dates=["report_date", "available_date"])
    events = financial_events(fundamentals)
    frames = []
    for name, info in manifest["files"].items():
        if not name.startswith("daily_") or not name.endswith(".csv"):
            continue
        if Path(name).name != name:
            raise ValueError("invalid market data file name")
        file = market / name
        if hashlib.sha256(file.read_bytes()).hexdigest() != info["sha256"]:
            raise ValueError("market daily file hash mismatch")
        p = pd.read_csv(file, dtype={"code": str}, parse_dates=["date"])
        expected_date = pd.to_datetime(name[len("daily_"):-4], format="%Y%m%d")
        if p.code.isna().any() or not p.date.eq(expected_date).all():
            raise ValueError("daily file contains a wrong session date or missing security key")
        p = p[p.code.map(is_ashare)].rename(columns={"code": "symbol"})
        frames.append(p)
    if not frames or events.empty:
        raise ValueError("no verified price/financial events to align")
    prices = pd.concat(frames, ignore_index=True)
    if prices.duplicated(["date", "symbol"]).any():
        raise ValueError("duplicate market price keys")
    result = pd.merge_asof(prices.sort_values(["date", "symbol"]),
                           events.sort_values(["available_date", "symbol"]),
                           by="symbol", left_on="date", right_on="available_date", direction="backward")
    if not (result.loc[result.available_date.notna(), "available_date"] <= result.loc[result.available_date.notna(), "date"]).all():
        raise AssertionError("financial visibility join used a future record")
    target.mkdir(parents=True, exist_ok=True)
    result.to_csv(target / "aligned_market.csv", index=False)
    info = dict(price_rows=len(result), symbols=int(result.symbol.nunique()),
                matched_financial_rows=int(result.available_date.notna().sum()),
                unpriced_financial_symbols=int(events.loc[~events.symbol.isin(prices.symbol.unique()), "symbol"].nunique()),
                first_date=str(result.date.min().date()), last_date=str(result.date.max().date()),
                market_partial=manifest["status"] != "downloaded_raw", financial_partial=summary["partial_source"],
                certified=False, model_ready=False,
                input_market_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                input_financial_panel_sha256=summary["panel_sha256"],
                output_sha256=hashlib.sha256((target / "aligned_market.csv").read_bytes()).hexdigest(),
                limitations=["Original financial versions remain unverified", "Raw prices require corporate-action and delisting treatment",
                             "Partial coverage is not a complete historical market backtest"])
    (target / "input_market_manifest_snapshot.json").write_bytes(manifest_bytes)
    (target / "summary.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return info

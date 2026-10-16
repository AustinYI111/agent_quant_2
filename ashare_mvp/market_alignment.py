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
    """Join every verified daily file, holding one cross-section in memory."""
    from io import BytesIO
    import tempfile
    market, financial, target = Path(market_directory), Path(financial_directory), Path(out)
    if target.exists() and any(target.iterdir()):
        raise ValueError("alignment output must be empty")
    manifest_bytes = (market / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    summary = json.loads((financial / "summary.json").read_text(encoding="utf-8"))
    raw_financial = (financial / "financial_panel.csv").read_bytes()
    if hashlib.sha256(raw_financial).hexdigest() != summary.get("panel_sha256"):
        raise ValueError("normalized financial panel hash mismatch")
    fundamentals = pd.read_csv(BytesIO(raw_financial), dtype={"symbol": str},
                               parse_dates=["report_date", "available_date"], low_memory=False)
    events = financial_events(fundamentals).sort_values(["available_date", "symbol"])
    headers, raw_columns = {}, []
    daily = sorted((name, info) for name, info in manifest["files"].items()
                   if name.startswith("daily_") and name.endswith(".csv"))
    if not daily or events.empty:
        raise ValueError("no verified price/financial events to align")
    for name, file_info in daily:
        if Path(name).name != name:
            raise ValueError("invalid market data file name")
        columns = file_info.get("columns")
        if columns is None:
            columns = list(pd.read_csv(market / name, nrows=0).columns)
        if not {"date", "code"}.issubset(columns) or len(columns) != len(set(columns)) or "symbol" in columns:
            raise ValueError("invalid daily price column schema")
        headers[name] = list(columns)
        raw_columns.extend(column for column in columns if column not in raw_columns)
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = None
    rows = matched = debt_rows = checked = peak_rows = 0
    observed_symbols = set()
    first_date = last_date = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", delete=False,
                dir=target.parent, prefix="." + target.name + "-", suffix=".pending.csv") as output:
            pending = Path(output.name)
            for name, file_info in daily:
                raw = (market / name).read_bytes()
                if hashlib.sha256(raw).hexdigest() != file_info["sha256"]:
                    raise ValueError("market daily file hash mismatch")
                p = pd.read_csv(BytesIO(raw), dtype={"code": str}, parse_dates=["date"], low_memory=False)
                if list(p.columns) != headers[name]:
                    raise ValueError("daily header differs from recorded schema")
                if "rows" in file_info and len(p) != file_info["rows"]:
                    raise ValueError("daily row count differs from manifest")
                expected_date = pd.to_datetime(name[len("daily_"):-4], format="%Y%m%d")
                if p.code.isna().any() or not p.date.eq(expected_date).all():
                    raise ValueError("daily file contains a wrong session date or missing security key")
                checked += 1
                p = p[p.code.map(is_ashare)].reindex(columns=raw_columns).rename(columns={"code": "symbol"})
                if p.duplicated(["date", "symbol"]).any():
                    raise ValueError("duplicate market price keys")
                if p.empty:
                    continue
                result = pd.merge_asof(p.sort_values(["date", "symbol"]), events,
                        by="symbol", left_on="date", right_on="available_date", direction="backward")
                known = result.available_date.notna()
                if not result.loc[known, "available_date"].le(result.loc[known, "date"]).all():
                    raise AssertionError("financial visibility join used a future record")
                result.to_csv(output, index=False, header=rows == 0)
                rows += len(result)
                matched += int(known.sum())
                if "debt_ratio" in result:
                    debt_rows += int(result.debt_ratio.notna().sum())
                peak_rows = max(peak_rows, len(result))
                observed_symbols.update(result.symbol)
                first_date = expected_date if first_date is None else first_date
                last_date = expected_date
        if rows == 0:
            raise ValueError("no verified price/financial events to align")
        checksum = hashlib.sha256()
        with pending.open("rb") as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                checksum.update(block)
        target.mkdir(parents=True, exist_ok=True)
        pending.replace(target / "aligned_market.csv")
        pending = None
        info = dict(price_rows=rows, symbols=len(observed_symbols), matched_financial_rows=matched,
                    matched_debt_rows=debt_rows, daily_files_hash_checked=checked,
                    peak_price_cross_section_rows=peak_rows, processing_mode="daily_streaming",
                    unpriced_financial_symbols=int(events.loc[~events.symbol.isin(observed_symbols), "symbol"].nunique()),
                    first_date=str(first_date.date()), last_date=str(last_date.date()),
                    market_partial=manifest["status"] != "downloaded_raw", financial_partial=summary["partial_source"],
                    certified=False, model_ready=False,
                    input_market_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                    input_financial_panel_sha256=summary["panel_sha256"], output_sha256=checksum.hexdigest(),
                    limitations=["Original financial versions remain unverified", "Raw prices require corporate-action and delisting treatment",
                                 "Partial coverage is not a complete historical market backtest"])
        (target / "input_market_manifest_snapshot.json").write_bytes(manifest_bytes)
        (target / "summary.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        return info
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)

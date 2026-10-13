"""Balance-sheet ratios visible only after all supplied provider clocks."""
import numpy as np
import pandas as pd


def normalize_balance_records(records, symbol, calendar):
    from .public_data import is_ashare
    if not is_ashare(symbol):
        raise ValueError("supported A-share symbol required")
    raw = pd.DataFrame(records)
    required = ["SECURITY_CODE", "REPORT_DATE", "NOTICE_DATE", "UPDATE_DATE", "TOTAL_ASSETS", "TOTAL_LIABILITIES"]
    if not set(required).issubset(raw) or raw.empty or not raw.SECURITY_CODE.astype(str).eq(symbol.split(".")[1]).all():
        raise ValueError("balance identity/schema mismatch")
    sessions = pd.DatetimeIndex(calendar)
    if sessions.empty or sessions.hasnans or sessions.has_duplicates or not sessions.is_monotonic_increasing or sessions.tz is not None or not sessions.equals(sessions.normalize()):
        raise ValueError("ordered normalized session calendar required")
    result = pd.DataFrame({"symbol": symbol, "report_date": pd.to_datetime(raw.REPORT_DATE).dt.normalize()})
    clocks = pd.DataFrame({c: pd.to_datetime(raw[c]).dt.normalize() for c in ["NOTICE_DATE", "UPDATE_DATE"]})
    if result.report_date.isna().any() or clocks.isna().any().any() or clocks.NOTICE_DATE.le(result.report_date).any():
        raise ValueError("invalid balance publication clocks")
    if "EITIME" in raw:
        clocks["EITIME"] = pd.to_datetime(raw.EITIME).dt.normalize()
    latest = clocks.max(axis=1)
    positions = sessions.searchsorted(latest, side="right")
    result["available_date"] = [sessions[p] if p < len(sessions) else pd.NaT for p in positions]
    assets = pd.to_numeric(raw.TOTAL_ASSETS, errors="raise")
    liabilities = pd.to_numeric(raw.TOTAL_LIABILITIES, errors="raise")
    if ((assets.notna() & (~np.isfinite(assets) | assets.le(0))) | (liabilities.notna() & (~np.isfinite(liabilities) | liabilities.lt(0)))).any():
        raise ValueError("invalid balance amounts")
    result["debt_ratio"] = liabilities / assets
    result["publication_date"] = clocks.NOTICE_DATE
    result["provider_update_date"] = clocks.UPDATE_DATE
    result["certified"] = False
    if result.duplicated(["symbol", "report_date", "available_date"]).any():
        raise ValueError("ambiguous balance versions")
    return result


def normalize_balance_download(directory, calendar_path, out):
    """Verify a frozen raw acquisition and publish a separate ratio panel."""
    from pathlib import Path
    from io import BytesIO
    import gzip
    import hashlib
    import json
    source, target = Path(directory), Path(out)
    if target.exists() and any(target.iterdir()):
        raise ValueError("balance panel output must be empty")
    manifest_bytes = (source / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("schema") != "issuer_balance_raw_v1":
        raise ValueError("raw issuer balance schema required")
    calendar_bytes = Path(calendar_path).read_bytes()
    calendar = pd.read_csv(BytesIO(calendar_bytes), dtype=str)
    if not {"calendar_date", "is_trading_day"}.issubset(calendar):
        raise ValueError("provider exchange calendar required")
    sessions = pd.to_datetime(calendar.loc[calendar.is_trading_day.eq("1"), "calendar_date"])
    records = {}
    import re
    for entry in manifest.get("superseded_responses", []):
        query = entry["query"]
        filename = query["file"]
        if Path(filename).name != filename or hashlib.sha256((source / filename).read_bytes()).hexdigest() != query["sha256"]:
            raise ValueError("superseded balance raw hash mismatch")
    # Hash every cached request, including unfinished issuers, before selection.
    for query in manifest["queries"].values():
        filename = query["file"]
        if Path(filename).name != filename:
            raise ValueError("invalid balance raw filename")
        raw = (source / filename).read_bytes()
        if hashlib.sha256(raw).hexdigest() != query["sha256"]:
            raise ValueError("balance raw hash mismatch")
        if query.get("status") != "ok" or query["endpoint"] not in ["zcfzbAjaxNew", "legacyBalances"]:
            continue
        raw = gzip.decompress(raw) if raw.startswith(b"\x1f\x8b") else raw
        payload = json.loads(raw)
        if query["endpoint"] == "legacyBalances":
            match = re.search(r'SECUCODE="(\d{6})\.(SH|SZ)"', query["params"]["filter"])
            if not match or payload.get("success") is not True:
                raise ValueError("invalid legacy balance identity/response")
            code = match[2] + match[1]
            result = payload["result"]
            rows = result["data"]
            page, total = int(query["params"]["p"]), int(result["count"])
            if int(result["pages"]) != (total + 199) // 200 or len(rows) != min(200, total - (page-1)*200):
                raise ValueError("legacy balance page count mismatch")
            symbol = code[:2].lower() + "." + code[2:]
            if total != manifest["securities"].get(symbol, {}).get("records"):
                raise ValueError("legacy balance total count mismatch")
            frame = pd.DataFrame(rows)
            observed = pd.to_datetime(frame.REPORT_DATE).dt.normalize()
            allowed = pd.date_range(manifest["request"]["start"], manifest["request"]["end"], freq="QE")
            if not frame.SECURITY_CODE.astype(str).eq(code[2:]).all() or not frame.SECUCODE.eq(code[2:] + "." + code[:2]).all() or observed.duplicated().any() or not observed.isin(allowed).all():
                raise ValueError("legacy balance identity/report scope mismatch")
            records.setdefault(symbol, []).extend(rows)
            continue
        rows = payload["data"]
        code = query["params"]["code"]
        symbol = code[:2].lower() + "." + code[2:]
        dates = pd.to_datetime(query["params"]["dates"].split(","))
        frame = pd.DataFrame(rows)
        if frame.empty or not frame.SECURITY_CODE.astype(str).eq(code[2:]).all():
            raise ValueError("balance raw security identity mismatch")
        observed = pd.to_datetime(frame.REPORT_DATE).dt.normalize()
        if observed.duplicated().any() or set(observed) != set(dates):
            raise ValueError("balance raw report scope mismatch")
        records.setdefault(symbol, []).extend(rows)
    panels, rejected = [], []
    for symbol, info in manifest["securities"].items():
        if info.get("status") != "ok":
            rejected.append(dict(symbol=symbol, reason="unfinished raw acquisition"))
            continue
        rows = records.get(symbol, [])
        if len(rows) != info["records"]:
            raise ValueError("balance issuer record count mismatch")
        if not rows:
            rejected.append(dict(symbol=symbol, reason="no advertised reports in requested interval"))
            continue
        try:
            panels.append(normalize_balance_records(rows, symbol, sessions))
        except (ValueError, TypeError) as exc:
            rejected.append(dict(symbol=symbol, reason=str(exc)))
    if not panels:
        raise ValueError("no verified usable balance records")
    panel = pd.concat(panels, ignore_index=True).sort_values(["symbol", "report_date", "available_date"])
    target.mkdir(parents=True, exist_ok=True)
    panel.to_csv(target / "balance_panel.csv", index=False)
    pd.DataFrame(rejected, columns=["symbol", "reason"]).to_csv(target / "rejected_securities.csv", index=False)
    (target / "input_manifest.json").write_bytes(manifest_bytes)
    (target / "input_calendar.csv").write_bytes(calendar_bytes)
    result = dict(records=len(panel), symbols=int(panel.symbol.nunique()), visible_records=int(panel.available_date.notna().sum()),
                  missing_debt_ratio=int(panel.debt_ratio.isna().sum()), rejected_securities=len(rejected),
                  partial_source=manifest["status"] != "downloaded_raw", certified=False, model_ready=False,
                  input_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                  calendar_sha256=hashlib.sha256(calendar_bytes).hexdigest(),
                  panel_sha256=hashlib.sha256((target / "balance_panel.csv").read_bytes()).hexdigest(),
                  limitations=["Current provider revisions, not archived original financial statements",
                               "Only complete issuer requests used; missing historical statements remain unverified"])
    (target / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result

"""Public quarterly cross-sections with complete-page checks and vintage clocks.

Request schema follows AKShare's stock_yjbb_em source; raw responses retained.
This endpoint returns updated records, not an archive of original announcements.
"""
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
import hashlib
import json
import time

import pandas as pd


def get_financial_page(report_date, page, timeout=20):
    params = dict(sortColumns="UPDATE_DATE,SECURITY_CODE", sortTypes="-1,-1", pageSize="500", pageNumber=str(page),
                  reportName="RPT_LICO_FN_CPD", columns="ALL", filter=f"(REPORTDATE='{report_date}')")
    request = Request("https://datacenter-web.eastmoney.com/api/data/v1/get?" + urlencode(params),
                      headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def fetch_quarter(report_date, path, fetch=get_financial_page, delay=0.2):
    date = pd.Timestamp(report_date)
    if date not in pd.date_range(date - pd.DateOffset(years=1), date, freq="QE"):
        raise ValueError("financial report_date must be quarter end")
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    first = fetch(str(date.date()), 1)
    if first.get("success") is not True or not first.get("result"):
        raise RuntimeError(f"financial provider failed: {first.get('message')}")
    pages, count = int(first["result"]["pages"]), int(first["result"]["count"])
    if not 1 <= pages <= 100 or count < 1 or pages != (count + 499) // 500:
        raise ValueError("invalid financial pagination metadata")
    rows, hashes = [], {}
    for page in range(1, pages + 1):
        payload = first if page == 1 else fetch(str(date.date()), page)
        result = payload.get("result")
        if payload.get("success") is not True or not result or int(result["pages"]) != pages or int(result["count"]) != count:
            raise ValueError("financial page count changed or request failed")
        data = result.get("data")
        if not isinstance(data, list) or not data:
            raise ValueError("empty financial page")
        file = path / f"page_{page:03d}.json"
        file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        hashes[file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
        rows.extend(data)
        if page < pages:
            time.sleep(delay)
    frame = pd.DataFrame(rows)
    required = {"SECUCODE", "REPORTDATE", "NOTICE_DATE", "UPDATE_DATE"}
    if not required.issubset(frame) or len(frame) != count or frame.duplicated(["SECUCODE", "REPORTDATE"]).any():
        raise ValueError("financial rows incomplete, duplicate or missing vintage fields")
    if frame.SECUCODE.isna().any() or not pd.to_datetime(frame.REPORTDATE, errors="raise").dt.normalize().eq(date).all():
        raise ValueError("wrong financial report period or missing security key")
    file = path / "records.csv"
    frame.to_csv(file, index=False)
    hashes[file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
    info = dict(report_date=str(date.date()), rows=len(frame), pages=pages, hashes=hashes,
                retrieved_at=datetime.now(timezone.utc).isoformat(), certified=False,
                vintage_policy="retain NOTICE_DATE, UPDATE_DATE and EITIME; original versions not certified")
    (path / "manifest.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return info


def download_financials(out, start, end, resume=False, fetch=get_financial_page):
    path = Path(out)
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if start > end or pd.isna(start) or pd.isna(end):
        raise ValueError("invalid financial range")
    periods = pd.period_range(start, end, freq="Q")
    dates = periods.end_time.normalize()
    dates = dates[(dates >= start) & (dates <= end)]
    if dates.empty:
        raise ValueError("financial range contains no completed quarter")
    file = path / "manifest.json"
    if resume:
        manifest = json.loads(file.read_text(encoding="utf-8"))
        if manifest.get("start") != str(start.date()) or manifest.get("end") != str(end.date()):
            raise ValueError("financial resume parameters changed")
    else:
        if path.exists() and any(path.iterdir()):
            raise ValueError("financial output must be empty unless resuming")
        path.mkdir(parents=True, exist_ok=True)
        manifest = dict(source="Eastmoney RPT_LICO_FN_CPD", schema="quarterly_financial_raw_v1",
                        start=str(start.date()), end=str(end.date()), synthetic=False, certified=False,
                        quarters={}, status="running", requested_quarters=len(dates))

    def checkpoint():
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        pending = file.with_suffix(".pending.json")
        pending.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        pending.replace(file)

    failures = 0
    manifest["status"] = "running"
    checkpoint()
    for date in dates:
        key = str(date.date())
        quarter_path = path / f"{date.year}Q{date.quarter}"
        prior = manifest["quarters"].get(key)
        if prior and prior.get("status") == "ok":
            for name, digest in prior["hashes"].items():
                if Path(name).name != name or hashlib.sha256((quarter_path / name).read_bytes()).hexdigest() != digest:
                    raise ValueError("financial cache hash mismatch")
            continue
        try:
            info = fetch_quarter(date, quarter_path, fetch)
            info["status"] = "ok"
            manifest["quarters"][key] = info
            failures = 0
        except Exception as exc:
            manifest["quarters"][key] = dict(status="failed", error=str(exc))
            failures += 1
        manifest["saved_quarters"] = sum(q.get("status") == "ok" for q in manifest["quarters"].values())
        checkpoint()
        print(f"Financial acquisition: {manifest['saved_quarters']}/{len(dates)} quarters", flush=True)
        if failures >= 3:
            break
    manifest["status"] = "downloaded_raw" if manifest.get("saved_quarters", 0) == len(dates) else "partial"
    checkpoint()
    return manifest


def normalize_financial_batch(directory, calendar, out):
    """Latest-known provider records with explicit conservative visibility clocks."""
    import re
    source, target = Path(directory), Path(out)
    if target.exists() and any(target.iterdir()):
        raise ValueError("financial normalization output must be empty")
    manifest_bytes = (source / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    sessions = pd.DatetimeIndex(calendar)
    if sessions.empty or sessions.has_duplicates or sessions.hasnans or not sessions.is_monotonic_increasing:
        raise ValueError("financial visibility requires an ordered exchange calendar")
    frames = []
    for period, info in manifest["quarters"].items():
        if info.get("status") != "ok":
            continue
        date = pd.Timestamp(period)
        path = source / f"{date.year}Q{date.quarter}"
        file = path / "records.csv"
        if hashlib.sha256(file.read_bytes()).hexdigest() != info["hashes"].get("records.csv"):
            raise ValueError("quarterly financial records hash mismatch")
        frame = pd.read_csv(file, dtype={"SECUCODE": str, "SECURITY_CODE": str})
        frame["origin_quarter"] = period
        frames.append(frame)
    if not frames:
        raise ValueError("no verified quarterly financial records")
    raw = pd.concat(frames, ignore_index=True)

    def symbol(code):
        match = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", str(code))
        return f"{match[2].lower()}.{match[1]}" if match else None

    result = pd.DataFrame({"symbol": raw.SECUCODE.map(symbol)})
    result["report_date"] = pd.to_datetime(raw.REPORTDATE, errors="coerce").dt.normalize()
    result["publication_date"] = pd.to_datetime(raw.NOTICE_DATE, errors="coerce").dt.normalize()
    result["provider_update_date"] = pd.to_datetime(raw.UPDATE_DATE, errors="coerce").dt.normalize()
    if "EITIME" in raw:
        result["provider_ingest_date"] = pd.to_datetime(raw.EITIME, errors="coerce").dt.normalize()
    else:
        result["provider_ingest_date"] = pd.NaT
    clocks = ["publication_date", "provider_update_date", "provider_ingest_date"]
    latest = result[clocks].max(axis=1)
    known = result.publication_date.notna() & result.provider_update_date.notna() & result.report_date.notna()
    known &= result.publication_date.gt(result.report_date)
    positions = sessions.searchsorted(latest.fillna(pd.Timestamp.max.normalize()), side="right")
    result["available_date"] = [sessions[pos] if valid and pos < len(sessions) else pd.NaT for valid, pos in zip(known, positions)]
    for source_field, field in [("WEIGHTAVG_ROE", "roe"), ("YSTZ", "revenue_growth"), ("SJLTZ", "earnings_growth")]:
        result[field] = pd.to_numeric(raw[source_field], errors="raise") / 100 if source_field in raw else float("nan")
    result["debt_ratio"] = float("nan")  # not supplied by this earnings endpoint
    for source_field, field in [("BASIC_EPS", "eps"), ("BPS", "book_value_per_share"), ("MGJYXJJE", "operating_cashflow_per_share")]:
        result[field] = pd.to_numeric(raw[source_field], errors="raise") if source_field in raw else float("nan")
    result["industry"] = raw["PUBLISHNAME"] if "PUBLISHNAME" in raw else pd.NA
    result["visibility_status"] = "outside_calendar_or_missing_clocks"
    result.loc[result.available_date.notna(), "visibility_status"] = "visible_provider_record_unverified_original"
    result.loc[result.symbol.isna(), "visibility_status"] = "unsupported_security_exchange"
    result["origin_quarter"] = raw.origin_quarter
    target.mkdir(parents=True, exist_ok=True)
    result.to_csv(target / "financial_panel.csv", index=False)
    summary = dict(source=manifest["source"], records=len(result), recognized_symbols=int(result.symbol.nunique()),
                   visible_records=int((result.available_date.notna() & result.symbol.notna()).sum()),
                   update_after_publication=int(result.provider_update_date.gt(result.publication_date).sum()),
                   outside_calendar=int((latest > sessions[-1]).sum()), certified=False,
                   policy="next exchange session after maximum notice/update/ingest date; no original-version certification",
                   debt_ratio_policy="missing; no invented liabilities/assets", partial_source=manifest["status"] != "downloaded_raw",
                   panel_sha256=hashlib.sha256((target / "financial_panel.csv").read_bytes()).hexdigest(),
                   input_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest())
    (target / "input_manifest_snapshot.json").write_bytes(manifest_bytes)
    (target / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary

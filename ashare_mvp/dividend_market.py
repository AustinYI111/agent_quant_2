"""Complete paginated market dividend plans, distinct from payment events."""
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
import hashlib
import json
import time

import pandas as pd


def get_dividend_page(period, page):
    params = dict(reportName="RPT_SHAREBONUS_DET", columns="ALL", pageSize="500", pageNumber=str(page),
                  sortColumns="PLAN_NOTICE_DATE,SECURITY_CODE", sortTypes="-1,-1",
                  filter=f"(REPORT_DATE='{period}')", source="WEB", client="WEB")
    request = Request("https://datacenter-web.eastmoney.com/api/data/v1/get?" + urlencode(params),
                      headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def fetch_dividend_period(period, out, fetch=get_dividend_page, delay=0.2):
    date = pd.Timestamp(period)
    path = Path(out)
    first = fetch(str(date.date()), 1)
    result = first.get("result")
    if first.get("success") is not True or not result:
        raise RuntimeError(f"dividend batch provider failed: {first.get('message')}")
    pages, count = int(result["pages"]), int(result["count"])
    if not 1 <= pages <= 100 or count < 1 or pages != (count + 499) // 500:
        raise ValueError("invalid dividend batch page metadata")
    rows, payloads = [], {}
    for page in range(1, pages + 1):
        payload = first if page == 1 else fetch(str(date.date()), page)
        current = payload.get("result")
        if payload.get("success") is not True or not current or int(current["pages"]) != pages or int(current["count"]) != count:
            raise ValueError("dividend batch pagination changed")
        data = current.get("data")
        expected_rows = min(500, count - (page - 1) * 500)
        if not isinstance(data, list) or len(data) != expected_rows:
            raise ValueError("dividend batch incomplete page")
        rows.extend(data)
        payloads[f"page_{page:03d}.json"] = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if page < pages:
            time.sleep(delay)
    frame = pd.DataFrame(rows)
    required = {"SECUCODE", "REPORT_DATE", "EX_DIVIDEND_DATE", "PLAN_NOTICE_DATE", "NOTICE_DATE",
                "ASSIGN_PROGRESS", "PRETAX_BONUS_RMB", "BONUS_RATIO", "IT_RATIO", "IMPL_PLAN_PROFILE"}
    if not required.issubset(frame) or frame.SECUCODE.isna().any() or frame.duplicated(["SECUCODE", "REPORT_DATE"]).any():
        raise ValueError("dividend batch security/period fields incomplete or ambiguous")
    if not pd.to_datetime(frame.REPORT_DATE, errors="raise").dt.normalize().eq(date).all():
        raise ValueError("dividend batch wrong report period")
    path.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, content in payloads.items():
        (path / name).write_bytes(content)
        hashes[name] = hashlib.sha256(content).hexdigest()
    frame.to_csv(path / "records.csv", index=False)
    hashes["records.csv"] = hashlib.sha256((path / "records.csv").read_bytes()).hexdigest()
    info = dict(rows=count, pages=pages, hashes=hashes, report_date=str(date.date()),
                retrieved_at=datetime.now(timezone.utc).isoformat(), status="ok")
    (path / "manifest.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return info


def download_dividend_market(out, start, end, resume=False, fetch=get_dividend_page):
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if any(pd.isna(day) or day.tz is not None or day != day.normalize() for day in [start, end]) or start > end:
        raise ValueError("invalid dividend batch report range")
    dates = pd.period_range(start, end, freq="Q").end_time.normalize()
    dates = dates[(dates >= start) & (dates <= end)]
    if dates.empty:
        raise ValueError("range contains no quarterly report period")
    path = Path(out)
    if resume:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("schema") != "market_dividend_plans_v1" or manifest.get("start") != str(start.date()) or manifest.get("end") != str(end.date()):
            raise ValueError("dividend batch resume request changed")
    else:
        if path.exists() and any(path.iterdir()):
            raise ValueError("dividend batch output must be empty unless resuming")
        path.mkdir(parents=True, exist_ok=True)
        manifest = dict(schema="market_dividend_plans_v1", source="Eastmoney RPT_SHAREBONUS_DET",
                        start=str(start.date()), end=str(end.date()), requested_periods=len(dates), periods={},
                        certified=False, model_ready=False, synthetic=False,
                        limitations=["plans include cancelled/unexecuted distributions", "payment date not supplied",
                                     "report period is not execution year", "original event vintages and full market coverage unverified"])

    def checkpoint():
        manifest["saved_periods"] = sum(item.get("status") == "ok" for item in manifest["periods"].values())
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        temporary = path / "manifest.pending.json"
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path / "manifest.json")

    manifest["status"] = "running"
    checkpoint()
    failures = 0
    for day in dates:
        key, directory = str(day.date()), path / f"{day.year}Q{day.quarter}"
        prior = manifest["periods"].get(key)
        if prior and prior.get("status") == "ok":
            for name, digest in prior["hashes"].items():
                if Path(name).name != name or hashlib.sha256((directory / name).read_bytes()).hexdigest() != digest:
                    raise ValueError("dividend batch cache hash mismatch")
            continue
        try:
            manifest["periods"][key] = fetch_dividend_period(day, directory, fetch)
            failures = 0
        except Exception as exc:
            manifest["periods"][key] = dict(status="failed", error=str(exc))
            failures += 1
        checkpoint()
        print(f"Dividend batch: {manifest['saved_periods']}/{len(dates)} periods", flush=True)
        if failures >= 3:
            break
    manifest["status"] = "downloaded_raw" if manifest["saved_periods"] == len(dates) else "partial"
    checkpoint()
    return manifest

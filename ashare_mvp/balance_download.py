"""Resumable issuer balance reports; current provider versions, not original archives."""
from pathlib import Path
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import urlencode
import gzip
import hashlib
import json
import re
import time
import pandas as pd
from .public_data import is_ashare

BASE = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/"


def fetch_balance(endpoint, params):
    request = Request(BASE + endpoint + "?" + urlencode(params), headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=25) as response:
        return response.read()


def download_balance_sheets(symbols, start, end, out, resume=False, fetch=None, delay=0.2):
    symbols = sorted(set(symbols))
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if not symbols or not all(is_ashare(s) for s in symbols) or start > end or delay < 0:
        raise ValueError("valid symbols/report interval/delay required")
    request = dict(symbols=symbols, start=str(start.date()), end=str(end.date()))
    path = Path(out); fetch = fetch or fetch_balance
    if resume:
        meta = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if meta.get("schema") != "issuer_balance_raw_v1" or meta.get("request") != request:
            raise ValueError("balance resume request changed")
    else:
        if path.exists() and any(path.iterdir()):
            raise ValueError("balance output must be empty")
        path.mkdir(parents=True, exist_ok=True)
        meta = dict(schema="issuer_balance_raw_v1", source=BASE, request=request, queries={}, securities={}, certified=False, model_ready=False)
    def checkpoint():
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        temporary = path / "manifest.pending.json"
        temporary.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path / "manifest.json")
    def query(key, endpoint, params):
        prior = meta["queries"].get(key)
        if prior and prior.get("status") == "ok":
            if Path(prior["file"]).name != prior["file"]:
                raise ValueError("invalid balance cache filename")
            raw = (path / prior["file"]).read_bytes()
            if Path(prior["file"]).name != prior["file"] or hashlib.sha256(raw).hexdigest() != prior["sha256"]:
                raise ValueError("balance cache hash mismatch")
            decoded = gzip.decompress(raw) if raw.startswith(b"\x1f\x8b") else raw
            if endpoint == "Index" or isinstance(json.loads(decoded).get("data"), list):
                return decoded
            meta.setdefault("superseded_responses", []).append(dict(key=key, query=prior, reason="cached response has no report data list"))
        if prior and prior.get("status") == "unusable_response":
            meta.setdefault("superseded_responses", []).append(dict(key=key, query=prior, reason="retry unusable provider response"))
        raw = fetch(endpoint, params)
        filename = hashlib.sha256(key.encode() + raw).hexdigest() + ".raw"
        (path / filename).write_bytes(raw)
        decoded = gzip.decompress(raw) if raw.startswith(b"\x1f\x8b") else raw
        valid = endpoint == "Index" or isinstance(json.loads(decoded).get("data"), list)
        meta["queries"][key] = dict(status="ok" if valid else "unusable_response", endpoint=endpoint, params=params, file=filename, sha256=hashlib.sha256(raw).hexdigest())
        checkpoint(); time.sleep(delay)
        return gzip.decompress(raw) if raw.startswith(b"\x1f\x8b") else raw
    meta["status"] = "running"; checkpoint(); failures = 0
    for symbol in symbols:
        try:
            code = symbol.replace(".", "").upper()
            html = query(symbol+":type", "Index", dict(type="web", code=code.lower())).decode("utf-8")
            match = re.search(r'<input[^>]*id=["\x27]hidctype["\x27][^>]*value=["\x27]([^"\x27]+)', html)
            if not match:
                raise ValueError("company type unavailable")
            params = dict(companyType=match[1], reportDateType="0", code=code)
            listing = json.loads(query(symbol+":dates", "zcfzbDateAjaxNew", params))
            advertised = pd.to_datetime(pd.DataFrame(listing["data"]).REPORT_DATE).dt.normalize()
            if advertised.isna().any() or advertised.duplicated().any():
                raise ValueError("ambiguous advertised report dates")
            wanted = sorted(advertised[(advertised >= start) & (advertised <= end)])
            rows = []
            for index in range(0, len(wanted), 5):
                dates = wanted[index:index+5]
                arguments = dict(params, reportType="1", dates=",".join(str(d.date()) for d in dates))
                data = json.loads(query(symbol+":reports:"+arguments["dates"], "zcfzbAjaxNew", arguments))["data"]
                frame = pd.DataFrame(data)
                if frame.empty or not frame.SECURITY_CODE.astype(str).eq(symbol.split(".")[1]).all():
                    raise ValueError("balance security identity mismatch")
                observed = pd.to_datetime(frame.REPORT_DATE).dt.normalize()
                if observed.duplicated().any() or set(observed) != set(dates):
                    raise ValueError("balance response missing/unrequested report dates")
                rows.extend(data)
            meta["securities"][symbol] = dict(status="ok", advertised_reports=len(wanted), records=len(rows),
                note="absence from provider dates does not certify historic report completeness")
            failures = 0
        except Exception as exc:
            meta["securities"][symbol] = dict(status="failed", error=str(exc)); failures += 1
        checkpoint()
        if failures >= 3:
            break
    meta["completed_securities"] = sum(v["status"] == "ok" for v in meta["securities"].values())
    meta["status"] = "downloaded_raw" if meta["completed_securities"] == len(symbols) else "partial"
    checkpoint()
    return meta

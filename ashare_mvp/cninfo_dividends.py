"""Public issuer dividend histories including explicit cash-payment dates."""
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
import base64
import hashlib
import json
import time

import pandas as pd

from .public_data import is_ashare


def get_cninfo_history(symbol):
    # Public timestamp encoding used by the website client (AKShare cninfo.js).
    # No account secret or user credentials participate in this protocol.
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding
    key = b"1234567887654321"
    padder = padding.PKCS7(128).padder()
    padded = padder.update(str(int(time.time())).encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key)).encryptor()
    encoded = base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()
    if not is_ashare(symbol):
        raise ValueError("supported A-share symbol required")
    request = Request("https://webapi.cninfo.com.cn/api/sysapi/p_sysapi1139?" + urlencode(dict(scode=symbol.split(".")[1])),
                      data=b"", headers={"Accept-Enckey": encoded, "User-Agent": "Mozilla/5.0",
                                         "Referer": "https://webapi.cninfo.com.cn/"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def download_cninfo_dividends(out, symbols, resume=False, fetch=get_cninfo_history, delay=0.2):
    symbols = sorted(set(symbols))
    if not symbols or any(not is_ashare(code) for code in symbols) or delay < 0:
        raise ValueError("explicit supported A-share symbols and valid delay required")
    path = Path(out)
    if resume:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("schema") != "cninfo_dividend_history_v1" or manifest.get("symbols") != symbols:
            raise ValueError("CNInfo dividend resume request changed")
    else:
        if path.exists() and any(path.iterdir()):
            raise ValueError("CNInfo dividend output must be empty unless resuming")
        path.mkdir(parents=True, exist_ok=True)
        manifest = dict(schema="cninfo_dividend_history_v1", source="CNInfo p_sysapi1139", symbols=symbols,
                        certified=False, model_ready=False, synthetic=False, securities={},
                        limitations=["latest issuer history, original vintages not certified",
                                     "response identity relies on sealed security request; historical renames require reconciliation",
                                     "empty response not independent evidence of absence; terminal settlements not supplied"])

    def checkpoint():
        manifest["saved_securities"] = sum(item.get("status") == "ok" for item in manifest["securities"].values())
        manifest["rows"] = sum(item.get("rows", 0) for item in manifest["securities"].values() if item.get("status") == "ok")
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        temporary = path / "manifest.pending.json"
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path / "manifest.json")

    manifest["status"] = "running"
    checkpoint()
    failures = 0
    for index, symbol in enumerate(symbols):
        prior = manifest["securities"].get(symbol)
        if prior and prior.get("status") == "ok":
            for name, digest in prior["hashes"].items():
                if Path(name).name != name or hashlib.sha256((path / name).read_bytes()).hexdigest() != digest:
                    raise ValueError("CNInfo dividend cache hash mismatch")
            continue
        try:
            payload = fetch(symbol)
            records = payload.get("records")
            if payload.get("resultcode") != 200 or not isinstance(records, list):
                raise ValueError("CNInfo dividend provider failed")
            if int(payload["count"]) != len(records) or int(payload["total"]) != len(records):
                raise ValueError("CNInfo history truncated or pagination required")
            frame = pd.DataFrame(records)
            fields = {"F006D", "F020D", "F023D", "F025D", "F010N", "F011N", "F012N", "F007V", "F001V", "F044V"}
            if records and not fields.issubset(frame):
                raise ValueError("CNInfo dividend date/term schema incomplete")
            if frame.empty:
                frame = pd.DataFrame(columns=sorted(fields))
            hashes = {}
            for suffix, content in [("json", json.dumps(payload, ensure_ascii=False).encode("utf-8")),
                                    ("csv", frame.to_csv(index=False).encode("utf-8"))]:
                file = path / f"{symbol}.{suffix}"
                temporary = file.with_suffix(f".pending.{suffix}")
                temporary.write_bytes(content)
                temporary.replace(file)
                hashes[file.name] = hashlib.sha256(content).hexdigest()
            manifest["securities"][symbol] = dict(status="ok", rows=len(frame), hashes=hashes,
                                                 retrieved_at=datetime.now(timezone.utc).isoformat())
            failures = 0
        except Exception as exc:
            manifest["securities"][symbol] = dict(status="failed", error=str(exc))
            failures += 1
        checkpoint()
        if index % 20 == 0:
            print(f"CNInfo dividends: {manifest['saved_securities']}/{len(symbols)} securities", flush=True)
        if failures >= 3:
            break
        time.sleep(delay)
    manifest["status"] = "downloaded_raw" if manifest["saved_securities"] == len(symbols) else "partial"
    checkpoint()
    return manifest


def normalize_cninfo_dividends(data, calendar_path, out):
    """Seal a completed or partial history snapshot; never hide unresolved terms."""
    from io import BytesIO
    from .dividend_normalization import _terms
    source, target = Path(data), Path(out)
    if target.exists() and any(target.iterdir()):
        raise ValueError("normalized CNInfo output must be empty")
    manifest_bytes = (source / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("schema") != "cninfo_dividend_history_v1":
        raise ValueError("CNInfo source schema mismatch")
    calendar_bytes = Path(calendar_path).read_bytes()
    calendar = pd.read_csv(BytesIO(calendar_bytes), dtype=str)
    dates = calendar.loc[calendar.is_trading_day == "1", "calendar_date"] if {"calendar_date", "is_trading_day"}.issubset(calendar) else calendar["date"]
    cal = pd.DatetimeIndex(pd.to_datetime(dates))
    if cal.empty or cal.has_duplicates or cal.hasnans or not cal.is_monotonic_increasing or cal.tz is not None or not cal.equals(cal.normalize()):
        raise ValueError("invalid CNInfo exchange calendar")
    accepted, issues, outside, raw_count = [], [], 0, 0
    for symbol, info in manifest["securities"].items():
        if info.get("status") != "ok":
            continue
        if symbol not in manifest["symbols"] or not is_ashare(symbol):
            raise ValueError("unexpected CNInfo request identity")
        for name, digest in info["hashes"].items():
            if Path(name).name != name or hashlib.sha256((source / name).read_bytes()).hexdigest() != digest:
                raise ValueError("CNInfo source hash mismatch")
        frame = pd.read_csv(source / f"{symbol}.csv")
        if len(frame) != info["rows"]:
            raise ValueError("CNInfo source row count mismatch")
        raw_count += len(frame)
        for index, row in frame.iterrows():
            try:
                ex_date = pd.Timestamp(row.F020D)
                if pd.isna(ex_date) or ex_date.tz is not None or ex_date != ex_date.normalize():
                    raise ValueError("missing/invalid execution date")
                if ex_date < cal.min() or ex_date > cal.max():
                    outside += 1
                    continue
                if ex_date not in cal:
                    raise ValueError("execution not an exchange session")
                def per_share(field):
                    return float(row[field]) / 10 if pd.notna(row[field]) else float("nan")
                terms = _terms(pd.Series(dict(dividCashStock=row.F007V, dividCashPsBeforeTax=per_share("F012N"),
                                             dividStocksPs=per_share("F010N"), dividReserveToStockPs=per_share("F011N"))))
                notice = pd.Timestamp(row.F006D)
                pay_date = pd.Timestamp(row.F023D) if terms["dividCashPsBeforeTax"] > 0 else ex_date
                if any(pd.isna(day) or day.tz is not None or day != day.normalize() for day in [notice, pay_date]):
                    raise ValueError("missing/invalid implementation notice or cash-payment date")
                position = cal.searchsorted(notice, side="right")
                if position >= len(cal) or cal[position] > ex_date or pay_date < ex_date:
                    raise ValueError("notice/payment clock inconsistent with execution")
                credit = pd.to_datetime(row.F025D, errors="raise") if pd.notna(row.F025D) else pd.NaT
                if pd.notna(credit) and (credit.tz is not None or credit != credit.normalize() or credit < ex_date):
                    raise ValueError("invalid share-credit date")
                accepted.append(dict(symbol=symbol, ex_date=ex_date, available_date=cal[position], pay_date=pay_date,
                    cash_per_share=terms["dividCashPsBeforeTax"],
                    share_multiplier=1 + terms["dividStocksPs"] + terms["dividReserveToStockPs"],
                    announcement_date=notice, share_credit_date=credit, stock_market_date=pd.NaT,
                    source_file=f"{symbol}.csv", source_row=int(index), event_vintage_certified=False))
            except (ValueError, TypeError, KeyError) as exc:
                issues.append(dict(symbol=symbol, source_row=int(index), reason=str(exc)))
    columns = ["symbol", "ex_date", "available_date", "pay_date", "cash_per_share", "share_multiplier",
               "announcement_date", "share_credit_date", "stock_market_date", "source_file", "source_row", "event_vintage_certified"]
    actions = pd.DataFrame(accepted, columns=columns)
    ambiguous = actions.duplicated(["symbol", "ex_date"], keep=False)
    for row in actions.loc[ambiguous].itertuples(index=False):
        issues.append(dict(symbol=row.symbol, source_row=row.source_row,
                           reason="ambiguous security/ex-date: all competing records quarantined"))
    actions = actions.loc[~ambiguous]
    target.mkdir(parents=True, exist_ok=True)
    actions.sort_values(["symbol", "ex_date"]).to_csv(target / "actions.csv", index=False)
    pd.DataFrame(issues, columns=["symbol", "source_row", "reason"]).to_csv(target / "rejected.csv", index=False)
    (target / "input_manifest.json").write_bytes(manifest_bytes)
    (target / "input_calendar.csv").write_bytes(calendar_bytes)
    summary = dict(records=raw_count, accepted=len(actions), rejected=len(issues), outside_calendar=outside,
                   partial_source=manifest.get("status") != "downloaded_raw", certified=False, model_ready=False,
                   status="normalized" if not issues else "partial",
                   input_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                   actions_sha256=hashlib.sha256((target / "actions.csv").read_bytes()).hexdigest(),
                   limitations=["original event vintages unverified", "share credit is not proof of exchange tradability",
                                "accepted subset does not certify complete market or terminal coverage"])
    (target / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary

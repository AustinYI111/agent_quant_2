"""Resumable raw corporate-event evidence; no event-vintage certification."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import time

import pandas as pd

from .public_data import _BoundedClient, _result_frame, is_ashare


def download_dividends(out, symbols, start_year, end_year, resume=False, client=None, delay=0.2):
    symbols = sorted(set(symbols))
    if not symbols or any(not is_ashare(code) for code in symbols):
        raise ValueError("explicit Shanghai/Shenzhen A-share symbols required")
    if not 1990 <= start_year <= end_year <= 2100 or delay < 0:
        raise ValueError("invalid dividend year range/delay")
    path = Path(out)
    request = dict(symbols=symbols, start_year=start_year, end_year=end_year, year_type="operate")
    if resume:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("schema") != "dividend_raw_v1" or manifest.get("request") != request:
            raise ValueError("resume request differs from original dividend acquisition")
    else:
        if path.exists() and any(path.iterdir()):
            raise ValueError("dividend output must be empty unless resuming")
        path.mkdir(parents=True, exist_ok=True)
        manifest = dict(schema="dividend_raw_v1", request=request, source="BaoStock query_dividend_data",
                        certified=False, synthetic=False, status="running", files={}, queries={},
                        limitations=["latest provider event records, original versions not certified",
                                     "empty response is not independent proof of no corporate actions",
                                     "rights, mergers and terminal settlements are not covered by this endpoint"])
    client = client or _BoundedClient(20)

    def checkpoint():
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        manifest["requests_saved"] = len(manifest["files"])
        manifest["requests_expected"] = len(symbols) * (end_year - start_year + 1)
        manifest["rows"] = sum(info["rows"] for info in manifest["files"].values())
        temporary = path / "manifest.pending.json"
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path / "manifest.json")

    fields = {"code", "dividPlanDate", "dividOperateDate", "dividPayDate", "dividStockMarketDate",
              "dividCashPsBeforeTax", "dividStocksPs", "dividReserveToStockPs", "dividCashStock"}
    try:
        if client.login().error_code != "0":
            raise RuntimeError("dividend provider login failed")
        manifest["status"] = "running"
        manifest.pop("error", None)
        checkpoint()
        failures = 0
        for symbol in symbols:
            for year in range(start_year, end_year + 1):
                name = f"{symbol}_{year}.csv"
                file = path / name
                if name in manifest["files"]:
                    if not file.exists() or hashlib.sha256(file.read_bytes()).hexdigest() != manifest["files"][name]["sha256"]:
                        raise ValueError(f"cached dividend evidence changed: {name}")
                    continue
                try:
                    frame = _result_frame(client.query_dividend_data(code=symbol, year=str(year), yearType="operate"))
                    if not fields.issubset(frame) or not frame.code.eq(symbol).all():
                        raise ValueError("dividend schema/security mismatch")
                    # Proposals lacking an execution date remain raw evidence, never an executed event.
                    executed = frame.dividOperateDate.notna() & frame.dividOperateDate.ne("")
                    dates = pd.to_datetime(frame.loc[executed, "dividOperateDate"], errors="raise")
                    if not dates.dt.year.eq(year).all() or dates.dt.tz is not None or not dates.eq(dates.dt.normalize()).all():
                        raise ValueError("ambiguous dividend execution year/date")
                    temporary = file.with_suffix(".pending.csv")
                    frame.to_csv(temporary, index=False)
                    temporary.replace(file)
                    manifest["files"][name] = dict(rows=len(frame), executed_rows=int(executed.sum()),
                                                   duplicate_execution_rows=int(dates.duplicated(keep=False).sum()),
                                                   sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
                                                   retrieved_at=datetime.now(timezone.utc).isoformat())
                    manifest["queries"][name] = dict(status="ok", rows=len(frame))
                    failures = 0
                except Exception as exc:
                    manifest["queries"][name] = dict(status="failed", error=str(exc))
                    failures += 1
                checkpoint()
                if failures >= 3:
                    raise RuntimeError("three consecutive dividend failures; stopped with resumable cache")
                time.sleep(delay)
        manifest["status"] = "downloaded_raw" if len(manifest["files"]) == manifest["requests_expected"] else "partial"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
    finally:
        try:
            client.logout()
        finally:
            checkpoint()
    return manifest

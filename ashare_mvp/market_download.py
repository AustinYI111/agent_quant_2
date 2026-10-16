"""Resumable full cross-section daily-price acquisition, independent of models."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import time
from .checkpoint import replace_checkpoint
from .network_retry import fetch_with_retry

import pandas as pd

from .public_data import _result_frame, is_ashare


def download_market_daily(out, start, end, resume=False, client=None, delay=0.2):
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if pd.isna(start) or pd.isna(end) or start.tz is not None or end.tz is not None or start > end or start != start.normalize() or end != end.normalize() or delay < 0:
        raise ValueError("invalid market-download dates/delay")
    path = Path(out)
    manifest_file = path / "manifest.json"
    if resume:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        if manifest.get("read_only_snapshot"):
            raise ValueError("materialized market snapshot is immutable; resume the original acquisition")
        if manifest.get("start") != str(start.date()) or manifest.get("end") != str(end.date()) or manifest.get("schema") != "market_daily_raw_v1":
            raise ValueError("resume request differs from original acquisition")
    else:
        if path.exists() and any(path.iterdir()):
            raise ValueError("market output must be empty unless resuming")
        path.mkdir(parents=True, exist_ok=True)
        manifest = dict(schema="market_daily_raw_v1", source="BaoStock query_daily_history_k_AStock",
                        start=str(start.date()), end=str(end.date()), synthetic=False, status="running",
                        files={}, queries={}, rows=0, certified=False,
                        limitations=["Provider raw prices; corporate actions not yet reconciled",
                                     "Security coverage and terminal delistings require independent reconciliation",
                                     "Price acquisition is not completion of financial PIT or backtest validation"])
    manifest["network_retry_policy"]={"max_attempts":2,"initial_delay_seconds":1.0,"scope":"transient transport only"}
    if client is None:
        from .provider_session import PersistentQueryClient
        client = PersistentQueryClient(20)

    def checkpoint():
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        pending = manifest_file.with_suffix(".pending.json")
        pending.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        replace_checkpoint(pending, manifest_file)

    def cached(name):
        info = manifest["files"].get(name)
        if info is None:
            return False
        file = path / name
        if not file.exists() or hashlib.sha256(file.read_bytes()).hexdigest() != info["sha256"]:
            raise ValueError(f"cached market file changed: {name}")
        return True

    def save(name, frame):
        file = path / name
        pending = file.with_suffix(".pending.csv")
        frame.to_csv(pending, index=False)
        pending.replace(file)
        manifest["files"][name] = dict(rows=len(frame), columns=list(frame.columns),
                                      sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
                                      retrieved_at=datetime.now(timezone.utc).isoformat())

    try:
        login = fetch_with_retry(client.login)
        if login.error_code != "0":
            raise RuntimeError(f"market provider login {login.error_code}: {login.error_msg}")
        if not cached("calendar.csv"):
            calendar = fetch_with_retry(lambda: _result_frame(client.query_trade_dates(str(start.date()), str(end.date()))))
            save("calendar.csv", calendar)
            checkpoint()
        calendar = pd.read_csv(path / "calendar.csv", dtype=str)
        dates = pd.DatetimeIndex(pd.to_datetime(calendar.loc[calendar.is_trading_day == "1", "calendar_date"]))
        if dates.empty or dates.has_duplicates or dates.hasnans or not dates.is_monotonic_increasing:
            raise ValueError("invalid provider market calendar")
        if dates.tz is not None or not dates.equals(dates.normalize()) or dates.min() < start or dates.max() > end:
            raise ValueError("provider calendar outside requested session dates")
        manifest["sessions_requested"] = len(dates)
        manifest["status"] = "running"
        manifest.pop("error", None)
        checkpoint()
        consecutive_failures = 0
        for index, day in enumerate(dates):
            name = f"daily_{day:%Y%m%d}.csv"
            if cached(name):
                continue
            try:
                frame = fetch_with_retry(lambda: _result_frame(client.query_daily_history_k_AStock(date=str(day.date()))))
                required = {"date", "code", "open", "close", "preclose", "volume", "adjustflag", "tradestatus", "isST"}
                if not required.issubset(frame) or frame.empty:
                    raise ValueError("empty/incomplete provider daily cross-section")
                if frame.code.isna().any() or frame.code.duplicated().any() or not pd.to_datetime(frame.date).eq(day).all():
                    raise ValueError("ambiguous daily cross-section keys")
                if not frame.tradestatus.isin(["0", "1"]).all() or not frame.isST.isin(["0", "1"]).all():
                    raise ValueError("invalid daily historical state")
                if not frame.adjustflag.eq("3").all():
                    raise ValueError("batch endpoint did not return the expected raw-price basis")
                save(name, frame)
                manifest["queries"][str(day.date())] = dict(status="ok", rows=len(frame), ashare_rows=int(frame.code.map(is_ashare).sum()))
                consecutive_failures = 0
            except Exception as exc:
                manifest["queries"][str(day.date())] = dict(status="failed", error=str(exc))
                consecutive_failures += 1
            manifest["sessions_saved"] = sum(1 for name in manifest["files"] if name.startswith("daily_"))
            manifest["rows"] = sum(info["rows"] for name, info in manifest["files"].items() if name.startswith("daily_"))
            checkpoint()
            if consecutive_failures >= 3:
                raise RuntimeError("three consecutive provider failures; acquisition stopped with resumable verified cache")
            if index % 20 == 0:
                print(f"Market acquisition: {manifest['sessions_saved']}/{len(dates)} sessions, {manifest['rows']} raw rows", flush=True)
            time.sleep(delay)
        manifest["status"] = "downloaded_raw" if manifest.get("sessions_saved", 0) == len(dates) else "partial"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
    finally:
        try:
            client.logout()
        finally:
            checkpoint()
    return manifest

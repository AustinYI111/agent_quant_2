"""Reconcile sealed requested lifetimes with daily historical market coverage."""
import csv
import hashlib
import json
from pathlib import Path

import pandas as pd

from .public_data import is_ashare
from .universe import historical_universe


def audit_market_universe(master_directory, market_directory, out):
    master_dir, market_dir, target = map(Path, (master_directory, market_directory, out))
    if target.exists() and any(target.iterdir()):
        raise ValueError("universe audit output must be empty")
    master_bytes = (master_dir / "manifest.json").read_bytes()
    market_bytes = (market_dir / "manifest.json").read_bytes()
    master_meta, market_meta = json.loads(master_bytes), json.loads(market_bytes)
    if master_meta.get("schema") not in ["requested_security_master_v1", "requested_security_master_snapshot_v1"]:
        raise ValueError("verified requested security master required")
    master_raw = (master_dir / "security_master.csv").read_bytes()
    if hashlib.sha256(master_raw).hexdigest() != master_meta.get("master_sha256"):
        raise ValueError("master hash mismatch")
    master = pd.read_csv(master_dir / "security_master.csv", dtype=str).fillna("")
    # Validate lifetimes without materializing millions of membership rows.
    historical_universe(master, pd.DatetimeIndex([]))
    master["listing"] = pd.to_datetime(master.ipoDate)
    master["terminal"] = pd.to_datetime(master.outDate.replace("", pd.NA))
    master = master[master.code.map(is_ashare) & master.type.eq("1")]
    info = market_meta["files"].get("calendar.csv", {})
    cal_raw = (market_dir / "calendar.csv").read_bytes()
    if hashlib.sha256(cal_raw).hexdigest() != info.get("sha256"):
        raise ValueError("calendar hash mismatch")
    calendar = pd.read_csv(market_dir / "calendar.csv", dtype=str)
    dates = pd.DatetimeIndex(pd.to_datetime(calendar.loc[calendar.is_trading_day.eq("1"), "calendar_date"]))
    if dates.empty or dates.has_duplicates or dates.hasnans or not dates.is_monotonic_increasing:
        raise ValueError("invalid market calendar")
    target.mkdir(parents=True, exist_ok=True)
    counts, discrepancies, categories = [], 0, {}
    with (target / "discrepancies.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "symbol", "discrepancy"])
        for date in dates:
            active = master[(master.listing <= date) & (master.terminal.isna() | master.terminal.gt(date))]
            expected = set(active.code)
            name = f"daily_{date:%Y%m%d}.csv"
            file_info = market_meta["files"].get(name)
            if file_info is None:
                counts.append(dict(date=date, status="missing_session", expected=len(expected), observed=None,
                                   missing_lifetime=None, outside_lifetime=None, unknown_master=None, terminal_nontrading=None))
                continue
            raw = (market_dir / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != file_info["sha256"]:
                raise ValueError(f"daily hash mismatch: {name}")
            frame = pd.read_csv(market_dir / name, dtype=str)
            if len(frame) != file_info["rows"] or frame.code.isna().any() or frame.code.duplicated().any() or not pd.to_datetime(frame.date).eq(date).all():
                raise ValueError("invalid daily coverage keys/count/date")
            observed = set(frame.loc[frame.code.map(is_ashare), "code"])
            missing = expected - observed
            extra = observed - expected
            unknown = extra - set(master.code)
            outside = extra - unknown
            terminal = set()
            if "tradestatus" in frame:
                ended_today = set(master.loc[master.terminal.eq(date), "code"])
                nontrading = set(frame.loc[frame.tradestatus.eq("0"), "code"])
                terminal = outside & ended_today & nontrading
                outside -= terminal
            for category, codes in (("missing_lifetime_quote", missing), ("unknown_master", unknown), ("outside_lifetime", outside), ("terminal_nontrading_record", terminal)):
                for code in sorted(codes):
                    writer.writerow([str(date.date()), code, category])
                    discrepancies += 1
                    categories[category] = categories.get(category, 0) + 1
            counts.append(dict(date=date, status="observed", expected=len(expected), observed=len(observed),
                               missing_lifetime=len(missing), outside_lifetime=len(outside), unknown_master=len(unknown), terminal_nontrading=len(terminal)))
    coverage = pd.DataFrame(counts)
    coverage.to_csv(target / "session_coverage.csv", index=False)
    master.to_csv(target / "lifetimes.csv", index=False)
    result = dict(sessions_requested=len(dates), sessions_observed=int(coverage.status.eq("observed").sum()),
                  requested_master_securities=master_meta["requested_securities"], resolved_master_securities=len(master),
                  discrepancies=discrepancies, discrepancy_counts=categories, complete_a_share_market=False, certified=False, model_ready=False,
                  input_master_manifest_sha256=hashlib.sha256(master_bytes).hexdigest(),
                  input_market_manifest_sha256=hashlib.sha256(market_bytes).hexdigest(),
                  limitations=["Requested master does not prove full historical membership; Beijing coverage absent",
                               "Missing quotes are not filled; current status never filters historical membership",
                               "Delisting date is conservatively excluded; nontrading terminal quotes never establish execution or settlement"])
    (target / "input_master_manifest.json").write_bytes(master_bytes)
    (target / "input_market_manifest.json").write_bytes(market_bytes)
    (target / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result

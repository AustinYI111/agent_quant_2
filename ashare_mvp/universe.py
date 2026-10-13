"""Lifetime security-master universe, with dated snapshot reconciliation."""
import pandas as pd
from pathlib import Path
import json
import hashlib

from .public_data import is_ashare


def historical_universe(master, calendar, snapshots=None):
    required = {"code", "ipoDate", "outDate", "type"}
    if not required.issubset(master):
        raise ValueError("security master requires code,ipoDate,outDate,type")
    if master.code.isna().any() or master.code.duplicated().any():
        raise ValueError("security master codes must be unique and nonnull")
    frame = master[master.code.map(is_ashare) & master.type.astype(str).eq("1")].copy()
    frame["listing_date"] = pd.to_datetime(frame.ipoDate, errors="raise")
    # Empty outDate is a provider assertion of no terminal listing date.
    frame["delisting_date"] = pd.to_datetime(frame.outDate.replace("", pd.NA), errors="raise")
    if frame.listing_date.isna().any() or (frame.delisting_date < frame.listing_date).any():
        raise ValueError("invalid security lifetime")
    calendar = pd.DatetimeIndex(calendar)
    if calendar.has_duplicates or not calendar.is_monotonic_increasing or calendar.hasnans:
        raise ValueError("calendar must be unique and ordered")
    rows, differences = [], []
    for date in calendar:
        # Conservatively exclude the delisting date itself; exchange-specific
        # terminal trading date must be supplied before execution simulation.
        active = frame[(frame.listing_date <= date)
                       & (frame.delisting_date.isna() | (frame.delisting_date > date))]
        for stock in active.itertuples(index=False):
            rows.append(dict(date=date, symbol=stock.code, listing_date=stock.listing_date,
                             delisting_date=stock.delisting_date))
        if snapshots is not None and date in snapshots:
            observed = set(snapshots[date].code)
            observed = {code for code in observed if is_ashare(code)}
            calculated = set(active.code)
            for code in sorted(calculated - observed):
                differences.append(dict(date=date, symbol=code, discrepancy="lifetime_only"))
            for code in sorted(observed - calculated):
                differences.append(dict(date=date, symbol=code, discrepancy="snapshot_only"))
    panel = pd.DataFrame(rows, columns=["date", "symbol", "listing_date", "delisting_date"])
    audit = pd.DataFrame(differences, columns=["date", "symbol", "discrepancy"])
    return panel, audit


def build_public_universe(directory, out):
    source, target = Path(directory), Path(out)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if target.exists() and any(target.iterdir()):
        raise ValueError("universe output must be empty")
    frames, snapshots = [], {}
    for name, info in manifest["files"].items():
        if not name.startswith("universe_") or not name.endswith(".csv"):
            continue
        if Path(name).name != name:
            raise ValueError("invalid snapshot file name")
        file = source / name
        if hashlib.sha256(file.read_bytes()).hexdigest() != info["sha256"]:
            raise ValueError(f"snapshot hash mismatch: {name}")
        date = pd.to_datetime(name[len("universe_"):-4], format="%Y%m%d")
        frame = pd.read_csv(file, dtype=str)
        if not {"code", "tradeStatus", "code_name"}.issubset(frame):
            raise ValueError("snapshot requires code,tradeStatus,code_name")
        if frame.code.isna().any() or frame.code.duplicated().any() or not frame.tradeStatus.isin(["0", "1"]).all():
            raise ValueError("invalid snapshot key or trade status")
        frame = frame[frame.code.map(is_ashare)].copy()
        snapshots[date] = frame
        frame["date"] = date
        frame = frame.rename(columns={"code": "symbol", "tradeStatus": "trading_status"})
        frames.append(frame[["date", "symbol", "trading_status", "code_name"]])
    if not frames:
        raise ValueError("no verified historical universe snapshots")
    panel = pd.concat(frames, ignore_index=True).sort_values(["date", "symbol"])
    differences = pd.DataFrame(columns=["date", "symbol", "discrepancy"])
    master_info = manifest["files"].get("security_master.csv")
    if master_info:
        file = source / "security_master.csv"
        if hashlib.sha256(file.read_bytes()).hexdigest() != master_info["sha256"]:
            raise ValueError("security master hash mismatch")
        _, differences = historical_universe(pd.read_csv(file, dtype=str).fillna(""), sorted(snapshots), snapshots)
    target.mkdir(parents=True, exist_ok=True)
    panel.to_csv(target / "universe_panel.csv", index=False)
    differences.to_csv(target / "lifetime_reconciliation.csv", index=False)
    result = dict(source=manifest["source"], snapshots=len(snapshots), rows=len(panel),
                  distinct_symbols=int(panel.symbol.nunique()), dates=[str(d.date()) for d in sorted(snapshots)],
                  counts={str(d.date()): len(f) for d, f in sorted(snapshots.items())},
                  lifetime_master_available=bool(master_info), discrepancies=len(differences) if master_info else None,
                  daily_coverage=False, complete_a_share_market=False,
                  limitations=["Only verified snapshot dates; no interpolation of membership",
                               "Shanghai/Shenzhen coverage; Beijing not yet covered",
                               "Trading status is not an audited limit-up/down execution flag"])
    (target / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result

"""Materialize immutable verified market snapshots, including audited repairs."""
from pathlib import Path
from io import BytesIO
import hashlib
import json
import re

import pandas as pd


def merge_market_snapshots(data, repairs, out):
    base, target = Path(data), Path(out)
    if target.exists() and any(target.iterdir()):
        raise ValueError("merged market output must be empty")
    source_bytes = (base / "manifest.json").read_bytes()
    source = json.loads(source_bytes)
    if source.get("schema") != "market_daily_raw_v1":
        raise ValueError("base market source schema mismatch")
    calendar_bytes = (base / "calendar.csv").read_bytes()
    if hashlib.sha256(calendar_bytes).hexdigest() != source["files"]["calendar.csv"]["sha256"]:
        raise ValueError("base exchange calendar hash mismatch")
    calendar = pd.read_csv(BytesIO(calendar_bytes), dtype=str)
    sessions = pd.DatetimeIndex(pd.to_datetime(calendar.loc[calendar.is_trading_day == "1", "calendar_date"]))
    if sessions.empty or sessions.has_duplicates or sessions.hasnans or not sessions.is_monotonic_increasing:
        raise ValueError("invalid merged market calendar")
    if sessions.tz is not None or not sessions.equals(sessions.normalize()):
        raise ValueError("merged calendar requires naive exchange dates")
    if sessions.min() < pd.Timestamp(source["start"]) or sessions.max() > pd.Timestamp(source["end"]):
        raise ValueError("merged calendar outside requested range")
    candidates = {name: (base / name, info, "base") for name, info in source["files"].items() if name != "calendar.csv"}
    sealed = {"input_base_manifest.json": source_bytes}
    for index, directory in enumerate(repairs):
        directory = Path(directory)
        repair_bytes = (directory / "manifest.json").read_bytes()
        repair = json.loads(repair_bytes)
        origin_bytes = (directory / "input_manifest.json").read_bytes()
        origin = json.loads(origin_bytes)
        if repair.get("schema") != "market_gap_repairs_v1" or hashlib.sha256(origin_bytes).hexdigest() != repair.get("source_manifest_sha256"):
            raise ValueError("repair source lineage hash/schema mismatch")
        if origin.get("schema") != source["schema"] or any(origin.get(key) != source.get(key) for key in ["start", "end"]):
            raise ValueError("repair belongs to a different market request")
        for name, info in origin["files"].items():
            if name not in source["files"] or info["sha256"] != source["files"][name]["sha256"]:
                raise ValueError("base history differs from sealed repair origin")
        failed = {date for date, query in origin["queries"].items() if query.get("status") == "failed"}
        if set(repair["dates"]) != failed:
            raise ValueError("repair request differs from audited failed dates")
        for name, info in repair["files"].items():
            match = re.fullmatch(r"daily_(\d{8})\.csv", name)
            if not match:
                raise ValueError("invalid repair filename")
            day = str(pd.to_datetime(match[1], format="%Y%m%d").date())
            if day not in failed or repair["queries"].get(day, {}).get("status") != "ok":
                raise ValueError("repair file is not a successfully audited failed date")
            if hashlib.sha256((directory / name).read_bytes()).hexdigest() != info["sha256"]:
                raise ValueError("repair cache hash mismatch")
            if name in candidates:
                if candidates[name][1]["sha256"] != info["sha256"]:
                    raise ValueError("conflicting market observations for the same session")
            else:
                candidates[name] = (directory / name, info, f"repair_{index}")
        sealed[f"input_repair_{index}_manifest.json"] = repair_bytes
        sealed[f"input_repair_{index}_origin.json"] = origin_bytes
    # Validate before publishing a readiness manifest. A failed export has no
    # usable manifest; no conflicting cache is selected by source preference.
    files, queries, origins = {"calendar.csv": source["files"]["calendar.csv"]}, {}, {}
    target.mkdir(parents=True, exist_ok=True)
    (target / "calendar.csv").write_bytes(calendar_bytes)
    for name, (path, info, origin) in sorted(candidates.items()):
        match = re.fullmatch(r"daily_(\d{8})\.csv", name)
        if not match:
            raise ValueError("invalid base daily filename")
        day = pd.to_datetime(match[1], format="%Y%m%d")
        if day not in sessions:
            raise ValueError("market snapshot includes a non-requested session")
        if origin == "base" and source["queries"].get(str(day.date()), {}).get("status") != "ok":
            raise ValueError("base daily cache has no successful query record")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != info["sha256"]:
            raise ValueError("market snapshot file hash mismatch")
        frame = pd.read_csv(BytesIO(raw), dtype=str)
        required = {"date", "code", "open", "close", "preclose", "volume", "adjustflag", "tradestatus", "isST"}
        if not required.issubset(frame) or frame.empty or len(frame) != info["rows"]:
            raise ValueError("market snapshot incomplete fields/count")
        if frame.code.isna().any() or frame.code.duplicated().any() or not frame.date.eq(str(day.date())).all():
            raise ValueError("market snapshot date/security keys mismatch")
        if not frame.adjustflag.eq("3").all() or not frame.tradestatus.isin(["0", "1"]).all() or not frame.isST.isin(["0", "1"]).all():
            raise ValueError("market snapshot raw price/state mismatch")
        (target / name).write_bytes(raw)
        files[name] = dict(info, columns=list(frame.columns))
        queries[str(day.date())] = dict(status="ok", rows=len(frame), source=origin)
        origins[name] = origin
    missing = [str(day.date()) for day in sessions if str(day.date()) not in queries]
    prefix = 0
    for day in sessions:
        if str(day.date()) not in queries:
            break
        prefix += 1
    for name, raw in sealed.items():
        (target / name).write_bytes(raw)
    manifest = dict(schema="market_daily_raw_v1", source=source["source"], start=source["start"], end=source["end"],
                    status="partial" if missing else "downloaded_raw", read_only_snapshot=True,
                    sessions_requested=len(sessions), sessions_saved=len(queries),
                    rows=sum(info["rows"] for name, info in files.items() if name != "calendar.csv"),
                    files=files, queries=queries, file_origins=origins, certified=False, synthetic=source.get("synthetic", False),
                    input_sha256={name: hashlib.sha256(raw).hexdigest() for name, raw in sealed.items()})
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame({"date": missing}).to_csv(target / "missing_sessions.csv", index=False)
    summary = dict(sessions_saved=len(queries), sessions_requested=len(sessions), missing_sessions=len(missing),
                   continuous_prefix_sessions=prefix, repair_files_added=sum(origin != "base" for origin in origins.values()),
                   status=manifest["status"], certified=False, model_ready=False,
                   limitations=["session continuity does not certify security membership or terminal coverage",
                                "raw prices still require company-action reconciliation and original financial vintages"])
    (target / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary

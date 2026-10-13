"""Requested security lifetimes via bounded name groups and exact-code fallback."""
from pathlib import Path
from io import BytesIO
import hashlib
import json
import time
from datetime import datetime, timezone

import pandas as pd

from .public_data import _BoundedClient, _result_frame, is_ashare


def download_security_master(targets_path, out, resume=False, client=None, delay=0.2):
    if delay < 0:
        raise ValueError("nonnegative master query delay required")
    raw_targets = Path(targets_path).read_bytes()
    targets = pd.read_csv(BytesIO(raw_targets), dtype=str).fillna("")
    if not {"code", "code_name"}.issubset(targets) or targets.empty or targets.code.duplicated().any() or not targets.code.map(is_ashare).all():
        raise ValueError("unique supported A-share targets with code/code_name required")
    digest = hashlib.sha256(raw_targets).hexdigest()
    path = Path(out)
    if resume:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("schema") != "requested_security_master_v1" or manifest.get("targets_sha256") != digest:
            raise ValueError("security master resume request changed")
    else:
        if path.exists() and any(path.iterdir()):
            raise ValueError("security master output must be empty unless resuming")
        path.mkdir(parents=True, exist_ok=True)
        manifest = dict(schema="requested_security_master_v1", source="BaoStock query_stock_basic",
                        targets_sha256=digest, requested_securities=len(targets), queries={},
                        certified=False, model_ready=False, status="running")
        (path / "input_targets.csv").write_bytes(raw_targets)
    if hashlib.sha256((path / "input_targets.csv").read_bytes()).hexdigest() != digest:
        raise ValueError("sealed master targets changed")
    client = client or _BoundedClient(20)
    frames, failures = [], 0
    fields = {"code", "code_name", "ipoDate", "outDate", "type", "status"}

    def checkpoint():
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        temporary = path / "manifest.pending.json"
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path / "manifest.json")

    def collect(key, arguments):
        nonlocal failures
        prior = manifest["queries"].get(key)
        if prior and prior.get("status") == "ok":
            name = prior["file"]
            if Path(name).name != name or hashlib.sha256((path / name).read_bytes()).hexdigest() != prior["sha256"]:
                raise ValueError("security master cache hash mismatch")
            frame = pd.read_csv(path / name, dtype=str).fillna("")
            if len(frame) != prior["rows"]:
                raise ValueError("security master cache row count mismatch")
        else:
            try:
                frame = _result_frame(client.query_stock_basic(**arguments)).fillna("")
                if not fields.issubset(frame) or frame.code.duplicated().any() or frame.code.eq("").any():
                    raise ValueError("security master query schema/keys incomplete")
                name = f"query_{hashlib.sha256(key.encode()).hexdigest()[:20]}.csv"
                temporary = path / (name + ".pending")
                frame.to_csv(temporary, index=False)
                temporary.replace(path / name)
                manifest["queries"][key] = dict(status="ok", arguments=arguments, rows=len(frame), file=name,
                    retrieved_at=datetime.now(timezone.utc).isoformat(),
                    sha256=hashlib.sha256((path / name).read_bytes()).hexdigest())
                failures = 0
            except Exception as exc:
                manifest["queries"][key] = dict(status="failed", arguments=arguments, error=str(exc))
                failures += 1
                checkpoint()
                return
        frames.append(frame[frame.code.isin(targets.code)].copy())
        checkpoint()
        time.sleep(delay)

    def assemble():
        joined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=sorted(fields))
        grouped = {code: rows for code, rows in joined.groupby("code", sort=False)}
        empty = joined.iloc[:0]
        good, coverage = [], []
        for code in targets.code:
            rows = grouped.get(code, empty)
            if rows.empty:
                coverage.append(dict(code=code, status="missing", reason="no complete query record"))
                continue
            core = rows[["ipoDate", "outDate", "type"]].drop_duplicates()
            try:
                if len(core) != 1:
                    raise ValueError("conflicting lifetime records")
                row = rows.iloc[0]
                listing = pd.Timestamp(row.ipoDate)
                terminal = pd.Timestamp(row.outDate) if row.outDate else pd.NaT
                if row.type != "1" or pd.isna(listing) or listing.tz is not None or listing != listing.normalize():
                    raise ValueError("unknown stock type/listing date")
                if pd.notna(terminal) and (terminal.tz is not None or terminal != terminal.normalize() or terminal < listing):
                    raise ValueError("invalid terminal listing date")
                if row.status == "0" and pd.isna(terminal):
                    raise ValueError("terminated security lacks a dated lifetime end")
                good.append(row)
                coverage.append(dict(code=code, status="resolved", reason="provider lifetime; original identity not certified"))
            except (ValueError, TypeError) as exc:
                coverage.append(dict(code=code, status="unresolved", reason=str(exc)))
        return pd.DataFrame(good, columns=sorted(fields)), pd.DataFrame(coverage)

    manifest["status"] = "running"
    checkpoint()
    try:
        for index, prefix in enumerate(sorted({name.strip()[0] for name in targets.code_name if name.strip()})):
            collect(f"name:{prefix}", dict(code_name=prefix))
            if index % 20 == 0:
                print(f"Master groups: {index + 1}; {len(frames)} complete responses", flush=True)
            if failures >= 3:
                break
        resolved, coverage = assemble()
        if failures < 3:
            for code in coverage.loc[coverage.status == "missing", "code"]:
                collect(f"code:{code}", dict(code=code))
                if failures >= 3:
                    break
        resolved, coverage = assemble()
        resolved.to_csv(path / "security_master.csv", index=False)
        coverage.to_csv(path / "coverage.csv", index=False)
        manifest.update(resolved_securities=len(resolved), status="downloaded_raw" if len(resolved) == len(targets) else "partial",
                        master_sha256=hashlib.sha256((path / "security_master.csv").read_bytes()).hexdigest(),
                        coverage_sha256=hashlib.sha256((path / "coverage.csv").read_bytes()).hexdigest(),
                        limitations=["requested code coverage is not proof of full historical market membership",
                                     "current status/name do not determine past eligibility", "code changes and terminal execution need independent reconciliation"])
    finally:
        client.logout()
        checkpoint()
    return manifest


def merge_security_masters(directories, out):
    """Seal compatible requested masters; never prefer one conflicting lifetime."""
    path = Path(out)
    if path.exists() and any(path.iterdir()):
        raise ValueError("merged master output must be empty")
    sources, frames, targets = [], [], []
    for directory in directories:
        source = Path(directory)
        manifest_bytes = (source / "manifest.json").read_bytes()
        meta = json.loads(manifest_bytes)
        if meta.get("schema") not in ["requested_security_master_v1", "requested_security_master_snapshot_v1"]:
            raise ValueError("requested security master required")
        loaded = {}
        for name, key in [("security_master.csv", "master_sha256"), ("input_targets.csv", "targets_sha256"), ("coverage.csv", "coverage_sha256")]:
            raw = (source / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != meta.get(key):
                raise ValueError(f"master source hash mismatch: {name}")
            loaded[name] = pd.read_csv(BytesIO(raw), dtype=str).fillna("")
        frame, requested = loaded["security_master.csv"], loaded["input_targets.csv"]
        if frame.code.duplicated().any() or requested.code.duplicated().any() or not frame.code.isin(requested.code).all():
            raise ValueError("ambiguous master source keys")
        frames.append(frame); targets.append(requested)
        sources.append(dict(directory=str(source), manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(), manifest=meta))
    if not sources:
        raise ValueError("at least one source required")
    combined = pd.concat(frames, ignore_index=True)
    core = combined[["code", "ipoDate", "outDate", "type"]].drop_duplicates()
    if core.code.duplicated().any():
        raise ValueError("conflicting lifetime records across sources")
    requested = pd.concat(targets, ignore_index=True).drop_duplicates("code", keep="first").sort_values("code")
    resolved = combined.drop_duplicates("code", keep="first").sort_values("code")
    from .universe import historical_universe
    historical_universe(resolved, pd.DatetimeIndex([]))
    coverage = pd.DataFrame({"code": requested.code})
    coverage["status"] = coverage.code.isin(resolved.code).map({True: "resolved", False: "unresolved"})
    coverage["reason"] = "sealed source union; unresolved source records are not fabricated"
    path.mkdir(parents=True, exist_ok=True)
    for name, frame in [("security_master.csv", resolved), ("input_targets.csv", requested), ("coverage.csv", coverage)]:
        frame.to_csv(path / name, index=False)
    result = dict(schema="requested_security_master_snapshot_v1", source="sealed requested master union", read_only_snapshot=True,
                  requested_securities=len(requested), resolved_securities=len(resolved), certified=False, model_ready=False,
                  status="downloaded_raw" if len(requested) == len(resolved) else "partial", sources=sources)
    for name, key in [("security_master.csv", "master_sha256"), ("input_targets.csv", "targets_sha256"), ("coverage.csv", "coverage_sha256")]:
        result[key] = hashlib.sha256((path / name).read_bytes()).hexdigest()
    (path / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result

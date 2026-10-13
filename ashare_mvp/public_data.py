"""Public-source staging: raw provenance, never auto-certify PIT revisions."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import socket
import multiprocessing
import time
from types import SimpleNamespace

import pandas as pd


def _result_frame(result):
    if result.error_code != "0":
        raise RuntimeError(f"provider {result.error_code}: {result.error_msg}")
    rows = []
    while result.next():
        rows.append(result.get_row_data())
    if result.error_code != "0":
        raise RuntimeError(f"provider pagination {result.error_code}: {result.error_msg}")
    # SDK next() can swallow a network receive failure without changing error_code.
    # A normal successful end has an empty or partial final page, not a full one.
    if hasattr(result, "data") and hasattr(result, "per_page_count"):
        if len(result.data) == int(result.per_page_count):
            raise RuntimeError("provider pagination ended on a full page; possible silent truncation")
    return pd.DataFrame(rows, columns=result.fields)


def is_ashare(code):
    return bool(re.fullmatch(r"(?:sh\.(?:600|601|603|605|688)\d{3}|sz\.(?:000|001|002|003|300|301)\d{3})", code))


def _worker(connection, method, args, kwargs, timeout):
    socket.setdefaulttimeout(timeout)
    try:
        import baostock as bs
        # Smaller metadata pages avoid large default responses and keep paging
        # within an individual socket deadline. Patch only this isolated worker.
        if method in ["query_all_stock", "query_stock_basic"]:
            import baostock.common.contants as constants
            constants.BAOSTOCK_PER_PAGE_COUNT = 500
        login = bs.login()
        if login.error_code != "0":
            raise RuntimeError(f"login {login.error_code}: {login.error_msg}")
        frame = _result_frame(getattr(bs, method)(*args, **kwargs))
        # Large Windows pipe messages can stall until the receiver closes.
        # Send schema and bounded row chunks instead of pickling one whole table.
        connection.send(("schema", list(frame.columns)))
        for start in range(0, len(frame), 100):
            connection.send(("rows", frame.iloc[start:start + 100].values.tolist()))
        connection.send(("done", None))
    except Exception as exc:
        try:
            connection.send(("error", str(exc)))
        except (BrokenPipeError, EOFError, OSError):
            pass  # parent has already recorded a timeout and closed the pipe
    finally:
        connection.close()


class _FrameResult:
    error_code = "0"
    error_msg = ""

    def __init__(self, frame):
        self.fields = list(frame.columns)
        self.rows = iter(frame.itertuples(index=False, name=None))

    def next(self):
        self.current = next(self.rows, None)
        return self.current is not None

    def get_row_data(self):
        return list(self.current)


class _BoundedClient:
    """Isolate each SDK query; recv EOF loops cannot hang the parent process."""
    def __init__(self, timeout):
        self.timeout = timeout

    def login(self):
        return SimpleNamespace(error_code="0", error_msg="")

    def logout(self):
        pass

    def __getattr__(self, method):
        if not method.startswith("query_"):
            raise AttributeError(method)

        def query(*args, **kwargs):
            context = multiprocessing.get_context("spawn")
            receiver, sender = context.Pipe(duplex=False)
            process = context.Process(target=_worker, args=(sender, method, args, kwargs, self.timeout))
            process.start()
            sender.close()
            try:
                large = method in ["query_all_stock", "query_stock_industry"] or (method == "query_stock_basic" and not kwargs.get("code"))
                deadline = time.monotonic() + (max(self.timeout, 90) if large else self.timeout) + 5
                columns, rows = None, []
                while True:
                    remaining = max(0, deadline - time.monotonic())
                    if not receiver.poll(remaining):
                        raise TimeoutError(f"{method}: hard deadline exceeded")
                    kind, value = receiver.recv()
                    if kind == "error":
                        raise RuntimeError(value)
                    if kind == "schema":
                        columns = value
                    elif kind == "rows":
                        rows.extend(value)
                    elif kind == "done":
                        if columns is None:
                            raise RuntimeError("provider worker returned no schema")
                        return _FrameResult(pd.DataFrame(rows, columns=columns))
            finally:
                receiver.close()
                process.join(1)
                if process.is_alive():
                    process.terminate()
                    process.join(5)
        return query


def download_public(out, start, end, symbols=None, max_symbols=5, timeout=15, client=None):
    """Download raw samples and dated universe snapshots; explicit bounded run.

    max_symbols=None requests the full historical union. BaoStock coverage is
    Shanghai/Shenzhen; Beijing coverage is not invented or silently asserted.
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if start > end or start != start.normalize() or end != end.normalize():
        raise ValueError("start/end must be ordered session dates")
    if timeout <= 0 or (max_symbols is not None and max_symbols < 1):
        raise ValueError("invalid timeout/max_symbols")
    path = Path(out)
    if path.exists() and any(path.iterdir()):
        raise ValueError("public staging output must be empty")
    if client is None:
        client = _BoundedClient(timeout)
    path.mkdir(parents=True, exist_ok=True)
    manifest = dict(source="BaoStock", retrieved_at=datetime.now(timezone.utc).isoformat(),
                    start=str(start.date()), end=str(end.date()), synthetic=False, files={}, queries=[],
                    requested_symbols=symbols, max_symbols=max_symbols,
                    training_ready=False,
                    blockers=["Financial original/revision vintage not certified",
                              "No audited limit-up/down execution flags or terminal delisting valuations",
                              "Adjusted price total-return methodology requires reconciliation",
                              "Public coverage does not prove all A shares including Beijing",
                              "Monthly snapshots alone may miss transient listings; reconcile lifetime security master"])
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    logged_in = False

    def fetch(name, query):
        try:
            frame = _result_frame(query())
            file = path / f"{name}.csv"
            frame.to_csv(file, index=False)
            manifest["files"][file.name] = dict(rows=len(frame), columns=list(frame.columns),
                                                sha256=hashlib.sha256(file.read_bytes()).hexdigest())
            manifest["queries"].append(dict(name=name, status="ok", rows=len(frame)))
            return frame
        except Exception as exc:
            manifest["queries"].append(dict(name=name, status="failed", error=str(exc)))
            return None

    try:
        login = client.login()
        if login.error_code != "0":
            raise RuntimeError(f"BaoStock login {login.error_code}: {login.error_msg}")
        logged_in = True
        calendar = fetch("trade_dates", lambda: client.query_trade_dates(str(start.date()), str(end.date())))
        if calendar is None or calendar.empty:
            raise RuntimeError("no exchange calendar returned")
        sessions = pd.DatetimeIndex(pd.to_datetime(calendar.loc[calendar.is_trading_day == "1", "calendar_date"]))
        if sessions.empty:
            raise ValueError("no sessions in requested interval")
        # Monthly historical union; daily ST/status comes from historical bars.
        checkpoints = pd.Series(sessions).groupby(sessions.to_period("M")).last().tolist()
        checkpoints = sorted(set([sessions[0], *checkpoints]))
        universe = set()
        master = fetch("security_master", client.query_stock_basic)
        if master is not None and {"code", "ipoDate", "outDate", "type"}.issubset(master):
            ipo = pd.to_datetime(master.ipoDate, errors="coerce")
            out_date = pd.to_datetime(master.outDate, errors="coerce")
            active_in_window = (master.type == "1") & (ipo <= end) & (out_date.isna() | (out_date >= start))
            universe.update(c for c in master.loc[active_in_window, "code"] if is_ashare(c))
        for day in checkpoints:
            frame = fetch(f"universe_{day:%Y%m%d}", lambda d=day: client.query_all_stock(str(d.date())))
            if frame is not None:
                universe.update(c for c in frame.code if is_ashare(c))
        chosen = list(symbols) if symbols is not None else sorted(universe)
        if not all(is_ashare(c) for c in chosen) or len(chosen) != len(set(chosen)):
            raise ValueError("symbols must be unique Shanghai/Shenzhen A-share codes, e.g. sh.600000")
        if max_symbols is not None:
            chosen = chosen[:max_symbols]
        manifest["downloaded_symbols"] = chosen
        fields = "date,code,open,high,low,close,preclose,volume,amount,tradestatus,isST,peTTM,pbMRQ"
        for code in chosen:
            key = code.replace(".", "_")
            fetch(f"basic_{key}", lambda c=code: client.query_stock_basic(code=c))
            for adjust, label in [("3", "raw"), ("1", "adjusted")]:
                fetch(f"prices_{label}_{key}", lambda c=code, a=adjust: client.query_history_k_data_plus(
                    c, fields, start_date=str(start.date()), end_date=str(end.date()), frequency="d", adjustflag=a))
            periods = pd.period_range(start - pd.DateOffset(years=1), end, freq="Q")
            for period in periods[periods.end_time.normalize() <= end]:
                for kind in ["profit", "growth", "balance"]:
                    fetch(f"{kind}_{key}_{period.year}Q{period.quarter}",
                          lambda c=code, p=period, k=kind: getattr(client, f"query_{k}_data")(
                              code=c, year=p.year, quarter=p.quarter))
        fetch("industry_current", client.query_stock_industry)
        manifest["status"] = "partial" if any(q["status"] == "failed" for q in manifest["queries"]) else "downloaded_raw"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
    finally:
        if logged_in:
            try:
                client.logout()
            except Exception:
                pass
        socket.setdefaulttimeout(old_timeout)
        (path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def readiness_audit(directory):
    path = Path(directory)
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    missing_values, financial_dates, bad_hashes = [], [], []
    for name, info in manifest["files"].items():
        if Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("manifest file names must remain inside the staging directory")
        file = path / name
        if hashlib.sha256(file.read_bytes()).hexdigest() != info["sha256"]:
            bad_hashes.append(name)
        frame = pd.read_csv(file, dtype=str)
        for field in frame:
            count = int(frame[field].isna().sum())
            if count:
                missing_values.append(dict(file=name, field=field, missing=count, rows=len(frame)))
        if {"pubDate", "statDate"}.issubset(frame):
            publication = pd.to_datetime(frame.pubDate, errors="coerce")
            report = pd.to_datetime(frame.statDate, errors="coerce")
            financial_dates.append(dict(file=name, rows=len(frame), invalid_dates=int((publication.isna() | report.isna()).sum()),
                                        publication_not_after_period=int((publication <= report).sum())))
    result = dict(source=manifest["source"], status=manifest["status"], hash_mismatches=bad_hashes,
                  failed_queries=[q for q in manifest["queries"] if q["status"] != "ok"],
                  missing_values=missing_values, financial_date_checks=financial_dates,
                  training_ready=False, blockers=manifest["blockers"])
    (path / "readiness_audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result

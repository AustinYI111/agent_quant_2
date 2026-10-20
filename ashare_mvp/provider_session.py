"""Reusable isolated BaoStock session with bounded queries and recovery."""
import multiprocessing
import socket
import time
from types import SimpleNamespace
import pandas as pd
from .public_data import _result_frame, _FrameResult


def _session_worker(connection, timeout):
    socket.setdefaulttimeout(timeout)
    provider = None
    try:
        import baostock as provider
        import baostock.common.contants as constants
        constants.BAOSTOCK_PER_PAGE_COUNT = 500
        login = provider.login()
        if login.error_code != "0":
            raise RuntimeError(f"login {login.error_code}: {login.error_msg}")
        connection.send(("ready", None))
        while True:
            request = connection.recv()
            if request is None:
                break
            method, args, kwargs = request
            frame = _result_frame(getattr(provider, method)(*args, **kwargs))
            connection.send(("schema", list(frame.columns)))
            for start in range(0, len(frame), 100):
                connection.send(("rows", frame.iloc[start:start+100].values.tolist()))
            connection.send(("done", None))
    except Exception as exc:
        try:
            connection.send(("error", str(exc)))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()
        if provider is not None:
            try:
                provider.logout()
            except Exception:
                pass


class PersistentQueryClient:
    def __init__(self, timeout=20, worker=_session_worker):
        if not 0 < timeout < float("inf"):
            raise ValueError("positive finite session timeout required")
        self.timeout = timeout
        self._worker = worker
        self._process = self._connection = None

    def _stop(self):
        connection, process = self._connection, self._process
        self._connection = self._process = None
        if connection is not None:
            connection.close()
        if process is not None and process.pid is not None:
            process.join(1)
            if process.is_alive():
                process.terminate()
                process.join(5)

    def _receive(self, deadline):
        if not self._connection.poll(max(0, deadline-time.monotonic())):
            raise TimeoutError("provider session hard deadline exceeded")
        return self._connection.recv()

    def _start(self):
        if self._process is not None and self._process.is_alive():
            return
        self._stop()
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        process = context.Process(target=self._worker, args=(child,self.timeout), daemon=True)
        self._process, self._connection = process, parent
        try:
            process.start()
            child.close()
            kind, value = self._receive(time.monotonic()+self.timeout+5)
            if kind != "ready":
                raise RuntimeError(value or "provider returned invalid startup response")
        except Exception:
            child.close()
            self._stop()
            raise

    def login(self):
        self._start()
        return SimpleNamespace(error_code="0",error_msg="")

    def logout(self):
        try:
            if self._connection is not None and self._process.is_alive():
                self._connection.send(None)
        finally:
            self._stop()

    def __getattr__(self, method):
        if not method.startswith("query_"):
            raise AttributeError(method)
        def query(*args, **kwargs):
            self._start()
            try:
                self._connection.send((method,args,kwargs))
                large = method in ["query_all_stock","query_stock_industry","query_daily_history_k_AStock"] or (method=="query_stock_basic" and not kwargs.get("code"))
                deadline = time.monotonic()+(max(self.timeout,90) if large else self.timeout)+5
                columns, rows = None, []
                while True:
                    kind,value = self._receive(deadline)
                    if kind=="error":
                        raise RuntimeError(value)
                    if kind=="schema": columns=value
                    elif kind=="rows": rows.extend(value)
                    elif kind=="done":
                        if columns is None: raise RuntimeError("provider returned no schema")
                        return _FrameResult(pd.DataFrame(rows,columns=columns))
                    else: raise RuntimeError("invalid provider session response")
            except Exception:
                self._stop()
                raise
        return query

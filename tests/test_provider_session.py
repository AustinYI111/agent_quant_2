import time
import pytest
from ashare_mvp.provider_session import PersistentQueryClient
from ashare_mvp.public_data import _result_frame


def fake_worker(connection,timeout):
    connection.send(("ready",None)); count=0
    try:
        while True:
            request=connection.recv()
            if request is None: return
            method,args,kwargs=request
            if method=="query_error":
                connection.send(("error","provider failure")); return
            if method=="query_wait": time.sleep(20)
            count+=1
            connection.send(("schema",["request_count"]))
            connection.send(("rows",[[count]]))
            connection.send(("done",None))
    finally:
        connection.close()


def test_reuses_worker_and_does_not_repeat_login_for_successful_queries():
    client=PersistentQueryClient(5,worker=fake_worker)
    try:
        client.login(); process=client._process
        assert _result_frame(client.query_sample()).iloc[0,0]==1
        assert _result_frame(client.query_sample()).iloc[0,0]==2
        assert client._process is process
    finally: client.logout()
    assert not process.is_alive()


def test_error_restarts_session_instead_of_reusing_broken_stream():
    client=PersistentQueryClient(5,worker=fake_worker)
    try:
        client.login(); process=client._process
        with pytest.raises(RuntimeError,match="provider failure"): client.query_error()
        assert not process.is_alive() and client._process is None
        assert _result_frame(client.query_sample()).iloc[0,0]==1
        assert client._process is not process
    finally: client.logout()


def test_hard_timeout_terminates_worker():
    client=PersistentQueryClient(.2,worker=fake_worker)
    try:
        client.login(); process=client._process
        with pytest.raises(TimeoutError,match="hard deadline"): client.query_wait()
        assert not process.is_alive() and client._process is None
    finally: client.logout()


def test_process_start_failure_preserves_original_error_and_cleans_handles(monkeypatch):
    from ashare_mvp import provider_session
    real_context=provider_session.multiprocessing.get_context("spawn")
    class BrokenProcess:
        pid=None
        def start(self): raise OSError("worker launch failed")
        def join(self,*args): raise AssertionError("unstarted process must not be joined")
    class Context:
        Pipe=staticmethod(real_context.Pipe)
        def Process(self,**kwargs): return BrokenProcess()
    monkeypatch.setattr(provider_session.multiprocessing,"get_context",lambda _:Context())
    client=PersistentQueryClient(5,worker=fake_worker)
    with pytest.raises(OSError,match="worker launch failed"): client.login()
    assert client._connection is None and client._process is None

from urllib.error import HTTPError
import pytest
from ashare_mvp import network_retry


def test_transient_failure_retries_same_request_then_returns(monkeypatch):
    calls=[];sleeps=[]
    monkeypatch.setattr(network_retry.time,"sleep",sleeps.append)
    def fetch():
        calls.append(1)
        if len(calls)==1: raise TimeoutError("temporary")
        return b"verified later by caller"
    assert network_retry.fetch_with_retry(fetch)==b"verified later by caller"
    assert len(calls)==2 and sleeps==[1.0]


def test_repeated_failure_is_bounded(monkeypatch):
    calls=[]
    monkeypatch.setattr(network_retry.time,"sleep",lambda _:None)
    def fetch(): calls.append(1);raise RuntimeError("provider 10002007: receive error")
    with pytest.raises(RuntimeError,match="10002007"): network_retry.fetch_with_retry(fetch)
    assert len(calls)==2


@pytest.mark.parametrize("error",[ValueError("wrong security"),HTTPError("https://example.test",403,"denied",{},None),HTTPError("https://example.test",503,"busy",{"Retry-After":"600"},None)])
def test_data_errors_permissions_and_explicit_cooldowns_not_retried(error):
    calls=[]
    def fetch(): calls.append(1);raise error
    with pytest.raises(type(error)): network_retry.fetch_with_retry(fetch)
    assert len(calls)==1


def test_temporary_http_failure_releases_response_before_retry(monkeypatch):
    from io import BytesIO
    body=BytesIO(b"temporary source error")
    calls=[]
    monkeypatch.setattr(network_retry.time,"sleep",lambda _:None)
    def fetch():
        calls.append(1)
        if len(calls)==1: raise HTTPError("https://example.test",503,"busy",{},body)
        return "complete response"
    assert network_retry.fetch_with_retry(fetch)=="complete response"
    assert len(calls)==2 and body.closed

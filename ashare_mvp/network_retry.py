"""Bounded retry for transient public-source transport failures only."""
import math
import socket
import time
from urllib.error import HTTPError, URLError


def transient_network_error(error):
    if isinstance(error, HTTPError):
        # Never repeat this request on explicit cooldown or permission errors.
        return error.code in {500,502,503,504} and not (error.headers and error.headers.get("Retry-After"))
    if isinstance(error,(TimeoutError,ConnectionError,EOFError,socket.gaierror,URLError)):
        return True
    return isinstance(error,RuntimeError) and any(term in str(error) for term in
        ["10002007","hard deadline exceeded","pagination ended on a full page"])


def fetch_with_retry(fetch, attempts=2, delay=1.0):
    if not isinstance(attempts,int) or attempts<1 or not math.isfinite(delay) or delay<0:
        raise ValueError("positive attempts and finite nonnegative delay required")
    for attempt in range(attempts):
        try:
            return fetch()
        except Exception as exc:
            if attempt==attempts-1 or not transient_network_error(exc):
                raise
            if isinstance(exc,HTTPError): exc.close()
            time.sleep(delay*(2**attempt))

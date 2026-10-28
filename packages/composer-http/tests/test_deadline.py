"""Tests for the total request deadline (:mod:`composer_http.deadline`), against a real server.

The server trickles: one byte every :data:`GAP_S`, far inside httpx's
per-read timeout, so only a total deadline can end the request.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest
from composer_http import DeadlineTransport, SourceSession, get_text, new_client

#: Gap between trickled bytes; each read waits this long, never the timeout.
GAP_S = 0.3
#: The total deadline under test.
TOTAL_S = 1.0
#: Slack for scheduling on a loaded CI runner: the deadline must land well
#: before the trickle would have finished on its own (~30 bytes * GAP_S).
SLACK_S = 0.8

_HEADERS = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 30\r\n\r\n"


class _Server:
    """A local HTTP server; ``/body`` trickles the body, ``/headers`` the whole response."""

    def __init__(self) -> None:
        self.stop = threading.Event()
        self.hits: list[str] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                server.hits.append(self.path)
                if self.path == "/fast":
                    self.wfile.write(_HEADERS.replace(b"30", b"2") + b"ok")
                    return
                head, body = (_HEADERS, b"x" * 30) if self.path == "/body" else (b"", _HEADERS)
                self.wfile.write(head)
                for byte in body:
                    if server.stop.is_set():
                        return
                    try:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                    except OSError:  # the client gave up and hung up
                        return
                    time.sleep(GAP_S)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"


@pytest.fixture
def trickle() -> Iterator[_Server]:
    server = _Server()
    thread = threading.Thread(target=server.httpd.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    yield server
    server.stop.set()
    server.httpd.shutdown()
    server.httpd.server_close()


def _elapsed(call: Callable[[], object]) -> tuple[float, BaseException]:
    started = time.monotonic()
    with pytest.raises(httpx.TimeoutException) as caught:
        call()
    return time.monotonic() - started, caught.value


def test_a_trickled_body_fails_at_the_total_deadline(trickle: _Server) -> None:
    with new_client(timeout=TOTAL_S) as client:
        elapsed, exc = _elapsed(lambda: client.get(f"{trickle.url}/body"))
    assert isinstance(exc, httpx.ReadTimeout)
    assert "total deadline of 1s" in str(exc)
    assert TOTAL_S <= elapsed < TOTAL_S + SLACK_S


def test_trickled_headers_fail_at_the_total_deadline(trickle: _Server) -> None:
    """The status line and headers arrive inside one httpcore call, so a
    deadline checked between body chunks would never see this one."""
    with new_client(timeout=TOTAL_S) as client:
        elapsed, exc = _elapsed(lambda: client.get(f"{trickle.url}/headers"))
    assert isinstance(exc, httpx.ReadTimeout)
    assert TOTAL_S <= elapsed < TOTAL_S + SLACK_S


def test_a_streamed_body_fails_at_the_total_deadline(trickle: _Server) -> None:
    """Under ``client.stream`` the caller pulls the body after the transport
    has returned; the deadline still started when the request was sent."""
    received: list[bytes] = []

    def stream() -> None:
        with new_client(timeout=TOTAL_S) as client, client.stream("GET", f"{trickle.url}/body") as resp:
            received.extend(resp.iter_raw())

    elapsed, _ = _elapsed(stream)
    assert TOTAL_S <= elapsed < TOTAL_S + SLACK_S
    assert 0 < len(b"".join(received)) < 30


def test_each_request_gets_a_fresh_deadline(trickle: _Server) -> None:
    """The deadline is per request, not per client: a reused client that
    timed out once still answers the next request."""
    with new_client(timeout=TOTAL_S) as client:
        _elapsed(lambda: client.get(f"{trickle.url}/body"))
        assert client.get(f"{trickle.url}/fast").text == "ok"


def test_a_missed_deadline_is_retried(trickle: _Server, monkeypatch: pytest.MonkeyPatch) -> None:
    # skip the backoff without stubbing time.sleep itself, which the server sleeps on too
    monkeypatch.setattr("composer_http.time", SimpleNamespace(sleep=lambda _: None))
    with new_client(timeout=TOTAL_S) as client:
        elapsed, _ = _elapsed(lambda: get_text(client, f"{trickle.url}/body", label="trickle", retries=2))
    assert trickle.hits == ["/body", "/body"]
    assert 2 * TOTAL_S <= elapsed < 2 * (TOTAL_S + SLACK_S)


def test_a_session_survives_a_missed_deadline(trickle: _Server) -> None:
    with SourceSession("test", new_client(timeout=TOTAL_S), delay_s=0.0) as session:
        with pytest.raises(httpx.ReadTimeout):
            session.get_text(f"{trickle.url}/body", label="trickle", retries=1)
        assert session.requests == 1
        assert session.get_text(f"{trickle.url}/fast", label="fast") == "ok"


def test_the_per_read_timeout_still_applies() -> None:
    """A server that goes silent trips httpx's own read timeout, with httpx's
    own message, well before a generous total deadline."""
    listener = socket.create_server(("127.0.0.1", 0))
    accepted: list[socket.socket] = []
    threading.Thread(target=lambda: accepted.append(listener.accept()[0]), daemon=True).start()
    try:
        with httpx.Client(timeout=httpx.Timeout(10.0, read=0.3), transport=DeadlineTransport(10.0)) as client:
            started = time.monotonic()
            with pytest.raises(httpx.ReadTimeout) as caught:
                client.get(f"http://127.0.0.1:{listener.getsockname()[1]}/")
        assert time.monotonic() - started < 2.0
        assert "total deadline" not in str(caught.value)
    finally:
        for conn in accepted:
            conn.close()
        listener.close()

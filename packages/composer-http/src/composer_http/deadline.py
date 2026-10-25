"""A total wall-clock deadline per request, which httpx does not have.

httpx's timeouts are per operation: ``read=90`` means no single socket read may
wait more than 90 seconds, not that the response must be in within 90 seconds. A
server that trickles a byte now and then, or one that keeps the connection open
and dribbles, resets that clock on every byte and can hold a fetch for as long as
it likes — one WDQS request ran for 27 minutes under a 90-second timeout (#365).

:class:`DeadlineTransport` adds the missing bound. Each request gets a deadline
when it is sent, and every socket operation it makes — connect, TLS handshake,
writing the request, reading the headers, reading the body — waits at most for
whatever is left of it. That is done in the network backend under httpcore's
connection pool rather than by checking the clock between chunks, because a
response's headers arrive inside one httpcore call that reads as often as it
needs to: only the socket layer sees every wait.

Expiry raises the httpx timeout of the phase it interrupted (:class:`httpx.ReadTimeout`
mid-body, say), so callers retry it like any other transport error. The deadline
covers one round trip; a followed redirect is a new request with a deadline of
its own, as is each retry.
"""

from __future__ import annotations

import ssl
import time
from collections.abc import Iterable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import httpcore
import httpx

__all__ = ["DeadlineTransport"]


@dataclass(frozen=True)
class _Budget:
    """When the request in flight must be done by, and the total it was given."""

    deadline: float
    total_s: float

    def expired(self) -> bool:
        return time.monotonic() >= self.deadline

    def clamp(self, timeout: float | None, error: type[httpcore.TimeoutException]) -> float:
        """*timeout* shortened to what is left; *error* once nothing is."""
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise error(self.message())
        return remaining if timeout is None else min(timeout, remaining)

    def message(self) -> str:
        return f"request exceeded its total deadline of {self.total_s:g}s"


#: The budget of the request whose socket operations are running, if it has one.
_budget: ContextVar[_Budget | None] = ContextVar("composer_http_budget", default=None)


class _DeadlineStream(httpcore.NetworkStream):
    """A connection whose every wait is bounded by the current request's deadline."""

    def __init__(self, stream: httpcore.NetworkStream) -> None:
        self._stream = stream

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        budget = _budget.get()
        if budget is None:
            return self._stream.read(max_bytes, timeout)
        try:
            return self._stream.read(max_bytes, budget.clamp(timeout, httpcore.ReadTimeout))
        except httpcore.ReadTimeout as exc:
            if budget.expired():
                raise httpcore.ReadTimeout(budget.message()) from exc
            raise

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        budget = _budget.get()
        if budget is None:
            return self._stream.write(buffer, timeout)
        try:
            self._stream.write(buffer, budget.clamp(timeout, httpcore.WriteTimeout))
        except httpcore.WriteTimeout as exc:
            if budget.expired():
                raise httpcore.WriteTimeout(budget.message()) from exc
            raise

    def close(self) -> None:
        self._stream.close()

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        budget = _budget.get()
        if budget is not None:
            timeout = budget.clamp(timeout, httpcore.ConnectTimeout)
        return _DeadlineStream(self._stream.start_tls(ssl_context, server_hostname, timeout))

    def get_extra_info(self, info: str) -> Any:
        return self._stream.get_extra_info(info)


class _DeadlineBackend(httpcore.NetworkBackend):
    """httpcore's blocking socket backend, handing out :class:`_DeadlineStream` connections."""

    def __init__(self) -> None:
        self._backend = httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        budget = _budget.get()
        if budget is not None:
            timeout = budget.clamp(timeout, httpcore.ConnectTimeout)
        stream = self._backend.connect_tcp(host, port, timeout, local_address, socket_options)
        return _DeadlineStream(stream)

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        budget = _budget.get()
        if budget is not None:
            timeout = budget.clamp(timeout, httpcore.ConnectTimeout)
        return _DeadlineStream(self._backend.connect_unix_socket(path, timeout, socket_options))

    def sleep(self, seconds: float) -> None:
        self._backend.sleep(seconds)


class _BudgetedBody(httpx.SyncByteStream):
    """A response body that reads under its request's budget.

    httpx reads the body after :meth:`DeadlineTransport.handle_request` has
    returned — :meth:`httpx.Client.send` reads it, or the caller does chunk by
    chunk under :meth:`httpx.Client.stream` — so each chunk re-enters the budget.
    """

    def __init__(self, stream: httpx.SyncByteStream, budget: _Budget) -> None:
        self._stream = stream
        self._budget = budget

    def __iter__(self) -> Iterator[bytes]:
        chunks = iter(self._stream)
        while True:
            token = _budget.set(self._budget)
            try:
                chunk = next(chunks)
            except StopIteration:
                return
            finally:
                _budget.reset(token)
            yield chunk

    def close(self) -> None:
        self._stream.close()


class DeadlineTransport(httpx.HTTPTransport):
    """:class:`httpx.HTTPTransport` with a total deadline of *total_s* per request.

    Otherwise httpx's defaults: TLS verification, connection limits, no proxy.
    """

    def __init__(self, total_s: float) -> None:
        super().__init__()
        limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)  # httpx's defaults
        self.total_s = total_s
        # the pool the base class built, again, over sockets that watch the clock
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            network_backend=_DeadlineBackend(),
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        budget = _Budget(deadline=time.monotonic() + self.total_s, total_s=self.total_s)
        token = _budget.set(budget)
        try:
            response = super().handle_request(request)
        finally:
            _budget.reset(token)
        assert isinstance(response.stream, httpx.SyncByteStream)
        response.stream = _BudgetedBody(response.stream, budget)
        return response

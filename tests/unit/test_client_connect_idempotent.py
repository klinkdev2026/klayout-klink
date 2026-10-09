"""KLinkClient.connect() must be idempotent while the client is live.

`with KLinkClient().connect() as c:` calls connect() and then __enter__ ->
connect(); that must open ONE transport connection and ONE reader thread.
After close(), a later connect() must reconnect normally.
"""

from __future__ import annotations

import threading

from klink.client import KLinkClient


class FakeTransport:
    """Stands in for NDJSONTransport: recv_line blocks until close()."""

    def __init__(self):
        self.connect_calls = 0
        self.close_calls = 0
        self._closed = threading.Event()

    def connect(self) -> None:
        self.connect_calls += 1
        self._closed = threading.Event()

    def send(self, obj: dict) -> None:  # pragma: no cover - unused here
        pass

    def recv_line(self):
        self._closed.wait(timeout=5.0)
        return None

    def close(self) -> None:
        self.close_calls += 1
        self._closed.set()


def _reader_threads():
    return [t for t in threading.enumerate()
            if t.name == "klink-reader" and t.is_alive()]


def _client_with_fake():
    client = KLinkClient()
    fake = FakeTransport()
    client._transport = fake
    return client, fake


def test_with_connect_connects_once_and_reconnects_after_close():
    before = len(_reader_threads())
    client, fake = _client_with_fake()

    with client.connect() as c:
        assert c is client
        assert fake.connect_calls == 1
        assert len(_reader_threads()) == before + 1

    assert fake.close_calls == 1
    assert client._reader_thread is None
    assert not client._running
    assert len(_reader_threads()) == before

    client.connect()
    try:
        assert fake.connect_calls == 2
        assert len(_reader_threads()) == before + 1
    finally:
        client.close()
    assert len(_reader_threads()) == before


def test_repeated_connect_is_noop_while_live():
    client, fake = _client_with_fake()
    try:
        first = client.connect()._reader_thread
        client.connect()
        client.connect()
        assert fake.connect_calls == 1
        assert client._reader_thread is first
    finally:
        client.close()

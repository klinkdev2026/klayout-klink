"""The client must fail in-flight calls FAST when the server goes away.

Regression guard: _reader_loop used to exit without draining _pending, so a
caller blocked in call() sat out its full timeout (default 30s) after KLayout
died. Now the reader's exit path delivers ERR_CONN_CLOSED immediately.
"""
from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from klink.client import KLinkClient
from klink.errors import KLinkServerError


def _one_shot_server(behaviour):
    """Tiny localhost TCP server: accepts one client, runs `behaviour(conn)`,
    returns (host, port, thread)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    host, port = srv.getsockname()

    def run():
        conn, _ = srv.accept()
        try:
            behaviour(conn)
        finally:
            try:
                conn.close()
            except OSError:
                pass
            srv.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return host, port, t


def test_inflight_call_fails_fast_when_server_disconnects():
    def behaviour(conn):
        conn.recv(65536)          # consume the request...
        # ...and hang up without answering.

    host, port, t = _one_shot_server(behaviour)
    client = KLinkClient(host=host, port=port, default_call_timeout=30.0)
    client.connect()
    try:
        start = time.monotonic()
        with pytest.raises(KLinkServerError) as excinfo:
            client.call("meta.ping", {})
        elapsed = time.monotonic() - start
        assert excinfo.value.code == "ERR_CONN_CLOSED"
        # The whole point: nowhere near the 30s call timeout.
        assert elapsed < 5.0, f"took {elapsed:.1f}s; pending was not drained"
    finally:
        client.close()
        t.join(timeout=2.0)


def test_call_still_works_then_close_is_clean():
    def behaviour(conn):
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                return
            buf += chunk
        req = json.loads(buf.decode("utf-8"))
        resp = {"id": req["id"], "ok": True, "result": {"pong": True}}
        conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))

    host, port, t = _one_shot_server(behaviour)
    client = KLinkClient(host=host, port=port)
    client.connect()
    try:
        assert client.call("meta.ping", {}) == {"pong": True}
    finally:
        client.close()
        t.join(timeout=2.0)

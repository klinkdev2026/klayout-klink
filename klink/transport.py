"""
NDJSON-over-TCP transport for the klink client.

Client side is free to use Python threads (no pya interaction), so the
transport is a thin wrapper around `socket` with a lock-guarded writer
and a blocking line reader that the client's reader thread drives.
"""

from __future__ import annotations

import json
import socket
import threading
from typing import Optional

from .errors import KLinkTransportError


class NDJSONTransport:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765, connect_timeout: float = 5.0):
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self._sock: Optional[socket.socket] = None
        self._rfile = None
        self._wfile = None
        self._write_lock = threading.Lock()

    def connect(self) -> None:
        try:
            s = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        except OSError as e:
            raise KLinkTransportError(
                f"cannot connect to klink at {self.host}:{self.port}: {e}. "
                f"Is KLayout running with the klink plugin loaded?"
            ) from e
        # After connect, switch to blocking mode (no timeout) so the
        # reader thread can block on readline() indefinitely.
        s.settimeout(None)
        self._sock = s
        self._rfile = s.makefile("rb")
        self._wfile = s.makefile("wb")

    def send(self, obj: dict) -> None:
        if self._wfile is None:
            raise KLinkTransportError("transport not connected")
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self._write_lock:
            try:
                self._wfile.write(data)
                self._wfile.flush()
            except OSError as e:
                raise KLinkTransportError(f"send failed: {e}") from e

    def recv_line(self) -> Optional[dict]:
        if self._rfile is None:
            return None
        try:
            raw = self._rfile.readline()
        except OSError:
            return None
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return None

    def close(self) -> None:
        # 1) Shutdown first so that a blocked readline() in another
        #    thread returns immediately with EOF. On Windows, closing
        #    the file object alone is NOT enough to wake up a blocking
        #    recv in another thread.
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        # 2) Now close the file wrappers + the socket.
        for f in (self._wfile, self._rfile):
            if f is not None:
                try:
                    f.close()
                except Exception:
                    pass
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None
        self._rfile = None
        self._wfile = None

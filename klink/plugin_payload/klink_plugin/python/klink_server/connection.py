"""
Per-connection state.

Each accepted QTcpSocket gets one ConnState instance.

All methods run in the Qt main thread (there are no worker threads on
the server side). The design relies on Qt's event loop:

* `readyRead` signal       => drains all complete NDJSON lines currently
                              in the socket's buffer, dispatches each.
* `disconnected` signal    => cleanup + ref drop.
* outgoing events          => queued and drained by `QTimer.singleShot`
                              so a burst of pya signals never blocks the
                              main thread if one client is slow.

Per-connection event queue rationale
------------------------------------
Event fan-out is cooperative: one pya signal (e.g. selection_changed)
can have N subscribers. If we called sock.write() synchronously for all
of them, a slow client would stall the whole loop. Instead each conn
owns its own out-queue; a 0ms QTimer drains it on the next tick, so the
signal handler returns immediately.
"""

from __future__ import annotations

import json
from typing import Callable, Optional, Set

import pya

from .protocol import encode_frame, make_response_err, make_event
from .errors import ErrorCode
from .log import get_logger

_log = get_logger("conn")


class ConnState:
    def __init__(self, conn_id: int, sock, dispatcher, events):
        self.conn_id = conn_id
        self.sock = sock
        self.dispatcher = dispatcher
        self.events = events

        # Event subscription channels this connection listens on
        self.subscriptions: Set[str] = set()

        # Lazy per-connection namespace used by `exec.python` (M4).
        # Created on first use so connections that never touch exec
        # pay nothing. Scope = this connection; discarded with the
        # conn on disconnect, so one client cannot trample another.
        self.exec_namespace: Optional[dict] = None

        # Outgoing event queue + drain scheduler flag
        self._out_queue = []
        self._drain_scheduled = False

        # Parent server cleanup callback (set externally)
        self.on_closed: Optional[Callable] = None

        self._closed = False

        # Hook Qt signals. Using method-call style (signal(handler))
        # because it is the form that has been battle-tested in
        # klive_server.py and avoids the Qt5 attribute-binding edge
        # cases reported in KLayout issue #629.
        self.sock.readyRead(self._on_ready_read)
        self.sock.disconnected(self._on_disconnected)

    # -----------------------------------------------------------------
    # Inbound
    # -----------------------------------------------------------------
    def _on_ready_read(self) -> None:
        try:
            while self.sock.canReadLine():
                raw = bytes(self.sock.readLine())
                try:
                    line = raw.decode("utf-8")
                except UnicodeDecodeError:
                    line = raw.decode("utf-8", errors="replace")
                line = line.strip()
                if not line:
                    continue
                self._handle_line(line)
        except Exception as ex:
            _log.error("conn#%d read loop error: %s", self.conn_id, ex, exc_info=True)

    def _handle_line(self, line: str) -> None:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            self._send(make_response_err(
                None, ErrorCode.BAD_REQUEST,
                f"invalid JSON: {e}",
                hint="each line must be a single valid JSON object",
            ))
            return

        if not isinstance(msg, dict):
            self._send(make_response_err(
                None, ErrorCode.BAD_REQUEST,
                "frame must be a JSON object",
                hint="expected {\"id\":..., \"method\":\"...\", \"params\":{...}}",
            ))
            return

        req_id = msg.get("id")
        method = msg.get("method")
        if not isinstance(method, str) or not method:
            self._send(make_response_err(
                req_id, ErrorCode.BAD_REQUEST,
                "missing 'method' field",
                hint="requests need {'method': '<namespace.verb>', 'params': {...}}",
            ))
            return

        params = msg.get("params")
        if params is None:
            params = {}

        self.dispatcher.dispatch(req_id, method, params, conn=self)

    # -----------------------------------------------------------------
    # Outbound -- responses (immediate)
    # -----------------------------------------------------------------
    def send(self, obj: dict) -> None:
        self._send(obj)

    def _send(self, obj: dict) -> None:
        if self._closed:
            return
        try:
            data = encode_frame(obj)
            self.sock.write(data)
            self.sock.flush()
        except Exception as ex:
            _log.warning("conn#%d write failed: %s", self.conn_id, ex)

    # -----------------------------------------------------------------
    # Outbound -- events (queued, drained by QTimer.singleShot)
    # -----------------------------------------------------------------
    def send_event(self, event: str, data: dict) -> None:
        if self._closed:
            return
        # We used to batch events and drain on the next event-loop tick
        # via pya.QTimer.singleShot, but that static method is exposed
        # as a `getset_descriptor` in pya 0.30.x and cannot be called
        # this way. Synchronous send is fine for now: writes to a local
        # TCP socket are non-blocking in practice. If slow clients
        # become a real problem we'll add back batching with a pya.QTimer
        # INSTANCE (timer.setSingleShot(True); timer.timeout += h;
        # timer.start(0)).
        self._send(make_event(event, data))

    # Kept for symmetry with older code; unused in current design.
    def _drain_events(self) -> None:
        self._drain_scheduled = False
        pending, self._out_queue = self._out_queue, []
        for obj in pending:
            self._send(obj)

    # -----------------------------------------------------------------
    # Teardown
    # -----------------------------------------------------------------
    def _on_disconnected(self) -> None:
        if self._closed:
            return
        self._closed = True
        _log.info("conn#%d closed", self.conn_id)
        try:
            self.events.unsubscribe_all(self)
        except Exception as exc:
            _log.debug("conn#%d unsubscribe_all failed: %s", self.conn_id, exc)
        try:
            if self.on_closed is not None:
                self.on_closed()
        except Exception as exc:
            _log.debug("conn#%d on_closed callback failed: %s", self.conn_id, exc, exc_info=True)
        try:
            # Let Qt schedule the socket object for deletion once control
            # returns to the event loop.
            self.sock.deleteLater()
        except Exception as exc:
            _log.debug("conn#%d deleteLater failed: %s", self.conn_id, exc)

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.sock.disconnectFromHost()
        except Exception as exc:
            _log.debug("conn#%d disconnectFromHost failed: %s", self.conn_id, exc)

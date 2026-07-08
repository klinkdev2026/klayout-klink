"""
klink server entry point.

Responsibilities
----------------
* Own the `pya.QTcpServer` listening on 127.0.0.1:8765
* Accept new connections, wrap them in `ConnState`
* Hold module-level state so the server survives macro re-runs in the
  KLayout Macro IDE (start() can be called many times; the old
  instance is cleanly stopped before a new one is created)

All socket I/O and method handlers run in the Qt main thread. See the
module docstrings in connection.py / dispatcher.py for details.
"""

from __future__ import annotations

import os

import pya

from .connection import ConnState
from .dispatcher import Dispatcher
from .events import EventBroadcaster
from .log import get_logger
from .signals import SignalHub
from .klive_compat import KliveCompatServer
from .anchor_pcell import register_anchor_library
from .port_pcell import register_port_library
from .structdevice_pcell import register_structdevice_library
from .session_registry import KLayoutSessionRegistry

_log = get_logger("server")

# Import the methods package so @method decorators register everything.
# Must happen after ./registry.py is importable; side-effect only.
from . import methods  # noqa: F401

KLINK_HOST = "127.0.0.1"
KLINK_PORT = 8765
KLINK_PORT_MAX = 8799

# Module-level holder so re-import in the Macro IDE finds the previous
# server and can shut it down before starting a new one.
_INSTANCE = {"server": None, "klive_compat": None}


class KlinkServer(pya.QTcpServer):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.conns = {}
        self._next_conn_id = 1
        self.host = KLINK_HOST
        self.port = None
        self.session_id = None
        self.registry = KLayoutSessionRegistry()
        self._heartbeat_timer = None

        self.dispatcher = Dispatcher()
        self.events = EventBroadcaster(self)
        self.signals = SignalHub(self)

        self.newConnection(self._on_new_connection)

        ha = pya.QHostAddress(KLINK_HOST)
        ok = False
        first_error = ""
        for port in range(KLINK_PORT, KLINK_PORT_MAX + 1):
            ok = self.listen(ha, port)
            if ok:
                self.port = port
                self.session_id = f"klayout-{port}"
                break
            if not first_error:
                first_error = self.errorString()
        if not ok or self.port is None:
            _log.error(
                "FAILED to listen on %s:%s: %s",
                KLINK_HOST, KLINK_PORT, first_error or self.errorString(),
            )
        else:
            _log.info("listening on %s:%s", KLINK_HOST, self.port)
            self._start_session_heartbeat()

        # Start the klive-compat server on port 8082 so gdsfactory
        # users can use klink as a drop-in replacement for klive.
        # Port conflict is handled inside KliveCompatServer (it warns
        # but does not prevent klink itself from running).
        try:
            _INSTANCE["klive_compat"] = KliveCompatServer(parent=parent)
        except Exception as e:
            _log.error("klive-compat server failed to start: %s", e, exc_info=True)

    def _on_new_connection(self) -> None:
        sock = self.nextPendingConnection()
        if sock is None:
            return
        conn_id = self._next_conn_id
        self._next_conn_id += 1

        conn = ConnState(conn_id, sock, self.dispatcher, self.events)
        self.conns[conn_id] = conn
        conn.on_closed = lambda cid=conn_id: self.conns.pop(cid, None)
        _log.info("conn#%d opened", conn_id)

    def session_record(self) -> dict:
        info = _active_layout_summary()
        return {
            "session_id": self.session_id,
            "host": self.host,
            "rpc_port": self.port,
            "pid": os.getpid(),
            **info,
        }

    def mark_klive_target(self) -> dict:
        if not self.session_id:
            return {"ok": False, "error": "klink session is not listening"}
        state = self.registry.write_state({"klive_target_session": self.session_id})
        self._write_session_record()
        return {
            "ok": True,
            "klive_target_session": self.session_id,
            "registry_root": str(self.registry.root),
            "state": state,
        }

    def _start_session_heartbeat(self) -> None:
        self._write_session_record()
        try:
            timer = pya.QTimer(self)
            timer.setSingleShot(False)
            timer.timeout += self._write_session_record
            timer.start(3000)
            self._heartbeat_timer = timer
        except Exception as e:
            _log.error("session heartbeat timer failed: %s", e, exc_info=True)

    def _write_session_record(self, *args) -> None:
        if not self.session_id:
            return
        try:
            self.registry.write_session(self.session_record())
        except Exception as e:
            _log.warning("session registry write failed: %s", e)

    def shutdown(self) -> None:
        try:
            if self._heartbeat_timer is not None:
                self._heartbeat_timer.stop()
        except Exception as exc:
            _log.debug("heartbeat timer stop failed: %s", exc)
        try:
            if self.session_id:
                self.registry.remove_session(self.session_id)
        except Exception as exc:
            _log.debug("session registry remove failed: %s", exc)
        try:
            self.signals.shutdown()
        except Exception as exc:
            _log.debug("signal hub shutdown failed: %s", exc, exc_info=True)
        for conn in list(self.conns.values()):
            try:
                conn.close()
            except Exception as exc:
                _log.debug("conn close failed: %s", exc)
        self.conns.clear()
        try:
            self.close()
        except Exception as exc:
            _log.debug("server close failed: %s", exc)
        # Shut down the klive-compat server too.
        try:
            klive = _INSTANCE.get("klive_compat")
            if klive is not None:
                klive.close()
                _INSTANCE["klive_compat"] = None
        except Exception as exc:
            _log.debug("klive-compat close failed: %s", exc)


def start() -> None:
    """Start (or restart) the klink server. Safe to call repeatedly."""
    old = _INSTANCE["server"]
    if old is not None:
        try:
            if old.isListening():
                _log.info("stopping previous server instance")
                old.shutdown()
                pya.Application.instance().process_events()
        except Exception as e:
            _log.error("error stopping old server: %s", e, exc_info=True)
        _INSTANCE["server"] = None

    # Also clean up any previous klive-compat server.
    old_klive = _INSTANCE.get("klive_compat")
    if old_klive is not None:
        try:
            old_klive.close()
        except Exception as exc:
            _log.debug("old klive-compat close failed: %s", exc)
        _INSTANCE["klive_compat"] = None

    try:
        mw = pya.Application.instance().main_window()
    except Exception as e:
        _log.error("cannot start: no main window (%s)", e)
        return

    # Register the klink_Port PCell library so port.* RPCs can create
    # PCell variants via ly.create_cell("Port", "klink_port", ...).
    try:
        register_port_library()
        _log.info("klink_port PCell library registered")
    except Exception as e:
        _log.error("klink_port library registration failed: %s", e, exc_info=True)

    try:
        register_anchor_library()
        _log.info("klink_anchor PCell library registered")
    except Exception as e:
        _log.error("klink_anchor library registration failed: %s", e, exc_info=True)

    try:
        register_structdevice_library()
        _log.info("klink_structdevice PCell library registered")
    except Exception as e:
        _log.error("klink_structdevice library registration failed: %s", e,
                   exc_info=True)

    try:
        _INSTANCE["server"] = KlinkServer(parent=mw)
    except Exception as e:
        _log.error("server construction failed: %s", e, exc_info=True)


def stop() -> None:
    old = _INSTANCE["server"]
    if old is not None:
        old.shutdown()
        _INSTANCE["server"] = None


def instance():
    return _INSTANCE["server"]


def mark_klive_target() -> dict:
    srv = instance()
    if srv is None:
        return {"ok": False, "error": "no klink server instance"}
    return srv.mark_klive_target()


def _active_layout_summary() -> dict:
    out = {
        "layout_path": None,
        "active_cell": None,
        "window_title": None,
        "dbu": None,
        "views": None,
    }
    try:
        app = pya.Application.instance()
        mw = app.main_window() if app is not None else None
        if mw is None:
            return out
        out["views"] = mw.views()
        try:
            out["window_title"] = str(mw.title)
        except Exception:
            pass
        view = mw.current_view()
        if view is None:
            return out
        cv = view.active_cellview()
        if cv is None or not cv.is_valid():
            return out
        try:
            out["layout_path"] = cv.filename() or None
        except Exception:
            pass
        try:
            out["active_cell"] = cv.cell.name if cv.cell is not None else None
        except Exception:
            pass
        try:
            out["dbu"] = cv.layout().dbu
        except Exception:
            pass
    except Exception:
        pass
    return out

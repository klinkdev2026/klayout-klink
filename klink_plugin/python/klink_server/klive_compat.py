"""
klive-compatible server on port 8082.

Drop-in replacement for the original klive server so that gdsfactory /
external scripts that hard-code `localhost:8082` work without changes.

Protocol (reverse-engineered from klive 0.4.1)
----------------------------------------------
Request (one JSON line):
    {"gds": "/abs/path/to/file.gds", "keep_position": true,
     "libraries": [{"name": "...", "file": "..."}],
     "technology": "tech_name",
     "lyrdb": "/path/to/lyrdb", "l2n": "/path/to/l2n"}

Response (one JSON line):
    {"version": "0.4.1", "klayout_version": "...",
     "type": "open"|"reload", "file": "..."}

Design
------
In single-session fallback mode, file loading goes through pya directly
(same calls as original klive). In multi-session mode, this server keeps
the fixed 8082 klive-compatible entrypoint and forwards the request to the
registered target session through klink RPC.
"""

from __future__ import annotations

import json

import pya

KLIVE_VERSION = "0.4.1"
KLIVE_HOST = "127.0.0.1"
KLIVE_PORT = 8082


class KliveCompatServer(pya.QTcpServer):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._app = pya.Application.instance()

        self.newConnection(self._on_new_connection)

        ha = pya.QHostAddress(KLIVE_HOST)
        ok = self.listen(ha, KLIVE_PORT)
        if not ok:
            print(
                f"[klink] klive-compat FAILED to listen on "
                f"{KLIVE_HOST}:{KLIVE_PORT}: {self.errorString()}"
            )
            print("[klink] (klive-compat unavailable; klink port 8765 is unaffected)")
        else:
            print(
                f"[klink] klive-compat listening on {KLIVE_HOST}:{KLIVE_PORT} "
                f"(drop-in replacement for klive)"
            )
        # Also try IPv6 loopback; not critical if it fails.
        try:
            ha6 = pya.QHostAddress("::1")
            self.listen(ha6, KLIVE_PORT)
        except Exception:
            pass

    def _on_new_connection(self) -> None:
        connection = self.nextPendingConnection()
        if connection is None:
            return

        try:
            self._handle_connection(connection)
        except Exception as ex:
            print(f"[klink] klive-compat error: {ex}")
        finally:
            try:
                connection.disconnectFromHost()
            except Exception:
                pass
            try:
                signal = pya.qt_signal("disconnected()")
                slot = pya.qt_slot("deleteLater()")
                pya.QObject.connect(connection, signal, connection, slot)
            except Exception:
                pass

    def _handle_connection(self, connection) -> None:
        # Read one JSON line, same pattern as klink connection.py
        data = None
        while (
            connection.isOpen()
            and connection.state() == pya.QTcpSocket.ConnectedState
        ):
            if connection.canReadLine():
                line = connection.readLine()
                data = json.loads(line)
                break
            else:
                connection.waitForReadyRead(100)

        if data is None:
            return

        gds_path = data.get("gds")
        if not gds_path:
            _send_json(connection, {
                "ok": False,
                "error": "missing required field: gds",
            })
            return

        # Push a caused_by entry so the recorder knows this is a file load
        self._push_caused_by(gds_path)
        try:
            forwarded = self._forward_layout(connection, data)
            if not forwarded:
                self._load_layout(connection, data)
        finally:
            self._pop_caused_by()

    def _forward_layout(self, connection, data: dict) -> bool:
        try:
            from .klive_forward import KliveCompatError, forward_klive_request
        except Exception:
            return False
        try:
            from .server import instance as _server_instance

            srv = _server_instance()
            local_session_id = getattr(srv, "session_id", None) if srv is not None else None
            response = forward_klive_request(data, avoid_session_id=local_session_id)
            try:
                response["klayout_version"] = str(pya.__version__)
            except Exception:
                pass
            _send_json(connection, response)
            print(
                "[klink] klive-compat forwarded %s to %s"
                % (data.get("gds"), response.get("target_session"))
            )
            return True
        except KliveCompatError as exc:
            text = str(exc)
            if "target session is the local klive-compatible server" in text:
                return False
            _send_json(connection, {
                "ok": False,
                "error": text,
                "version": KLIVE_VERSION,
            })
            print(f"[klink] klive-compat target error: {text}")
            return True
        except Exception as exc:
            _send_json(connection, {
                "ok": False,
                "error": str(exc),
                "version": KLIVE_VERSION,
            })
            print(f"[klink] klive-compat forward failed: {exc}")
            return True

    def _push_caused_by(self, path: str) -> None:
        try:
            from .dispatcher import _REQUEST_STACK
            _REQUEST_STACK.append({
                "request_id": "klive_compat",
                "method": "layout.show_file",
                "trace_id": "klive_compat",
                "conn_id": 0,
            })
        except Exception:
            pass

        # Also tell the recorder the file path directly so it can emit
        # `c.layout_show_file(path)` in the recorded script.
        try:
            from .recorder import instance as _rec
            rec = _rec()
            rec._show_file_path = path
        except Exception:
            pass

    def _pop_caused_by(self) -> None:
        try:
            from .dispatcher import _REQUEST_STACK
            _REQUEST_STACK.pop()
        except (IndexError, Exception):
            pass

    def _load_layout(self, connection, data: dict) -> None:
        """Load a GDS/OAS file into KLayout. Faithful to original klive
        logic so existing gdsfactory users have the same behaviour."""
        gds_path = data["gds"]
        keep_position = data.get("keep_position", True)
        libraries = data.get("libraries", [])
        technology = data.get("technology", None)

        window = self._app.main_window()
        current_view = window.current_view()
        previous_view = current_view.box() if current_view else None

        send_data = {
            "version": KLIVE_VERSION,
            "klayout_version": str(pya.__version__),
        }

        # Register libraries before loading the main file
        for lib_dict in libraries:
            try:
                lib = pya.Library()
                lib.register(lib_dict["name"])
                lib.layout().read(lib_dict["file"])
            except Exception as e:
                print(f"[klink] klive-compat library register error: {e}")

        def load_existing_layout(view):
            for i in range(window.views()):
                v = window.view(i)
                for j in range(v.cellviews()):
                    try:
                        if v.active_cellview().filename() == gds_path:
                            print(
                                f"File {v.active_cellview().filename()} "
                                "already openend"
                            )
                            window.current_view_index = i
                            v.active_setview_index = j
                            v.reload_layout(j)
                            if technology is not None:
                                _apply_technology(v, technology, send_data)
                            if v.active_cellview().cell is None:
                                try:
                                    v.active_cellview().cell = (
                                        v.active_cellview()
                                        .layout()
                                        .top_cells()[0]
                                    )
                                except Exception:
                                    pass
                            send_data["type"] = "reload"
                            send_data["file"] = gds_path
                            _send_json(connection, send_data)
                            return v
                    except Exception:
                        continue
            return None

        if window.views() > 0:
            view = load_existing_layout(window.current_view())
            if view is not None:
                # File was already open and was reloaded
                pass
            else:
                # Load into a new tab (editable mode = 1)
                new_cview = window.load_layout(gds_path, 1)
                new_view = new_cview.view()
                new_view.max_hier()
                window.current_view_index = window.index_of(new_view)
                if technology is not None:
                    _apply_technology(new_view, technology, send_data)
                send_data["type"] = "open"
                send_data["file"] = gds_path
                _send_json(connection, send_data)
        else:
            # No views yet; load the first one
            window.load_layout(gds_path, 1)
            view = window.current_view()
            view.max_hier()
            if previous_view and keep_position:
                try:
                    view.zoom_box(previous_view)
                except Exception:
                    pass
            if technology is not None:
                _apply_technology(view, technology, send_data)
            print(f"Loaded {gds_path}")
            send_data["type"] = "open"
            send_data["file"] = gds_path
            _send_json(connection, send_data)

        # Load optional RDB (Report Database)
        if "lyrdb" in data:
            try:
                lyrdb_path = data["lyrdb"]
                rdb = pya.ReportDatabase().load(lyrdb_path)
                cv = window.current_view().active_cellview()
                rdb_i = window.current_view().add_rdb(rdb)
                window.current_view().show_rdb(
                    rdb_i, cv.cell_index if cv.cell is not None else 0
                )
            except Exception as e:
                print(f"[klink] klive-compat lyrdb error: {e}")

        # Load optional L2N (Layout-to-Netlist)
        if "l2n" in data:
            try:
                l2n_path = data["l2n"]
                l2n = pya.LayoutToNetlist()
                l2n.read(l2n_path)
                cv = window.current_view().active_cellview()
                l2n_i = window.current_view().add_l2ndb(l2n)
                window.current_view().show_l2ndb(
                    l2n_i, cv.cell_index if cv.cell is not None else 0
                )
            except Exception as e:
                print(f"[klink] klive-compat l2n error: {e}")


def _apply_technology(view, technology: str, send_data: dict) -> None:
    """Apply a KLayout technology to the active cellview of `view`."""
    try:
        available = pya.Technology.technology_names()
        if technology in available:
            if view.active_cellview().technology != technology:
                view.active_cellview().technology = technology
        else:
            send_data["info"] = (
                f"Technology {technology!r} is not available. "
                f"Available technologies are {available}. "
                "Are you sure you have installed the technology in klayout?"
            )
    except Exception as e:
        print(f"[klink] klive-compat technology error: {e}")


def _send_json(connection, obj: dict) -> None:
    try:
        connection.write(json.dumps(obj).encode("utf-8"))
        connection.flush()
    except Exception as ex:
        print(f"[klink] klive-compat write failed: {ex}")

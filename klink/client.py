"""
Synchronous klink RPC client.

Model
-----
The client owns a background reader thread that drains responses and
events from the server. Callers that want RPC semantics get a blocking
`call(...)` that waits for the matching response. Events are delivered
to user-registered handlers (`client.on(name, handler)`) from the
reader thread - handlers should not do heavy work; marshal to a queue
or other thread if needed.

LLM integration notes
---------------------
`client.methods()` returns the server's full method catalogue with JSON
schemas; `generate_tool_schemas()` converts it into the OpenAI
function-calling / Anthropic tool format. Higher-level wrappers
(e.g. MCP adapter) can sit on top of these.
"""

from __future__ import annotations

import itertools
import queue
import threading
from typing import Any, Callable, Dict, List, Optional

from ._meta import PROTOCOL_VERSION, __version__
from .errors import KLinkServerError, KLinkTransportError
from .handshake import evaluate_handshake
from .transport import NDJSONTransport


class KLinkClient:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        connect_timeout: float = 5.0,
        default_call_timeout: float = 30.0,
    ):
        self._transport = NDJSONTransport(host, port, connect_timeout)
        self._id_counter = itertools.count(1)
        self._pending: Dict[int, queue.Queue] = {}
        self._pending_lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False
        self._event_handlers: Dict[str, List[Callable]] = {}
        self._event_handlers_lock = threading.Lock()
        self.default_call_timeout = default_call_timeout

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def connect(self) -> "KLinkClient":
        self._transport.connect()
        self._running = True
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            daemon=True,
            name="klink-reader",
        )
        self._reader_thread.start()
        return self

    def close(self) -> None:
        # Flip the flag first so the reader loop exits as soon as its
        # blocked recv unblocks.
        self._running = False
        # Closing the transport also shutdowns the socket, which causes
        # the reader thread's blocking readline() to return immediately.
        try:
            self._transport.close()
        except Exception:
            pass
        # Drain any still-pending callers with a transport error so they
        # don't hang forever.
        self._drain_pending("connection was closed before the response arrived")
        # Explicitly join the reader thread so the interpreter can exit
        # cleanly. Even though the thread is a daemon, on Windows a
        # still-running socket recv can delay interpreter shutdown and
        # make the terminal feel "frozen" after the script finishes.
        t = self._reader_thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=2.0)
        self._reader_thread = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ------------------------------------------------------------------
    # Core RPC
    # ------------------------------------------------------------------
    def call(
        self,
        method: str,
        params: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        if not self._running:
            raise KLinkTransportError("client not connected; call connect() first")

        req_id = next(self._id_counter)
        q: queue.Queue = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[req_id] = q

        try:
            self._transport.send({
                "id": req_id,
                "method": method,
                "params": params or {},
            })
        except Exception:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise

        t = timeout if timeout is not None else self.default_call_timeout
        try:
            response = q.get(timeout=t)
        except queue.Empty:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise KLinkTransportError(
                f"timeout after {t:.1f}s waiting for response to {method!r}"
            )
        finally:
            with self._pending_lock:
                self._pending.pop(req_id, None)

        if response.get("ok"):
            return response.get("result")

        err = response.get("error") or {}
        raise KLinkServerError(
            code=err.get("code", "ERR_UNKNOWN"),
            message=err.get("message", ""),
            hint=err.get("hint", ""),
            data=err.get("data"),
        )

    # ------------------------------------------------------------------
    # Convenience wrappers (mirror the server's method catalogue)
    # ------------------------------------------------------------------
    def hello(self, client_id: str = f"klayout-klink/{__version__}") -> dict:
        return self.call("hello", {"client": client_id, "protocol": PROTOCOL_VERSION})

    def handshake(self) -> dict:
        """Call ``hello`` and return a structured client/plugin compatibility
        report. On a protocol mismatch the report carries an instructive
        ``next_action`` (see :func:`klink.handshake.evaluate_handshake`)."""
        info = self.hello()
        return evaluate_handshake(__version__, PROTOCOL_VERSION, info)

    def ping(self, **kwargs) -> dict:
        return self.call("meta.ping", kwargs)

    def methods(self) -> dict:
        return self.call("meta.methods")

    def layout_info(self, verbosity: str = "normal") -> dict:
        return self.call("layout.info", {"verbosity": verbosity})

    def layout_show_file(self, path: str, *, mode: str = "replace",
                         keep_position: bool = True,
                         technology: Optional[str] = None) -> dict:
        """Load a GDS/OAS file into KLayout.

        Args:
            path: Absolute path to the GDS/OAS file.
            mode: 'replace' (current view) or 'new' (new tab).
            keep_position: Restore viewport after loading.
            technology: Optional KLayout technology name to apply.
        """
        p: Dict[str, Any] = {"path": path, "mode": mode,
                             "keep_position": bool(keep_position)}
        if technology is not None:
            p["technology"] = technology
        return self.call("layout.show_file", p)

    # ---- layer wrappers (M3 write) ----
    def layer_list(self) -> dict:
        return self.call("layer.list")

    def layer_ensure(self, layer: int, datatype: int = 0,
                     name: Optional[str] = None) -> dict:
        p = {"layer": int(layer), "datatype": int(datatype)}
        if name is not None:
            p["name"] = name
        return self.call("layer.ensure", p)

    # ---- cell / shape / selection / view wrappers ----
    def cell_list(self, **kwargs) -> dict:
        return self.call("cell.list", kwargs)

    def cell_create(self, name: Optional[str] = None) -> dict:
        p = {}
        if name is not None:
            p["name"] = name
        return self.call("cell.create", p)

    def cell_delete(self, cell, recursive: bool = False) -> dict:
        return self.call("cell.delete", {"cell": cell, "recursive": bool(recursive)})

    def cell_rename(self, cell, new_name: str, allow_suffix: bool = False) -> dict:
        return self.call("cell.rename", {
            "cell": cell, "new_name": new_name, "allow_suffix": bool(allow_suffix),
        })

    def cell_tree(self, **kwargs) -> dict:
        return self.call("cell.tree", kwargs)

    def shape_query(self, cell, **kwargs) -> dict:
        return self.call("shape.query", {"cell": cell, **kwargs})

    # ---- M3 shape writes ----
    def shape_insert_box(self, cell, *, layer_index=None, layer=None,
                         datatype=0, bbox_um=None, bbox_dbu=None) -> dict:
        p = {"cell": cell}
        if layer_index is not None:
            p["layer_index"] = int(layer_index)
        elif layer is not None:
            p["layer"] = int(layer); p["datatype"] = int(datatype)
        if bbox_um is not None:
            p["bbox_um"] = list(bbox_um)
        if bbox_dbu is not None:
            p["bbox_dbu"] = list(bbox_dbu)
        return self.call("shape.insert_box", p)

    def shape_insert_boxes(self, cell, *, layer_index=None, layer=None,
                           datatype=0, boxes_um=None, boxes_dbu=None,
                           dry_run: bool = False) -> dict:
        p = {"cell": cell, "dry_run": bool(dry_run)}
        if layer_index is not None:
            p["layer_index"] = int(layer_index)
        elif layer is not None:
            p["layer"] = int(layer); p["datatype"] = int(datatype)
        if boxes_um is not None:
            p["boxes_um"] = [list(box) for box in boxes_um]
        if boxes_dbu is not None:
            p["boxes_dbu"] = [list(box) for box in boxes_dbu]
        return self.call("shape.insert_boxes", p)

    def shape_insert_many(self, cell, items, *, dry_run: bool = False) -> dict:
        return self.call("shape.insert_many", {
            "cell": cell,
            "items": [dict(item) for item in items],
            "dry_run": bool(dry_run),
        })

    def shape_insert_polygon(self, cell, *, layer_index=None, layer=None,
                             datatype=0, points_um=None, points_dbu=None) -> dict:
        p = {"cell": cell}
        if layer_index is not None:
            p["layer_index"] = int(layer_index)
        elif layer is not None:
            p["layer"] = int(layer); p["datatype"] = int(datatype)
        if points_um is not None:
            p["points_um"] = [list(pt) for pt in points_um]
        if points_dbu is not None:
            p["points_dbu"] = [list(pt) for pt in points_dbu]
        return self.call("shape.insert_polygon", p)

    def shape_insert_path(self, cell, *, layer_index=None, layer=None,
                          datatype=0, points_um=None, points_dbu=None,
                          width_um=None, width_dbu=None,
                          begin_ext_um=None, begin_ext_dbu=None,
                          end_ext_um=None, end_ext_dbu=None,
                          round_ends=False) -> dict:
        p = {"cell": cell, "round_ends": bool(round_ends)}
        if layer_index is not None:
            p["layer_index"] = int(layer_index)
        elif layer is not None:
            p["layer"] = int(layer); p["datatype"] = int(datatype)
        if points_um is not None:
            p["points_um"] = [list(pt) for pt in points_um]
        if points_dbu is not None:
            p["points_dbu"] = [list(pt) for pt in points_dbu]
        if width_um is not None: p["width_um"] = float(width_um)
        if width_dbu is not None: p["width_dbu"] = int(width_dbu)
        if begin_ext_um is not None: p["begin_ext_um"] = float(begin_ext_um)
        if begin_ext_dbu is not None: p["begin_ext_dbu"] = int(begin_ext_dbu)
        if end_ext_um is not None: p["end_ext_um"] = float(end_ext_um)
        if end_ext_dbu is not None: p["end_ext_dbu"] = int(end_ext_dbu)
        return self.call("shape.insert_path", p)

    def shape_delete(self, cell, *, layer_index=None, layer=None, datatype=0,
                     layers=None, all_layers=False,
                     bbox_um=None, bbox_dbu=None,
                     kinds=None, limit=10000, dry_run=False) -> dict:
        p = {"cell": cell, "all_layers": bool(all_layers),
             "limit": int(limit), "dry_run": bool(dry_run)}
        if layer_index is not None:
            p["layer_index"] = int(layer_index)
        elif layer is not None:
            p["layer"] = int(layer); p["datatype"] = int(datatype)
        if layers is not None:
            p["layers"] = list(layers)
        if bbox_um is not None:  p["bbox_um"]  = list(bbox_um)
        if bbox_dbu is not None: p["bbox_dbu"] = list(bbox_dbu)
        if kinds is not None:    p["kinds"]    = list(kinds)
        return self.call("shape.delete", p)

    def shape_insert_text(self, cell, string, *, layer_index=None, layer=None,
                          datatype=0, position_um=None, position_dbu=None,
                          size_um=None, size_dbu=None) -> dict:
        p = {"cell": cell, "string": str(string)}
        if layer_index is not None:
            p["layer_index"] = int(layer_index)
        elif layer is not None:
            p["layer"] = int(layer); p["datatype"] = int(datatype)
        if position_um is not None: p["position_um"] = list(position_um)
        if position_dbu is not None: p["position_dbu"] = list(position_dbu)
        if size_um is not None: p["size_um"] = float(size_um)
        if size_dbu is not None: p["size_dbu"] = int(size_dbu)
        return self.call("shape.insert_text", p)

    # ---- M3 instance writes ----
    def instance_insert(self, parent, child, *,
                        position_um=None, position_dbu=None,
                        rotation: float = 0, mirror: bool = False,
                        magnification: float = 1.0,
                        array: Optional[dict] = None) -> dict:
        p = {
            "parent": parent, "child": child,
            "rotation": float(rotation),
            "mirror": bool(mirror),
            "magnification": float(magnification),
        }
        if position_um is not None: p["position_um"] = list(position_um)
        if position_dbu is not None: p["position_dbu"] = list(position_dbu)
        if array is not None: p["array"] = dict(array)
        return self.call("instance.insert", p)

    def instance_insert_many(self, parent, items, *, dry_run: bool = False) -> dict:
        return self.call("instance.insert_many", {
            "parent": parent,
            "items": [dict(item) for item in items],
            "dry_run": bool(dry_run),
        })

    def instance_delete(self, parent, *, child=None,
                        bbox_um=None, bbox_dbu=None,
                        all: bool = False, limit: int = 10000,
                        dry_run: bool = False) -> dict:
        p = {"parent": parent, "all": bool(all),
             "limit": int(limit), "dry_run": bool(dry_run)}
        if child is not None:    p["child"]    = child
        if bbox_um is not None:  p["bbox_um"]  = list(bbox_um)
        if bbox_dbu is not None: p["bbox_dbu"] = list(bbox_dbu)
        return self.call("instance.delete", p)

    def instance_insert_pcell(self, parent, pcell: str, *,
                              library: str = "Basic",
                              params: Optional[dict] = None,
                              position_um=None, position_dbu=None,
                              rotation: float = 0, mirror: bool = False,
                              magnification: float = 1.0,
                              array: Optional[dict] = None) -> dict:
        p = {
            "parent": parent, "pcell": pcell, "library": library,
            "rotation": float(rotation),
            "mirror": bool(mirror),
            "magnification": float(magnification),
            "params": dict(params or {}),
        }
        if position_um is not None: p["position_um"] = list(position_um)
        if position_dbu is not None: p["position_dbu"] = list(position_dbu)
        if array is not None: p["array"] = dict(array)
        return self.call("instance.insert_pcell", p)

    def instance_insert_pcell_many(self, parent, items, *,
                                   dry_run: bool = False) -> dict:
        return self.call("instance.insert_pcell_many", {
            "parent": parent,
            "items": [dict(item) for item in items],
            "dry_run": bool(dry_run),
        })

    def instance_query(self, parent, *, child=None,
                       bbox_um=None, bbox_dbu=None,
                       limit: int = 10000) -> dict:
        p = {"parent": parent, "limit": int(limit)}
        if child is not None:    p["child"]    = child
        if bbox_um is not None:  p["bbox_um"]  = list(bbox_um)
        if bbox_dbu is not None: p["bbox_dbu"] = list(bbox_dbu)
        return self.call("instance.query", p)

    # ---- port writes ----
    def port_mark_many(self, cell, items, **defaults) -> dict:
        """Batch port.mark_many wrapper. `items` is a list of per-port dicts
        (same fields as port.mark's params, each needing center_um or
        center_dbu); `**defaults` are top-level fields (layer, label,
        orientation, width_um, port_type, net, target_layer, show_label,
        access_mode, slide_allowed, slide_edge) inherited by items that
        omit them."""
        return self.call("port.mark_many", {
            "cell": cell,
            "items": [dict(item) for item in items],
            **defaults,
        })

    # ---- M3 Basic-library PCell shortcuts ----------------------------
    # Thin wrappers around instance.insert_pcell that hard-code the
    # parameter names for each standard KLayout Basic PCell. Extra
    # params can be passed as **extra and will be forwarded as-is (useful
    # for per-PDK or version-specific extra options like `inverse`).
    #
    # NOTE: if a parameter name is wrong for your KLayout build, call
    # client.pcell_info(name, library="Basic") to discover the actual
    # names and just use instance_insert_pcell(..., params=...) directly.

    @staticmethod
    def _as_layer_spec(layer):
        """Accept (L, D) tuple, int (datatype=0), 'L/D' str, or dict."""
        if isinstance(layer, (tuple, list)) and len(layer) == 2:
            return {"layer": int(layer[0]), "datatype": int(layer[1])}
        if isinstance(layer, int):
            return {"layer": int(layer), "datatype": 0}
        if isinstance(layer, str):
            return layer
        if isinstance(layer, dict):
            return layer
        raise TypeError(f"cannot interpret layer spec: {layer!r}")

    def _basic_pcell(self, parent, name: str, params: dict, *,
                     position_um=None, rotation: float = 0,
                     mirror: bool = False,
                     magnification: float = 1.0) -> dict:
        return self.instance_insert_pcell(
            parent, name, library="Basic", params=params,
            position_um=position_um, rotation=rotation, mirror=mirror,
            magnification=magnification,
        )

    def basic_circle(self, parent, *, layer, radius: float,
                     npoints: int = 64, position_um=None,
                     rotation: float = 0, mirror: bool = False,
                     magnification: float = 1.0, **extra) -> dict:
        # Three things are needed for CIRCLE to honour our radius when
        # built programmatically:
        #   1. radius          (the human-visible drawing param)
        #   2. actual_radius   (the shadow param produce_impl reads)
        #   3. handle          (a DPoint at (-radius, 0); CIRCLE's
        #                       handle default is (-1,0) and is NOT
        #                       None, so produce derives radius from
        #                       it and overrides actual_radius).
        # DONUT/ARC don't need step 3 because their handles default
        # to None.
        p = {"layer": self._as_layer_spec(layer),
             "radius": float(radius),
             "actual_radius": float(radius),
             "handle": {"point_um": [-float(radius), 0.0]},
             "npoints": int(npoints), **extra}
        return self._basic_pcell(parent, "CIRCLE", p,
                                 position_um=position_um, rotation=rotation,
                                 mirror=mirror, magnification=magnification)

    def basic_arc(self, parent, *, layer,
                  radius1: float, radius2: float,
                  start_angle: float, end_angle: float,
                  npoints: int = 64, position_um=None,
                  rotation: float = 0, mirror: bool = False,
                  magnification: float = 1.0, **extra) -> dict:
        # ARC has a dual-parameter design: drawing params (radius1,
        # radius2, ...) are tied to interactive handles and get
        # coerced back to defaults in scripted placement; the script-
        # friendly ones are the `actual_*` shadow params. Per KLayout
        # author Matthias Koefferlein (forum discussion 551), we send
        # only the actual_* names. NOTE: radius1 != radius2 is
        # required, otherwise ARC degenerates to zero-thickness.
        p = {"layer": self._as_layer_spec(layer),
             "actual_radius1": float(radius1),
             "actual_radius2": float(radius2),
             "actual_start_angle": float(start_angle),
             "actual_end_angle": float(end_angle),
             "npoints": int(npoints), **extra}
        return self._basic_pcell(parent, "ARC", p,
                                 position_um=position_um, rotation=rotation,
                                 mirror=mirror, magnification=magnification)

    def basic_donut(self, parent, *, layer,
                    radius1: float, radius2: float,
                    npoints: int = 64, position_um=None,
                    rotation: float = 0, mirror: bool = False,
                    magnification: float = 1.0, **extra) -> dict:
        # Same drawing-vs-actual split as CIRCLE: programmatic creation
        # doesn't trigger coerce, so we also set the actual_* shadows.
        p = {"layer": self._as_layer_spec(layer),
             "radius1": float(radius1), "radius2": float(radius2),
             "actual_radius1": float(radius1),
             "actual_radius2": float(radius2),
             "npoints": int(npoints), **extra}
        return self._basic_pcell(parent, "DONUT", p,
                                 position_um=position_um, rotation=rotation,
                                 mirror=mirror, magnification=magnification)

    def basic_ellipse(self, parent, *, layer,
                      radius_x: float, radius_y: float,
                      npoints: int = 64, position_um=None,
                      rotation: float = 0, mirror: bool = False,
                      magnification: float = 1.0, **extra) -> dict:
        # Same triad as basic_circle: drawing + actual + handle, once
        # per axis. ELLIPSE's handle_x / handle_y defaults are (-1,0)
        # and (0,0.5) respectively, both non-None, so without our
        # override produce_impl would fall back to the default 2x1 um
        # ellipse.
        p = {"layer": self._as_layer_spec(layer),
             "radius_x": float(radius_x), "radius_y": float(radius_y),
             "actual_radius_x": float(radius_x),
             "actual_radius_y": float(radius_y),
             "handle_x": {"point_um": [-float(radius_x), 0.0]},
             "handle_y": {"point_um": [0.0, float(radius_y)]},
             "npoints": int(npoints), **extra}
        return self._basic_pcell(parent, "ELLIPSE", p,
                                 position_um=position_um, rotation=rotation,
                                 mirror=mirror, magnification=magnification)

    def basic_text(self, parent, *, layer, text: str,
                   mag: float = 1.0, position_um=None,
                   rotation: float = 0, mirror: bool = False,
                   magnification: float = 1.0, **extra) -> dict:
        p = {"layer": self._as_layer_spec(layer),
             "text": str(text), "mag": float(mag), **extra}
        return self._basic_pcell(parent, "TEXT", p,
                                 position_um=position_um, rotation=rotation,
                                 mirror=mirror, magnification=magnification)

    def basic_round_path(self, parent, *, layer, points_um, width_um: float,
                         radius: float = 0.5, npoints: int = 16,
                         position_um=None, rotation: float = 0,
                         mirror: bool = False, magnification: float = 1.0,
                         **extra) -> dict:
        p = {"layer": self._as_layer_spec(layer),
             "path": {"points_um": [list(pt) for pt in points_um],
                      "width_um": float(width_um)},
             "radius": float(radius), "npoints": int(npoints), **extra}
        return self._basic_pcell(parent, "ROUND_PATH", p,
                                 position_um=position_um, rotation=rotation,
                                 mirror=mirror, magnification=magnification)

    def basic_round_polygon(self, parent, *, layer, points_um,
                            radius: float = 0.5, npoints: int = 16,
                            position_um=None, rotation: float = 0,
                            mirror: bool = False, magnification: float = 1.0,
                            **extra) -> dict:
        # The TypeShape parameter for ROUND_POLYGON is named `polygon`
        # (confirmed via pcell.info). Previously this helper used key
        # `corners`, which KLayout silently ignored -> fell back to the
        # default ~0.4um square.
        p = {"layer": self._as_layer_spec(layer),
             "polygon": {"points_um": [list(pt) for pt in points_um]},
             "radius": float(radius), "npoints": int(npoints), **extra}
        return self._basic_pcell(parent, "ROUND_POLYGON", p,
                                 position_um=position_um, rotation=rotation,
                                 mirror=mirror, magnification=magnification)

    def basic_stroked_box(self, parent, *, layer, bbox_um,
                          width_um: float, position_um=None,
                          rotation: float = 0, mirror: bool = False,
                          magnification: float = 1.0, **extra) -> dict:
        p = {"layer": self._as_layer_spec(layer),
             "shape": {"bbox_um": list(bbox_um)},
             "width": float(width_um), **extra}
        return self._basic_pcell(parent, "STROKED_BOX", p,
                                 position_um=position_um, rotation=rotation,
                                 mirror=mirror, magnification=magnification)

    def basic_stroked_polygon(self, parent, *, layer, points_um,
                              width_um: float, position_um=None,
                              rotation: float = 0, mirror: bool = False,
                              magnification: float = 1.0, **extra) -> dict:
        p = {"layer": self._as_layer_spec(layer),
             "shape": {"points_um": [list(pt) for pt in points_um]},
             "width": float(width_um), **extra}
        return self._basic_pcell(parent, "STROKED_POLYGON", p,
                                 position_um=position_um, rotation=rotation,
                                 mirror=mirror, magnification=magnification)

    # ---- M3 pcell introspection ----
    def pcell_libraries(self) -> dict:
        return self.call("pcell.libraries")

    def pcell_list(self, library: str = "Basic") -> dict:
        return self.call("pcell.list", {"library": library})

    def pcell_info(self, pcell: str, library: str = "Basic") -> dict:
        return self.call("pcell.info", {"library": library, "pcell": pcell})

    # ---- M3 Round 5: edit history ----
    def edit_undo(self) -> dict:
        return self.call("edit.undo")

    def edit_redo(self) -> dict:
        return self.call("edit.redo")

    def edit_status(self, *, debug: bool = False) -> dict:
        return self.call("edit.status", {"debug": bool(debug)})

    def selection_get(self, **kwargs) -> dict:
        return self.call("selection.get", kwargs)

    def selection_clear(self) -> dict:
        return self.call("selection.clear")

    def selection_set_box(self, cell, bbox_dbu, **kwargs) -> dict:
        return self.call("selection.set_box", {
            "cell": cell, "bbox_dbu": bbox_dbu, **kwargs,
        })

    def transfer_pending_set(self, package: dict) -> dict:
        return self.call("transfer.pending_set", {"package": dict(package)})

    def transfer_pending_status(self) -> dict:
        return self.call("transfer.pending_status")

    def transfer_pending_clear(self) -> dict:
        return self.call("transfer.pending_clear")

    def transfer_paste_pending(self, *, dry_run: bool = False, clear_after: bool = True) -> dict:
        return self.call("transfer.paste_pending", {
            "dry_run": bool(dry_run),
            "clear_after": bool(clear_after),
        })

    def transfer_import_cell_tree_package(self, path: str, source_cell: str, *, dry_run: bool = False) -> dict:
        return self.call("transfer.import_cell_tree_package", {
            "path": str(path),
            "source_cell": source_cell,
            "dry_run": bool(dry_run),
        })

    def session_mark_klive_target(self) -> dict:
        return self.call("session.mark_klive_target")

    def session_label_set(
        self,
        session_id: str,
        label: str,
        *,
        aliases=None,
        description: str | None = None,
    ) -> dict:
        params = {
            "session_id": str(session_id),
            "label": str(label),
            "aliases": [str(a) for a in (aliases or [])],
        }
        if description:
            params["description"] = str(description)
        return self.call("session.label_set", params)

    def screenshot(self, mode: str = "base64", **kwargs) -> dict:
        return self.call("view.screenshot", {"mode": mode, **kwargs})

    def zoom_fit(self) -> dict:
        return self.call("view.zoom_fit")

    def zoom_box(self, bbox_um=None, *, bbox_dbu=None) -> dict:
        """Zoom the viewport to a bbox. Prefer `bbox_um=[x1,y1,x2,y2]`
        (microns, klink's standard unit); `bbox_dbu=[x1,y1,x2,y2]`
        (integer database units, converted server-side via the active
        layout's dbu) is also accepted. Provide exactly one."""
        p = {}
        if bbox_um is not None:
            p["bbox_um"] = list(bbox_um)
        if bbox_dbu is not None:
            p["bbox_dbu"] = list(bbox_dbu)
        return self.call("view.zoom_box", p)

    def viewport(self) -> dict:
        return self.call("view.viewport")

    def new_tab(self, cell_name: str = "TOP", dbu: float = 0.001) -> dict:
        """Open a new empty layout tab (made current). The response's
        `previous_current_index` lets scratch-tab workflows restore the
        user's tab afterwards via activate_tab."""
        return self.call("view.new_tab", {"cell_name": cell_name, "dbu": dbu})

    def hier_levels(self, min=None, max=None) -> dict:
        """Read (no args) or set the displayed hierarchy depth."""
        p = {}
        if min is not None:
            p["min"] = min
        if max is not None:
            p["max"] = max
        return self.call("view.hier_levels", p)

    def show_cell(self, cell, zoom_fit: bool = True) -> dict:
        return self.call("view.show_cell", {"cell": cell, "zoom_fit": bool(zoom_fit)})

    # ---- exec.python escape hatch (M4) ----
    def exec_python(
        self,
        code: str,
        *,
        reset: bool = False,
        stdout_limit: Optional[int] = None,
        stderr_limit: Optional[int] = None,
        result_mode: str = "auto",
    ) -> dict:
        """Raw wrapper around `exec.python`.

        Returns the full server response: stdout/stderr strings,
        return_value (Jupyter-style last-expression), exception dict
        (None on success), wall_ms, etc. Does NOT raise on user-code
        exceptions — inspect `result["exception"]` instead. Raises
        `KLinkServerError` only on malformed requests (missing code,
        syntax error, oversized).
        """
        params: Dict[str, Any] = {"code": code, "reset": bool(reset),
                                  "result_mode": result_mode}
        if stdout_limit is not None:
            params["stdout_limit"] = int(stdout_limit)
        if stderr_limit is not None:
            params["stderr_limit"] = int(stderr_limit)
        return self.call("exec.python", params)

    def pyeval(self, expr: str, *, reset: bool = False) -> Any:
        """Evaluate a single expression and return its value.

        Raises `RuntimeError` if user code threw; passes stdout/stderr
        on the exception message for debugging. For multi-statement
        snippets use `pyexec` instead.
        """
        res = self.exec_python(expr, reset=reset)
        if res.get("exception") is not None:
            exc = res["exception"]
            raise RuntimeError(
                f"remote {exc['type']}: {exc['message']}\n"
                f"--- stdout ---\n{res.get('stdout', '')}"
                f"--- stderr ---\n{res.get('stderr', '')}"
                f"--- traceback ---\n{exc.get('traceback', '')}"
            )
        if not res.get("had_result"):
            raise RuntimeError(
                "pyeval expected an expression but last line was a statement; "
                "use pyexec for side-effect code."
            )
        return res.get("return_value")

    def pyexec(
        self,
        code: str,
        *,
        reset: bool = False,
        return_stdout: bool = True,
    ) -> str:
        """Run multi-statement Python; return captured stdout.

        Ignores last-expression value (the usual pyexec semantic).
        Raises `RuntimeError` on user-code exceptions. If you need
        the full structured response (stderr, return_value, wall_ms),
        call `exec_python` directly.
        """
        res = self.exec_python(code, reset=reset, result_mode="none")
        if res.get("exception") is not None:
            exc = res["exception"]
            raise RuntimeError(
                f"remote {exc['type']}: {exc['message']}\n"
                f"--- stdout ---\n{res.get('stdout', '')}"
                f"--- stderr ---\n{res.get('stderr', '')}"
                f"--- traceback ---\n{exc.get('traceback', '')}"
            )
        return res.get("stdout", "") if return_stdout else ""

    def exec_reset(self) -> dict:
        """Wipe this connection's per-session Python namespace."""
        return self.call("exec.reset")

    # ---- drc.run escape hatch (P3) ----
    def drc_run(
        self,
        code: str,
        *,
        input_layout: Optional[str] = None,
        output_rdb: Optional[str] = None,
        top_cell: Optional[str] = None,
        result_mode: str = "summary",
        stdout_limit: Optional[int] = None,
        stderr_limit: Optional[int] = None,
    ) -> dict:
        """Raw wrapper around `drc.run`.

        Returns the full server response: stdout/stderr strings,
        rdb_file path (if generated), rdb_summary, exception dict
        (None on success), wall_ms. Does NOT raise on DRC script
        errors — inspect ``result["exception"]`` instead.
        """
        params: Dict[str, Any] = {"code": code, "result_mode": result_mode}
        if input_layout is not None:
            params["input_layout"] = input_layout
        if output_rdb is not None:
            params["output_rdb"] = output_rdb
        if top_cell is not None:
            params["top_cell"] = top_cell
        if stdout_limit is not None:
            params["stdout_limit"] = int(stdout_limit)
        if stderr_limit is not None:
            params["stderr_limit"] = int(stderr_limit)
        return self.call("drc.run", params)

    # ---- recorder (macro recorder) ----
    def recorder_start(self, output_path: Optional[str] = None) -> dict:
        p: Dict[str, Any] = {}
        if output_path is not None:
            p["output_path"] = output_path
        return self.call("recorder.start", p)

    def recorder_stop(self, output_path: Optional[str] = None) -> dict:
        p: Dict[str, Any] = {}
        if output_path is not None:
            p["output_path"] = output_path
        return self.call("recorder.stop", p)

    def recorder_status(self) -> dict:
        return self.call("recorder.status")

    # ---- event subscription ----
    def event_channels(self) -> dict:
        return self.call("events.channels")

    def subscribe(self, channels) -> dict:
        if isinstance(channels, str):
            channels = [channels]
        return self.call("events.subscribe", {"channels": list(channels)})

    def unsubscribe(self, channels="*") -> dict:
        if isinstance(channels, str):
            channels = [channels]
        return self.call("events.unsubscribe", {"channels": list(channels)})

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def on(self, event_name: str, handler: Callable[[dict], None]) -> None:
        with self._event_handlers_lock:
            self._event_handlers.setdefault(event_name, []).append(handler)

    def off(self, event_name: str, handler: Optional[Callable] = None) -> None:
        with self._event_handlers_lock:
            if handler is None:
                self._event_handlers.pop(event_name, None)
            else:
                lst = self._event_handlers.get(event_name, [])
                if handler in lst:
                    lst.remove(handler)

    # ------------------------------------------------------------------
    # LLM helper
    # ------------------------------------------------------------------
    def generate_tool_schemas(self, flavor: str = "openai") -> List[dict]:
        """
        Fetch the server's method catalogue and convert it into the
        function/tool schema expected by a given LLM provider.

        flavor:
          - "openai"    : [{"type":"function","function":{"name":..., "description":..., "parameters":{...}}}]
          - "anthropic" : [{"name":..., "description":..., "input_schema":{...}}]
          - "raw"       : server's own catalogue (pass-through)
        """
        cat = self.methods()["methods"]
        if flavor == "raw":
            return cat
        out = []
        for m in cat:
            name = m["name"].replace(".", "__")  # function names usually disallow dots
            params = m.get("params") or {"type": "object"}
            if flavor == "openai":
                out.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": m.get("description", ""),
                        "parameters": params,
                    },
                })
            elif flavor == "anthropic":
                out.append({
                    "name": name,
                    "description": m.get("description", ""),
                    "input_schema": params,
                })
            else:
                raise ValueError(f"unknown flavor: {flavor!r}")
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _drain_pending(self, message: str) -> None:
        """Fail every in-flight call() immediately with ERR_CONN_CLOSED."""
        with self._pending_lock:
            pendings = list(self._pending.values())
            self._pending.clear()
        sentinel = {"ok": False, "error": {
            "code": "ERR_CONN_CLOSED",
            "message": message,
        }}
        for q in pendings:
            try:
                q.put_nowait(sentinel)
            except Exception:
                pass

    def _reader_loop(self) -> None:
        try:
            while self._running:
                msg = self._transport.recv_line()
                if msg is None:
                    break
                if "event" in msg:
                    self._deliver_event(msg)
                    continue
                req_id = msg.get("id")
                if req_id is None:
                    continue
                with self._pending_lock:
                    q = self._pending.get(req_id)
                if q is not None:
                    try:
                        q.put_nowait(msg)
                    except queue.Full:
                        pass
        finally:
            self._running = False
            # The server side went away (or transport failed): fail blocked
            # callers NOW instead of letting each one sit out its full call
            # timeout waiting for a response that can never arrive.
            self._drain_pending(
                "connection lost before the response arrived (KLayout closed "
                "or the plugin stopped); reconnect and retry")

    def _deliver_event(self, msg: dict) -> None:
        name = msg.get("event")
        data = msg.get("data", {})
        with self._event_handlers_lock:
            handlers = list(self._event_handlers.get(name, []))
        for h in handlers:
            try:
                h(data)
            except Exception as e:
                print(f"[klink] event handler for {name!r} raised: {e}")

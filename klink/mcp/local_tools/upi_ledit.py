"""L-Edit bridge MCP tools (domain: bridge_ledit).

One user intention = one call, per docs/AGENT_TOOL_DESIGN.md. These tools
run in the MCP runtime on the same machine as L-Edit; transport is the
file-exchange bridge (klink.bridges.ledit), KLayout side goes through the
session-scoped klink client. Layer identity migrates BY NAME + GDS number
in both directions — L-Edit names become named KLayout layers and vice
versa.
"""

from __future__ import annotations

import json

from . import local_tool
from ..results import _error_result, _json_result
from ...bridges.ledit import (LEditBridgeClient, LEditBridgeError,
                              build_layer_map, import_cell_tree,
                              merge_layer_name, push_cell_tree,
                              require_capability, selection_to_items)

_REQ_BYTES_BUDGET = 50 * 1024   # stay under the macro's 64 KiB request cap


def _bridge(arguments: dict) -> LEditBridgeClient:
    return LEditBridgeClient(
        namespace=str(arguments.get("namespace") or "default"))


@local_tool(
    "ledit.status",
    "Discover L-Edit bridge namespaces and report liveness + handshake "
    "for one: hello heartbeat age, macro version/capabilities, current "
    ".tdb file and cell. When the macro supports it, also lists every "
    "open design (designs, with the visible/changed flags) and the "
    "active design's cells (with T-Cell flags) and, with macro >= 0.5.6, "
    "the open windows (windows[]) -- one call answers \"what is in "
    "L-Edit right now\". Start here when any ledit.* call "
    "misbehaves; errors name the exact fix (load/reload the macro, "
    "close a modal dialog, ...).",
    {
        "type": "object",
        "properties": {
            "namespace": {"type": "string", "default": "default",
                          "description": "Bridge namespace (one per L-Edit instance)."},
        },
        "additionalProperties": False,
    },
)
def _tool_ledit_status(ctx, arguments: dict) -> dict:
    # Thin shell over LEditBridgeClient.status(): the same health check has
    # to be available to plain scripts, and two implementations of it drift.
    try:
        return _json_result(_bridge(arguments).status())
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "ledit.import_selection",
    "Import the CURRENT L-Edit selection into a fresh KLayout landing "
    "cell (fresh GET each call -- never stale geometry). Generic "
    "capability matching: box->box, wire->path, circle->Basic.CIRCLE "
    "PCell (stays parametric), any other outline->polygon; "
    "non-convertible objects are reported, never silently dropped. "
    "Layers migrate by NAME + GDS number (L-Edit's own table; unmapped "
    "layers get auto-assigned numbers, reported).",
    {
        "type": "object",
        "properties": {
            "target_cell": {"type": "string",
                            "default": "from_ledit_selection",
                            "description": "KLayout landing cell (recreated each call)."},
            "namespace": {"type": "string", "default": "default"},
            "session": {"type": "string",
                        "description": "KLayout session id/label/alias (default: primary)."},
        },
        "additionalProperties": False,
    },
)
def _tool_ledit_import_selection(ctx, arguments: dict) -> dict:
    try:
        bridge = _bridge(arguments)
        sel = bridge.get_selection()
        if not sel.get("objects"):
            return _error_result(
                "nothing convertible selected in L-Edit "
                f"(count={sel.get('count', 0)}, skipped={sel.get('skipped')}). "
                "Select shapes (or T-Cell instances via get_cell on their "
                "variant) in L-Edit, then call again.")

        layer_table = bridge.get_layers()
        mapping, auto = build_layer_map(layer_table)
        pair_to_name = {pair: name for name, pair in mapping.items()}
        shapes, pcells, failures = selection_to_items(
            sel["objects"], lambda n: mapping.get(n, (999, 99)))

        target = str(arguments.get("target_cell") or "from_ledit_selection")
        client, close_after = ctx._session_scoped_client(
            arguments.get("session"))
        try:
            try:
                client.cell_delete(target)     # fresh landing cell
            except Exception:
                pass
            cell = client.cell_create(target)["name"]
            pairs = {(i["layer"], i["datatype"]) for i in shapes} | {
                (p["params"]["layer"]["layer"],
                 p["params"]["layer"]["datatype"]) for p in pcells}
            # layer-NAME migration with the merge policy: fill empty names,
            # keep equal ones, append "existing|incoming" when they differ
            # (never overwrite the user's own naming, never lose L-Edit's)
            existing_names = {
                (e["layer"], e["datatype"]): e.get("name") or ""
                for e in client.call("layer.list")["layers"]}
            for gl, gd in sorted(pairs):
                merged = merge_layer_name(
                    existing_names.get((gl, gd), ""),
                    pair_to_name.get((gl, gd), ""))
                client.call("layer.ensure", {
                    "layer": gl, "datatype": gd, "name": merged})
            if shapes:
                client.shape_insert_many(cell, shapes)
            if pcells:
                client.instance_insert_pcell_many(cell, pcells)
            client.call("view.show_cell", {"cell": cell})
            client.call("view.zoom_fit")
        finally:
            if close_after:
                try:
                    client.close()
                except Exception:
                    pass
        return _json_result({
            "cell": cell,
            "source_cell": sel.get("cell"),
            "imported_shapes": len(shapes),
            "imported_circle_pcells": len(pcells),
            "not_convertible": failures,
            "skipped_in_ledit": sel.get("skipped", {}),
            "auto_assigned_gds": auto,
        })
    except LEditBridgeError as exc:
        return _error_result(str(exc))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "ledit.push_cell_tree",
    "Push a KLayout cell AND EVERY CELL BELOW IT into L-Edit, keeping the "
    "hierarchy: cells are created children-first and instances are rebuilt "
    "as real instances (ledit.push_cell is the FLAT one -- it refuses "
    "sub-instances). Idempotent by default (each cell is cleared before "
    "redraw, since L-Edit's draw only appends). The whole tree goes over "
    "as ONE ordered batch. Instances L-Edit placement cannot express "
    "exactly (magnification, non-orthogonal rotation, skewed array) are "
    "REPORTED in unsupported_instances, never approximated. For a whole "
    "design rather than a subtree, a GDS file via import_gds is cheaper.",
    {
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"type": "string",
                     "description": "KLayout root cell; its whole subtree is pushed."},
            "expect_file": {"type": "string",
                            "description": "Refuse to write unless this L-Edit design is active (recommended)."},
            "clear": {"type": "boolean", "default": True,
                      "description": "Clear each target cell first so re-running is idempotent."},
            "namespace": {"type": "string", "default": "default"},
            "session": {"type": "string",
                        "description": "KLayout session id/label/alias (default: primary)."},
        },
        "additionalProperties": False,
    },
)
def _tool_ledit_push_cell_tree(ctx, arguments: dict) -> dict:
    try:
        bridge = _bridge(arguments)
        bridge.require_alive()
        client, close_after = ctx._session_scoped_client(
            arguments.get("session"))
        try:
            report = push_cell_tree(
                client, bridge, str(arguments["cell"]),
                expect_file=str(arguments.get("expect_file") or ""),
                clear=bool(arguments.get("clear", True)))
        finally:
            if close_after:
                try:
                    client.close()
                except Exception:
                    pass
        return _json_result(report)
    except LEditBridgeError as exc:
        return _error_result(str(exc))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "ledit.import_cell_tree",
    "Import an L-Edit cell AND ITS HIERARCHY into KLayout as real cells + "
    "instances (ledit.import_selection reads the current SELECTION and "
    "drops instances by design). Cells are rebuilt children-first, layer "
    "identity travels by NAME + GDS number, and each target cell is "
    "recreated so re-importing is idempotent. Shapes L-Edit exposes "
    "without a convertible outline are reported in not_convertible.",
    {
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"type": "string",
                     "description": "L-Edit root cell; its whole subtree is imported."},
            "namespace": {"type": "string", "default": "default"},
            "session": {"type": "string",
                        "description": "KLayout session id/label/alias (default: primary)."},
        },
        "additionalProperties": False,
    },
)
def _tool_ledit_import_cell_tree(ctx, arguments: dict) -> dict:
    try:
        bridge = _bridge(arguments)
        bridge.require_alive()
        client, close_after = ctx._session_scoped_client(
            arguments.get("session"))
        try:
            report = import_cell_tree(client, bridge, str(arguments["cell"]))
        finally:
            if close_after:
                try:
                    client.close()
                except Exception:
                    pass
        return _json_result(report)
    except LEditBridgeError as exc:
        return _error_result(str(exc))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "ledit.push_cell",
    "Push a KLayout cell's flat geometry into an L-Edit cell through the "
    "bridge: boxes, paths (->wires) and polygons transfer; text and "
    "sub-instances are counted and reported, not silently dropped. "
    "Layers are created in L-Edit with the KLayout layer NAME when one "
    "exists (else L<gds>D<dt>) plus the GDS numbers. Draw is append-only "
    "on the L-Edit side -- pass a fresh ledit_cell to regenerate.",
    {
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"type": "string",
                     "description": "KLayout source cell (flat shapes only; flatten first if hierarchical)."},
            "ledit_cell": {"type": "string",
                           "description": "Target L-Edit cell name (default: same as cell)."},
            "namespace": {"type": "string", "default": "default"},
            "session": {"type": "string",
                        "description": "KLayout session id/label/alias (default: primary)."},
        },
        "additionalProperties": False,
    },
)
def _tool_ledit_push_cell(ctx, arguments: dict) -> dict:
    try:
        source = str(arguments["cell"])
        target = str(arguments.get("ledit_cell") or source)
        bridge = _bridge(arguments)
        bridge.require_alive()

        client, close_after = ctx._session_scoped_client(
            arguments.get("session"))
        try:
            dbu = float(client.call("layout.info").get("dbu", 0.001))
            ltab = client.call("layer.list")["layers"]
            by_index = {e["layer_index"]: e for e in ltab}
            q = client.shape_query(source)
            if q.get("truncated"):
                return _error_result(
                    f"shape.query truncated for cell '{source}' -- push a "
                    "smaller/flattened cell, or split it")
            shapes = q.get("shapes", [])
            try:
                n_instances = len(client.instance_query(source)
                                  .get("instances", []))
            except Exception:
                n_instances = 0
        finally:
            if close_after:
                try:
                    client.close()
                except Exception:
                    pass

        def um(v):
            return v * dbu

        items, skipped = [], {}
        used_layers = {}
        for s in shapes:
            e = by_index.get(s.get("layer_index"), {})
            lname = e.get("name") or f"L{e.get('layer', 0)}D{e.get('datatype', 0)}"
            used_layers[lname] = (int(e.get("layer", 0)),
                                  int(e.get("datatype", 0)))
            t = s.get("type")
            if t == "box" and s.get("bbox_dbu"):
                items.append({"kind": "box", "layer": lname,
                              "bbox_um": [um(v) for v in s["bbox_dbu"]]})
            elif t == "path" and s.get("points_dbu"):
                items.append({"kind": "wire", "layer": lname,
                              "points_um": [[um(x), um(y)] for x, y in
                                            s["points_dbu"]],
                              "width_um": um(s.get("width_dbu", 0))})
            elif t == "polygon" and s.get("points_dbu"):
                items.append({"kind": "polygon", "layer": lname,
                              "points_um": [[um(x), um(y)] for x, y in
                                            s["points_dbu"]]})
            else:
                skipped[t or "?"] = skipped.get(t or "?", 0) + 1

        if n_instances:
            skipped["instance"] = n_instances
        if not items:
            return _error_result(
                f"cell '{source}' has no transferable flat shapes "
                f"(skipped: {skipped}). Flatten instances first "
                "(cell.flatten) or pick another cell.")

        bridge.create_cell(target)
        for lname, (gl, gd) in sorted(used_layers.items()):
            bridge.ensure_layer(lname, gl, gd)
        # chunk to respect the macro's request size cap
        sent, batch, batch_bytes = 0, [], 0
        for item in items:
            cost = len(json.dumps(item))
            if batch and batch_bytes + cost > _REQ_BYTES_BUDGET:
                bridge.draw(batch, cell=target)
                sent += len(batch)
                batch, batch_bytes = [], 0
            batch.append(item)
            batch_bytes += cost
        if batch:
            bridge.draw(batch, cell=target)
            sent += len(batch)

        return _json_result({
            "ledit_cell": target,
            "drawn": sent,
            "layers": {n: list(p) for n, p in sorted(used_layers.items())},
            "skipped_shape_types": skipped,
            "note": "L-Edit draw is append-only; rerun into a fresh "
                    "ledit_cell to regenerate",
        })
    except LEditBridgeError as exc:
        return _error_result(str(exc))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "ledit.show_cell",
    "Open (or raise) a layout window on an L-Edit cell and make it the "
    "visible cell -- the answer to 'show me X in L-Edit' / 'open cell X'. "
    "Reports window_opened (a new window was created) and via. Cell "
    "names come from ledit.status cells[]. Read-only on the design: no "
    "geometry is touched.",
    {
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"type": "string",
                     "description": "L-Edit cell to show (from ledit.status cells[])."},
            "namespace": {"type": "string", "default": "default"},
        },
        "additionalProperties": False,
    },
)
def _tool_ledit_show_cell(ctx, arguments: dict) -> dict:
    try:
        bridge = _bridge(arguments)
        ping = bridge.ping()
        require_capability(ping, "show_cell")
        return _json_result(bridge.show_cell(str(arguments["cell"])))
    except LEditBridgeError as exc:
        return _error_result(str(exc))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "ledit.set_cell_hidden",
    "Hide an L-Edit cell from the cell lists (the 'Hide In Lists' flag "
    "that auto-generated T-Cell variants carry) or show it again -- "
    "'hide cell X' / 'unhide X'. Round-trips through L-Edit's own flag "
    "(LCell_SetShowInLists) and reports the value read back; if the "
    "stored property disagrees with the flag both are reported (hidden, "
    "hidden_property), never one picked silently. ledit.status cells[] "
    "reads the same flag.",
    {
        "type": "object",
        "required": ["cell", "hidden"],
        "properties": {
            "cell": {"type": "string",
                     "description": "L-Edit cell to hide/show."},
            "hidden": {"type": "boolean",
                       "description": "True to hide from cell lists, false to show again."},
            "namespace": {"type": "string", "default": "default"},
        },
        "additionalProperties": False,
    },
)
def _tool_ledit_set_cell_hidden(ctx, arguments: dict) -> dict:
    try:
        bridge = _bridge(arguments)
        ping = bridge.ping()
        require_capability(ping, "set_cell_hidden")
        return _json_result(bridge.set_cell_hidden(
            str(arguments["cell"]), bool(arguments["hidden"])))
    except LEditBridgeError as exc:
        return _error_result(str(exc))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "ledit.list_windows",
    "List every open L-Edit window (layout, text, log, ...) with its "
    "index, file, cell and whether it is the visible one. Works with no "
    "design open. The index is the handle ledit.close_window takes.",
    {
        "type": "object",
        "properties": {
            "namespace": {"type": "string", "default": "default"},
        },
        "additionalProperties": False,
    },
)
def _tool_ledit_list_windows(ctx, arguments: dict) -> dict:
    try:
        bridge = _bridge(arguments)
        ping = bridge.ping()
        require_capability(ping, "list_windows")
        windows = bridge.list_windows()
        return _json_result({"windows": windows, "count": len(windows)})
    except LEditBridgeError as exc:
        return _error_result(str(exc))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "ledit.close_window",
    "Close L-Edit window(s): by cell name (all layout windows on that "
    "cell; pass file when two open designs share the name) or by index "
    "from ledit.list_windows. Reports matched/closed. No last-window "
    "guard: closing a design's last window may close that design, so "
    "confirm with the user before closing windows you did not open.",
    {
        "type": "object",
        "properties": {
            "cell": {"type": "string",
                     "description": "Close layout window(s) on this cell."},
            "index": {"type": "integer",
                      "description": "Close the window at this index (from ledit.list_windows)."},
            "file": {"type": "string",
                     "description": "Disambiguate cell by design file when two designs share the cell name."},
            "namespace": {"type": "string", "default": "default"},
        },
        "additionalProperties": False,
    },
)
def _tool_ledit_close_window(ctx, arguments: dict) -> dict:
    try:
        bridge = _bridge(arguments)
        ping = bridge.ping()
        require_capability(ping, "close_window")
        cell = arguments.get("cell")
        index = arguments.get("index")
        return _json_result(bridge.close_window(
            cell=str(cell) if cell is not None else None,
            index=int(index) if index is not None else None,
            file=str(arguments.get("file") or "")))
    except LEditBridgeError as exc:
        return _error_result(str(exc))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "ledit.layout_view",
    "One verb for an L-Edit cell's view: with neither rect_um nor home "
    "it READS the current view; rect_um=[left,bottom,right,top] "
    "(microns) SETS it (zoom to that area -- 'zoom to the device' / "
    "'look at this region'); home=true resets to the cell's home view. "
    "Always returns the view AFTER the call plus has_window (without an "
    "open window the rect is not meaningful -- ledit.show_cell first). "
    "Default cell = the visible cell.",
    {
        "type": "object",
        "properties": {
            "cell": {"type": "string",
                     "description": "L-Edit cell (default: the visible cell)."},
            "rect_um": {"type": "array", "items": {"type": "number"},
                        "minItems": 4, "maxItems": 4,
                        "description": "[left, bottom, right, top] in microns; sets the view."},
            "home": {"type": "boolean",
                     "description": "Reset to the cell's home view."},
            "namespace": {"type": "string", "default": "default"},
        },
        "additionalProperties": False,
    },
)
def _tool_ledit_layout_view(ctx, arguments: dict) -> dict:
    try:
        bridge = _bridge(arguments)
        ping = bridge.ping()
        require_capability(ping, "layout_view")
        cell = arguments.get("cell")
        return _json_result(bridge.layout_view(
            cell=str(cell) if cell is not None else None,
            rect_um=arguments.get("rect_um"),
            home=bool(arguments.get("home", False))))
    except LEditBridgeError as exc:
        return _error_result(str(exc))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "ledit.save_image",
    "Render an L-Edit cell to an image file (PNG/BMP/JPG by extension) "
    "via LCell_SaveImageToFile -- whole cell by default, or rect_um for "
    "an area. USER-REQUESTED ARTIFACT ONLY: call it when the user asks "
    "for a picture/screenshot of the L-Edit cell, never as verification "
    "evidence (verify with ledit.status / get_cell geometry, same rule "
    "as KLayout view.screenshot). The folder must already exist. "
    "Reports path and bytes.",
    {
        "type": "object",
        "required": ["cell", "path"],
        "properties": {
            "cell": {"type": "string",
                     "description": "L-Edit cell to render."},
            "path": {"type": "string",
                     "description": "Output image path (.png/.bmp/.jpg); folder must already exist."},
            "width_px": {"type": "integer", "default": 1600},
            "height_px": {"type": "integer", "default": 1200},
            "dpi": {"type": "integer", "default": 96},
            "rect_um": {"type": "array", "items": {"type": "number"},
                        "minItems": 4, "maxItems": 4,
                        "description": "[left, bottom, right, top] in microns; default the whole cell."},
            "namespace": {"type": "string", "default": "default"},
        },
        "additionalProperties": False,
    },
)
def _tool_ledit_save_image(ctx, arguments: dict) -> dict:
    try:
        bridge = _bridge(arguments)
        ping = bridge.ping()
        require_capability(ping, "save_image")
        return _json_result(bridge.save_image(
            str(arguments["cell"]), str(arguments["path"]),
            width_px=int(arguments.get("width_px", 1600)),
            height_px=int(arguments.get("height_px", 1200)),
            dpi=int(arguments.get("dpi", 96)),
            rect_um=arguments.get("rect_um")))
    except LEditBridgeError as exc:
        return _error_result(str(exc))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "ledit.delete_cell",
    "DESTRUCTIVE: delete an L-Edit cell by explicit name. Refused (unless "
    "force=true) when the cell is the visible cell, is instanced by other "
    "cells (referenced_by names them -- deleting it deletes those "
    "instances too), or is a T-Cell generator. Confirm with the user "
    "before deleting anything they drew; klink's own scratch cells (push "
    "targets, probes) are fair game.",
    {
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"type": "string",
                     "description": "L-Edit cell to delete."},
            "force": {"type": "boolean", "default": False,
                      "description": "Delete even if visible / instanced / a T-Cell generator."},
            "namespace": {"type": "string", "default": "default"},
        },
        "additionalProperties": False,
    },
)
def _tool_ledit_delete_cell(ctx, arguments: dict) -> dict:
    try:
        bridge = _bridge(arguments)
        ping = bridge.ping()
        require_capability(ping, "delete_cell")
        return _json_result(bridge.delete_cell(
            str(arguments["cell"]), force=bool(arguments.get("force", False))))
    except LEditBridgeError as exc:
        return _error_result(str(exc))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "ledit.rename_cell",
    "Rename an L-Edit cell; refuses when new_name is already taken "
    "(ledit.status cells[] shows what exists).",
    {
        "type": "object",
        "required": ["cell", "new_name"],
        "properties": {
            "cell": {"type": "string",
                     "description": "L-Edit cell to rename."},
            "new_name": {"type": "string",
                        "description": "New name; must not already exist."},
            "namespace": {"type": "string", "default": "default"},
        },
        "additionalProperties": False,
    },
)
def _tool_ledit_rename_cell(ctx, arguments: dict) -> dict:
    try:
        bridge = _bridge(arguments)
        ping = bridge.ping()
        require_capability(ping, "rename_cell")
        return _json_result(bridge.rename_cell(
            str(arguments["cell"]), str(arguments["new_name"])))
    except LEditBridgeError as exc:
        return _error_result(str(exc))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "ledit.delete_objects",
    "DESTRUCTIVE: delete shapes inside an L-Edit cell by layer and/or "
    "area. rect_um=[left,bottom,right,top] (microns) deletes only objects "
    "whose bounding box lies entirely INSIDE the rect (a route merely "
    "crossing it stays); layer restricts to one layer; give at least one. "
    "Instances are never deleted here (clear_cell resets a whole cell). "
    "Reports deleted and by_layer. Confirm with the user first unless the "
    "cell is klink's own.",
    {
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"type": "string",
                     "description": "L-Edit cell to delete shapes from."},
            "layer": {"type": "string",
                     "description": "Restrict to this layer (name)."},
            "rect_um": {"type": "array", "items": {"type": "number"},
                        "minItems": 4, "maxItems": 4,
                        "description": "[left, bottom, right, top] in microns; object MBB must lie entirely inside."},
            "namespace": {"type": "string", "default": "default"},
        },
        "additionalProperties": False,
    },
)
def _tool_ledit_delete_objects(ctx, arguments: dict) -> dict:
    try:
        bridge = _bridge(arguments)
        ping = bridge.ping()
        require_capability(ping, "delete_objects")
        layer = arguments.get("layer")
        return _json_result(bridge.delete_objects(
            str(arguments["cell"]),
            layer=str(layer) if layer is not None else None,
            rect_um=arguments.get("rect_um")))
    except LEditBridgeError as exc:
        return _error_result(str(exc))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "ledit.close_design",
    "Close an OPEN L-Edit design by name (from ledit.status designs[]). A "
    "design with unsaved changes is refused unless discard=true. Closing "
    "a design's last WINDOW does not close it -- this does. Use it to "
    "drop scratch designs klink created (new_design); confirm with the "
    "user before closing theirs.",
    {
        "type": "object",
        "required": ["file"],
        "properties": {
            "file": {"type": "string",
                     "description": "Open design name (from ledit.status designs[])."},
            "discard": {"type": "boolean", "default": False,
                       "description": "Discard unsaved changes and close anyway."},
            "namespace": {"type": "string", "default": "default"},
        },
        "additionalProperties": False,
    },
)
def _tool_ledit_close_design(ctx, arguments: dict) -> dict:
    try:
        bridge = _bridge(arguments)
        ping = bridge.ping()
        require_capability(ping, "close_design")
        return _json_result(bridge.close_design(
            str(arguments["file"]),
            discard=bool(arguments.get("discard", False))))
    except LEditBridgeError as exc:
        return _error_result(str(exc))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "ledit.run_drc",
    "Run L-Edit's own DRC on a cell (whole cell, or "
    "rect_um=[left,bottom,right,top] microns for an area) with the "
    "design's loaded rule set; reports the error COUNT and status only "
    "-- L-Edit v16.3 does not expose the violation geometry through the "
    "UPI; for violation geometry use export_gds and klink's KLayout-side "
    "drc tools. Also reports the rule count. Refused when the design "
    "has no DRC rules.",
    {
        "type": "object",
        "properties": {
            "cell": {"type": "string",
                     "description": "L-Edit cell to run DRC on (default: the visible cell)."},
            "rect_um": {"type": "array", "items": {"type": "number"},
                        "minItems": 4, "maxItems": 4,
                        "description": "[left, bottom, right, top] in microns; default the whole cell."},
            "namespace": {"type": "string", "default": "default"},
        },
        "additionalProperties": False,
    },
)
def _tool_ledit_run_drc(ctx, arguments: dict) -> dict:
    try:
        bridge = _bridge(arguments)
        ping = bridge.ping()
        require_capability(ping, "run_drc")
        cell = arguments.get("cell")
        return _json_result(bridge.run_drc(
            cell=str(cell) if cell is not None else None,
            rect_um=arguments.get("rect_um")))
    except LEditBridgeError as exc:
        return _error_result(str(exc))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "ledit.drc_summary",
    "Read the last DRC result of an L-Edit cell without re-running: "
    "errors and status (needed = never run or stale, passed, failed). "
    "errors is null until a run has happened.",
    {
        "type": "object",
        "properties": {
            "cell": {"type": "string",
                     "description": "L-Edit cell to read (default: the visible cell)."},
            "namespace": {"type": "string", "default": "default"},
        },
        "additionalProperties": False,
    },
)
def _tool_ledit_drc_summary(ctx, arguments: dict) -> dict:
    try:
        bridge = _bridge(arguments)
        ping = bridge.ping()
        require_capability(ping, "drc_summary")
        cell = arguments.get("cell")
        return _json_result(bridge.drc_summary(
            cell=str(cell) if cell is not None else None))
    except LEditBridgeError as exc:
        return _error_result(str(exc))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "ledit.export_gds",
    "Write an L-Edit design (or one cell with its hierarchy) to a GDS "
    "file with LFile_ExportGDSII -- the cheap L-Edit -> KLayout return "
    "path: then layout.file_info / layout.import_file in KLayout read it "
    "as-is (no padding needed in this direction). GDS limits cell names "
    "to cell_name_length (32 standard; KLayout accepts longer, raise it "
    "for a round trip of long klink names). The folder must already "
    "exist; the export log is written to log_path (default next to the "
    "bridge inbox) and scanned for errors.",
    {
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string",
                     "description": "Output .gds path; the containing folder must already exist."},
            "cell": {"type": "string",
                     "description": "Export only this cell + its hierarchy (default: the whole design)."},
            "include_hierarchy": {"type": "boolean", "default": True,
                                  "description": "Include cell's sub-instances (only meaningful with cell set)."},
            "cell_name_length": {"type": "integer", "default": 32,
                                 "description": "GDSII cell-name length limit; raise for long klink names."},
            "log_path": {"type": "string",
                        "description": "Export log path (default: next to the bridge inbox)."},
            "namespace": {"type": "string", "default": "default"},
        },
        "additionalProperties": False,
    },
)
def _tool_ledit_export_gds(ctx, arguments: dict) -> dict:
    try:
        bridge = _bridge(arguments)
        ping = bridge.ping()
        require_capability(ping, "export_gds")
        cell = arguments.get("cell")
        return _json_result(bridge.export_gds(
            str(arguments["path"]),
            cell=str(cell) if cell is not None else None,
            include_hierarchy=bool(arguments.get("include_hierarchy", True)),
            cell_name_length=int(arguments.get("cell_name_length", 32)),
            log_path=str(arguments.get("log_path") or "")))
    except LEditBridgeError as exc:
        return _error_result(str(exc))
    except Exception as exc:
        return _error_result(str(exc))

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
import os

from . import local_tool
from ..results import _error_result, _json_result
from ...bridges.ledit import (LEditBridgeClient, LEditBridgeError,
                              build_layer_map, merge_layer_name,
                              selection_to_items)
from ...bridges.ledit.client import default_root

_REQ_BYTES_BUDGET = 50 * 1024   # stay under the macro's 64 KiB request cap


def _bridge(arguments: dict) -> LEditBridgeClient:
    return LEditBridgeClient(
        namespace=str(arguments.get("namespace") or "default"))


@local_tool(
    "ledit.status",
    "Discover L-Edit bridge namespaces and report liveness + handshake "
    "for one: hello heartbeat age, macro version/capabilities, current "
    ".tdb file and cell. Start here when any ledit.* call misbehaves; "
    "errors name the exact fix (load/reload the macro, close a modal "
    "dialog, ...).",
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
    try:
        root = default_root()
        namespaces = []
        if os.path.isdir(root):
            for entry in sorted(os.listdir(root)):
                if os.path.exists(os.path.join(root, entry, "hello.json")):
                    namespaces.append(entry)
        bridge = _bridge(arguments)
        result: dict = {"root": root, "namespaces": namespaces,
                        "namespace": bridge.namespace}
        try:
            hello = bridge.hello()
            result["hello"] = hello
            result["heartbeat_age_s"] = round(bridge.hello_age_s(), 1)
            # macro_alive = the bridge polls; design_ready = a .tdb is open
            # and commands can act. They fail differently - report both.
            result["macro_alive"] = bridge.alive()
            if result["macro_alive"]:
                try:
                    ping = bridge.ping()
                    result["ping"] = ping
                    result["design_ready"] = bool(ping.get("design_ready",
                                                           ping.get("file")))
                except LEditBridgeError as exc:
                    result["design_ready"] = False
                    result["error"] = str(exc)
                    result["next_action"] = exc.next_action
            else:
                result["design_ready"] = False
                result["next_action"] = (
                    "heartbeat stale: in L-Edit run Tools > klink: Bridge "
                    "Start, or reload ledit_bridge.cpp")
        except LEditBridgeError as exc:
            result["macro_alive"] = False
            result["design_ready"] = False
            result["error"] = str(exc)
            result["next_action"] = exc.next_action
        return _json_result(result)
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "ledit.import_selection",
    "Import the CURRENT L-Edit selection into a fresh KLayout landing "
    "cell (fresh GET each call — never stale geometry). Generic "
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
    "ledit.push_cell",
    "Push a KLayout cell's flat geometry into an L-Edit cell through the "
    "bridge: boxes, paths (->wires) and polygons transfer; text and "
    "sub-instances are counted and reported, not silently dropped. "
    "Layers are created in L-Edit with the KLayout layer NAME when one "
    "exists (else L<gds>D<dt>) plus the GDS numbers. Draw is append-only "
    "on the L-Edit side — pass a fresh ledit_cell to regenerate.",
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
                    f"shape.query truncated for cell '{source}' — push a "
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

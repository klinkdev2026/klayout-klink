"""Whole-cell-HIERARCHY transfer between KLayout and L-Edit over the RPC path.

`ledit.push_cell` and `ledit.import_selection` deliberately handle FLAT
geometry: they count sub-instances and tell you to flatten. That left the
only way to move a hierarchical design across the bridge being "flatten it
first", which is how a 93-cell design turned into 6224 polygons in one cell
and hit the request cap.

`import_gds` is the bulk lane and keeps the hierarchy, but it is a whole
file: it flattens parametric content to static geometry, maps layers by
number rather than name, and cannot push one subtree into an existing
design. This module is the RPC lane that keeps those properties: cell by
cell in dependency order, instances rebuilt as real instances, layer
identity carried by NAME.

Cost model that shapes the implementation: a bridge request costs about one
poll interval REGARDLESS of payload, so the whole tree goes over as one
ordered `batch` (auto-split only when the byte cap forces it) rather than
three round trips per cell.

Neither direction invents geometry it cannot represent exactly: an instance
with a magnification, a non-orthogonal rotation, or an array form the other
side cannot express is REPORTED in ``unsupported``, never silently dropped
or approximated.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .client import LEditBridgeClient, LEditBridgeError

_DRAW_CHUNK_BYTES = 40 * 1024      # one draw op must fit a batch request
_ORTHOGONAL = (0, 90, 180, 270)


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def _chunk_items(items: List[dict], budget: int = _DRAW_CHUNK_BYTES
                 ) -> List[List[dict]]:
    """Split draw items so no single batch op can exceed the request cap."""
    out: List[List[dict]] = []
    batch: List[dict] = []
    used = 0
    for item in items:
        cost = len(json.dumps(item)) + 1
        if batch and used + cost > budget:
            out.append(batch)
            batch, used = [], 0
        batch.append(item)
        used += cost
    if batch:
        out.append(batch)
    return out


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _points_ok(pts, minimum: int) -> bool:
    return (isinstance(pts, list) and len(pts) >= minimum and
            all(isinstance(p, (list, tuple)) and len(p) == 2 and
                _is_num(p[0]) and _is_num(p[1]) for p in pts))


def validate_draw_items(cell: str, items: List[dict]) -> None:
    """Apply the macro's own draw rules BEFORE anything is sent.

    A batch is not atomic on the L-Edit side: the macro stops at the first
    bad item and everything before it has already been written. Checking
    the same rules here (layer present; box = 4 numbers with x0 < x1 and
    y0 < y1; polygon >= 3 points; wire >= 2 points and width >= 0;
    circle radius > 0) means a malformed tree costs nothing at all.
    """
    for i, it in enumerate(items):
        where = "cell '%s' items[%d]" % (cell, i)
        kind = it.get("kind")
        if not it.get("layer"):
            raise LEditBridgeError(
                "%s: layer is required" % where,
                "every draw item needs a layer name", "ERR_INVALID_ITEM")
        if kind == "box":
            bb = it.get("bbox_um")
            if (not isinstance(bb, list) or len(bb) != 4 or
                    not all(_is_num(v) for v in bb) or
                    not (bb[0] < bb[2] and bb[1] < bb[3])):
                raise LEditBridgeError(
                    "%s: box needs bbox_um:[x0,y0,x1,y1] with x0<x1, y0<y1"
                    % where, "fix the source shape (a zero-area box cannot "
                    "be drawn in L-Edit)", "ERR_INVALID_ITEM")
        elif kind == "polygon":
            if not _points_ok(it.get("points_um"), 3):
                raise LEditBridgeError(
                    "%s: polygon needs >= 3 points" % where,
                    "fix the source shape", "ERR_INVALID_ITEM")
        elif kind == "wire":
            if not _points_ok(it.get("points_um"), 2):
                raise LEditBridgeError(
                    "%s: wire needs >= 2 points" % where,
                    "fix the source path", "ERR_INVALID_ITEM")
            w = it.get("width_um", 0)
            if not _is_num(w) or w < 0:
                raise LEditBridgeError(
                    "%s: wire needs width_um >= 0" % where,
                    "fix the source path width", "ERR_INVALID_ITEM")
        elif kind == "circle":
            r = it.get("radius_um", 0)
            if not _is_num(r) or r <= 0:
                raise LEditBridgeError(
                    "%s: circle needs radius_um > 0" % where,
                    "fix the source shape", "ERR_INVALID_ITEM")
        else:
            raise LEditBridgeError(
                "%s: unknown kind %r" % (where, kind),
                "supported kinds: box, polygon, wire, circle",
                "ERR_INVALID_ITEM")


def _orientation(rotation_deg: float, mirror: bool) -> Optional[int]:
    """KLayout rotation -> the L-Edit orient value, or None if not expressible."""
    rot = int(round(rotation_deg)) % 360
    if rot not in _ORTHOGONAL:
        return None
    return rot


def collect_klayout_tree(client, cell: str) -> Tuple[List[str], Dict[str, list]]:
    """Post-order cell list (children before parents) + each cell's instances.

    Walks instance.query itself instead of cell.tree: the transforms are
    needed anyway, and one traversal cannot disagree with itself.
    """
    order: List[str] = []
    seen: Dict[str, list] = {}
    stack = [(cell, False)]
    while stack:
        name, expanded = stack.pop()
        if expanded:
            if name not in order:
                order.append(name)
            continue
        if name in seen:
            continue
        instances = client.instance_query(name).get("instances", [])
        seen[name] = instances
        stack.append((name, True))
        for inst in instances:
            child = inst.get("child")
            if child and child not in seen:
                stack.append((child, False))
    return order, seen


# ---------------------------------------------------------------------------
# KLayout -> L-Edit
# ---------------------------------------------------------------------------

def push_cell_tree(client, bridge: LEditBridgeClient, cell: str, *,
                   expect_file: str = "", clear: bool = True,
                   timeout: float = 120.0) -> Dict[str, Any]:
    """Push ``cell`` and every cell below it into the ACTIVE L-Edit design.

    Cells are created and filled children-first so every ``place_instance``
    finds its master. Re-running is idempotent when ``clear`` is set, which
    matters because L-Edit's draw only appends.
    """
    dbu = float(client.call("layout.info").get("dbu", 0.001))
    by_index = {e["layer_index"]: e
                for e in client.call("layer.list")["layers"]}

    order, instances_of = collect_klayout_tree(client, cell)
    guard: Dict[str, Any] = {"expect_file": expect_file} if expect_file else {}

    layers_used: Dict[str, Tuple[int, int]] = {}
    per_cell: Dict[str, dict] = {}
    unsupported: List[dict] = []

    for name in order:
        q = client.shape_query(name)
        if q.get("truncated"):
            raise LEditBridgeError(
                f"shape.query truncated for cell '{name}'",
                "the cell has more shapes than one query returns; move this "
                "design with import_gds instead of the RPC path")
        items: List[dict] = []
        skipped: Dict[str, int] = {}
        for s in q.get("shapes", []):
            entry = by_index.get(s.get("layer_index"), {})
            lname = entry.get("name") or "L%dD%d" % (entry.get("layer", 0),
                                                     entry.get("datatype", 0))
            layers_used[lname] = (int(entry.get("layer", 0)),
                                  int(entry.get("datatype", 0)))
            kind = s.get("type")
            if kind == "box" and s.get("bbox_dbu"):
                items.append({"kind": "box", "layer": lname,
                              "bbox_um": [v * dbu for v in s["bbox_dbu"]]})
            elif kind == "path" and s.get("points_dbu"):
                items.append({"kind": "wire", "layer": lname,
                              "points_um": [[x * dbu, y * dbu]
                                            for x, y in s["points_dbu"]],
                              "width_um": s.get("width_dbu", 0) * dbu})
            elif kind == "polygon" and s.get("points_dbu"):
                items.append({"kind": "polygon", "layer": lname,
                              "points_um": [[x * dbu, y * dbu]
                                            for x, y in s["points_dbu"]]})
            else:
                skipped[kind or "?"] = skipped.get(kind or "?", 0) + 1

        placements: List[dict] = []
        for inst in instances_of.get(name, []):
            trans = inst.get("trans") or {}
            mag = float(trans.get("magnification", 1.0) or 1.0)
            orient = _orientation(float(trans.get("rotation_deg", 0.0) or 0.0),
                                  bool(trans.get("mirror")))
            if abs(mag - 1.0) > 1e-9 or orient is None:
                unsupported.append({
                    "cell": name, "child": inst.get("child"),
                    "reason": ("magnification %g" % mag
                               if abs(mag - 1.0) > 1e-9
                               else "rotation %s is not orthogonal"
                                    % trans.get("rotation_deg")),
                    "fix": "L-Edit placement here takes orthogonal "
                           "orientation at magnification 1; flatten this "
                           "instance or move the design with import_gds"})
                continue
            place = dict(guard, cell=name, child=inst.get("child"),
                         x_um=float(trans.get("dx_dbu", 0)) * dbu,
                         y_um=float(trans.get("dy_dbu", 0)) * dbu,
                         orient=orient,
                         mirror_x=bool(trans.get("mirror")))
            array = inst.get("array")
            if array:
                na = int(array.get("na", 1) or 1)
                nb = int(array.get("nb", 1) or 1)
                # only a rectangular, axis-aligned array maps onto nx/ny
                a = array.get("a_dbu") or [0, 0]
                b = array.get("b_dbu") or [0, 0]
                if a[1] or b[0]:
                    unsupported.append({
                        "cell": name, "child": inst.get("child"),
                        "reason": "skewed array (a=%s, b=%s)" % (a, b),
                        "fix": "L-Edit arrays are nx/ny on the axes; flatten "
                               "this array or use import_gds"})
                    continue
                ax, by = float(a[0]), float(b[1])
                if (na > 1 and ax == 0.0) or (nb > 1 and by == 0.0):
                    unsupported.append({
                        "cell": name, "child": inst.get("child"),
                        "reason": "array repeats with a zero step "
                                  "(a=%s, b=%s, na=%d, nb=%d)" % (a, b, na, nb),
                        "fix": "the copies would sit on top of each other; "
                               "L-Edit requires a nonzero pitch per repeated "
                               "axis. Fix the source array or flatten it"})
                    continue
                # A KLayout array may step in -x/-y; L-Edit's nx/ny grow in
                # +x/+y only. Taking abs() of the pitch would silently mirror
                # the array about its origin, so move the ORIGIN to the far
                # corner instead and keep the pitch positive -- same
                # instances, same places.
                x0, y0 = place["x_um"], place["y_um"]
                if ax < 0:
                    x0 += ax * (na - 1) * dbu
                if by < 0:
                    y0 += by * (nb - 1) * dbu
                place.update(x_um=x0, y_um=y0, nx=na, ny=nb,
                             dx_um=abs(ax) * dbu, dy_um=abs(by) * dbu)
            placements.append(place)

        per_cell[name] = {"items": items, "skipped": skipped,
                          "placements": placements}

    # Validate EVERYTHING before the first byte goes over: a bad item in
    # the last cell must not cost the user the cells before it.
    for name in order:
        validate_draw_items(name, per_cell[name]["items"])

    ops: List[tuple] = []
    for lname, (gl, gd) in sorted(layers_used.items()):
        ops.append(("ensure_layer", dict(guard, name=lname,
                                         gds_layer=gl, gds_datatype=gd)))
    for name in order:                       # children first
        info = per_cell[name]
        ops.append(("create_cell", dict(guard, name=name)))
        if clear:
            ops.append(("clear_cell", dict(guard, cell=name)))
        for chunk in _chunk_items(info["items"]):
            ops.append(("draw", dict(guard, cell=name, items=chunk)))
        for place in info["placements"]:
            ops.append(("place_instance", place))

    # What exists BEFORE this call decides what a failure may roll back:
    # cells this call creates are ours to delete; cells that were already
    # there are the user's, and the honest thing to do with one that was
    # cleared and only half redrawn is to SAY so, not to delete it.
    caps = bridge.ping().get("capabilities") or []
    existing = {e["name"] for e in bridge.list_cells()}
    try:
        results = bridge.batch(ops, timeout=timeout)
    except LEditBridgeError as exc:
        _rollback_failed_push(bridge, guard, caps, existing, order,
                              per_cell, exc)
        raise

    # Width-0 KLayout paths (klink marker outlines) go over as zero-width
    # L-Edit wires. The macro counts the ones L-Edit accepted per draw;
    # surface the total so the report SAYS it instead of hiding a
    # hairline-vs-outline difference inside the shape counts.
    zero_width = sum(int((r or {}).get("zero_width_wires", 0) or 0)
                     for r in results)

    return {
        "cells": order,
        "cell_count": len(order),
        "shapes": {n: len(per_cell[n]["items"]) for n in order},
        "instances": {n: len(per_cell[n]["placements"]) for n in order},
        "layers": {n: list(p) for n, p in sorted(layers_used.items())},
        "skipped_shape_types": {n: per_cell[n]["skipped"] for n in order
                                if per_cell[n]["skipped"]},
        "unsupported_instances": unsupported,
        "zero_width_wires": zero_width,
        "requests": len(results) and 1 or 0,
        "ops": len(ops),
    }


def _rollback_failed_push(bridge: LEditBridgeClient, guard: dict, caps,
                          existing, order, per_cell,
                          exc: LEditBridgeError) -> None:
    """After a failed batch: delete the cells this push CREATED, report the
    pre-existing ones whose content no longer matches what was meant to be
    there, and fold both lists into ``exc`` (message + attributes)."""
    now = {e["name"] for e in bridge.list_cells()}
    created = [n for n in order if n not in existing and n in now]
    rolled_back: List[str] = []
    left_behind: List[str] = []
    if "delete_cell" in caps:
        # parents first: a child still instanced by a created parent would
        # be refused (force bypasses that anyway, but the order keeps the
        # instance count honest)
        for name in reversed(created):
            try:
                bridge.call("delete_cell", dict(guard, cell=name, force=True))
                rolled_back.append(name)
            except LEditBridgeError:
                left_behind.append(name)
    else:
        left_behind = list(created)

    clobbered: List[str] = []
    for name in order:
        if name not in existing:
            continue
        expected = len(per_cell[name]["items"]) + \
            len(per_cell[name]["placements"])
        try:
            count = int(bridge.get_cell(name).get("count", -1))
        except LEditBridgeError:
            count = -1
        if count != expected:
            clobbered.append(name)

    exc.rolled_back = rolled_back          # type: ignore[attr-defined]
    exc.clobbered = clobbered              # type: ignore[attr-defined]
    exc.left_behind = left_behind          # type: ignore[attr-defined]
    note = " | rolled_back (created by this call, now deleted): %s" % (
        rolled_back or "none")
    if left_behind:
        note += " | LEFT BEHIND (no delete_cell in macro %s, or delete " \
                "failed): %s" % ("< 0.5.7" if "delete_cell" not in caps
                                 else "", left_behind)
    if clobbered:
        note += " | CLOBBERED (existed before, cleared and not fully " \
                "redrawn -- their old content is gone): %s" % clobbered
    exc.args = (exc.args[0] + note,) + tuple(exc.args[1:])


# ---------------------------------------------------------------------------
# L-Edit -> KLayout
# ---------------------------------------------------------------------------

def collect_ledit_tree(bridge: LEditBridgeClient, cell: str
                       ) -> Tuple[List[str], Dict[str, dict]]:
    """Post-order cell list + each cell's get_cell payload, L-Edit side."""
    order: List[str] = []
    seen: Dict[str, dict] = {}
    stack = [(cell, False)]
    while stack:
        name, expanded = stack.pop()
        if expanded:
            if name not in order:
                order.append(name)
            continue
        if name in seen:
            continue
        got = bridge.get_cell(name)
        seen[name] = got
        stack.append((name, True))
        for obj in got.get("objects", []):
            if obj.get("kind") != "instance":
                continue
            child = obj.get("cell")
            if child and child not in seen:
                stack.append((child, False))
    return order, seen


def import_cell_tree(client, bridge: LEditBridgeClient, cell: str, *,
                     layer_of=None) -> Dict[str, Any]:
    """Rebuild an L-Edit cell AND its hierarchy as real KLayout cells.

    ``import_selection`` drops instances by design ("harvest the child cell
    instead"), which makes a hierarchical design unreachable from KLayout
    except by flattening. This keeps the instances.

    ``layer_of`` is a CALLABLE taking an L-Edit layer NAME and returning a
    ``(layer, datatype)`` pair -- not the mapping itself. Passing the dict
    from ``build_layer_map()`` directly raises ``'dict' object is not
    callable``; pass ``mapping.get`` or leave it None to build one from the
    design's own table so the numbers stay the design's.
    """
    if layer_of is not None and not callable(layer_of):
        raise TypeError(
            "layer_of must be CALLABLE (name -> (layer, datatype)), got %s. "
            "Pass mapping.get rather than the mapping, or omit it to build "
            "one from the design's own layer table."
            % type(layer_of).__name__)
    from .adapter import build_layer_map, selection_to_items

    if layer_of is None:
        mapping, _auto = build_layer_map(bridge.get_layers())
        pair_to_name = {pair: n for n, pair in mapping.items()}

        def layer_of(name):                  # noqa: E306 - local default
            return mapping.get(name, (999, 99))
    else:
        pair_to_name = {}

    order, payloads = collect_ledit_tree(bridge, cell)
    report: Dict[str, Any] = {"cells": order, "shapes": {}, "instances": {},
                              "not_convertible": {}}

    for name in order:                       # children first
        got = payloads[name]
        objects = got.get("objects", [])
        shape_objs = [o for o in objects if o.get("kind") != "instance"]
        inst_objs = [o for o in objects if o.get("kind") == "instance"]

        shapes, pcells, failures = selection_to_items(shape_objs, layer_of)
        try:
            client.cell_delete(name)         # fresh, so re-import is idempotent
        except Exception:
            pass
        target = client.cell_create(name)["name"]
        pairs = {(i["layer"], i["datatype"]) for i in shapes} | {
            (p["params"]["layer"]["layer"],
             p["params"]["layer"]["datatype"]) for p in pcells}
        for gl, gd in sorted(pairs):
            client.call("layer.ensure", {
                "layer": gl, "datatype": gd,
                "name": pair_to_name.get((gl, gd), "")})
        if shapes:
            client.shape_insert_many(target, shapes)
        if pcells:
            client.instance_insert_pcell_many(target, pcells)

        placements = []
        for obj in inst_objs:
            repeat = obj.get("repeat") or [1, 1]
            delta = obj.get("delta_um") or [0, 0]
            one = {"child": obj.get("cell"),
                   "position_um": [obj.get("x_um", 0), obj.get("y_um", 0)],
                   "rotation": int(obj.get("orient", 0) or 0) % 360}
            if int(repeat[0]) > 1 or int(repeat[1]) > 1:
                # array fields live in a nested "array" object; at top level
                # they are accepted and silently ignored, which would turn an
                # nx*ny array into a single instance
                one["array"] = {"na": int(repeat[0]), "nb": int(repeat[1]),
                                "a_um": [float(delta[0]), 0.0],
                                "b_um": [0.0, float(delta[1])]}
            placements.append(one)
        if placements:
            client.instance_insert_many(target, placements)

        report["shapes"][name] = len(shapes) + len(pcells)
        report["instances"][name] = len(placements)
        if failures:
            report["not_convertible"][name] = failures

    report["cell_count"] = len(order)
    return report

"""Harvest optical ports from PDK blackbox instances by stub convention.

Many foundry PDK blackbox cells mark their optical ports as small waveguide
stub boxes on the waveguide layer (one common foundry convention: 0.5 x 0.5 um
boxes on 1/0 at the cell boundary). This module turns that convention into
klink Ports that are *derived data*: harvested from live instance positions
at route time, so users can freely drag instances in the GUI and re-route.

Identity rule: a port name is `{prefix}_{index}` where `prefix` is the
instance's stamped `klink_id` user property (written by instance.insert*
at import time; survives save/reload and GUI drags) and `index` numbers
the stubs of the child sorted by child-local (x, y). Instances without a
stamped id (placed by older klink) fall back to `{tag}{ordinal}` counted
in instance.query order — that order is KLayout's internal iteration
order and is NOT stable across save/reload, so the fallback warns.
"""

from __future__ import annotations

import math
import warnings
from typing import Any, Sequence

# No process constants here: the waveguide layer and stub size are PDK-specific
# (e.g. one common foundry convention is 0.5 x 0.5 um stubs on 1/0). Callers pass
# them explicitly from their own pdk.py — klink ships no default.
_EPS_DBU = 2


def _layer_index_for(client, layer: str) -> int | None:
    for entry in client.layer_list().get("layers", []):
        if "%s/%s" % (entry.get("layer"), entry.get("datatype")) == layer:
            return entry.get("layer_index")
    return None


def stub_template(
    client,
    child_cell: str,
    *,
    wg_layer: str,
    stub_size_um: float,
    dbu: float,
) -> list[dict[str, Any]]:
    """Return stub ports of one blackbox cell in child-local dbu coords.

    A stub is a box on `wg_layer` whose width and height both equal
    `stub_size_um` (within 2 dbu); larger boxes (e.g. edge-coupler facets)
    are ignored. Orientation points outward: from the cell bbox center
    toward the stub, snapped to the nearest axis.
    """
    wg_index = _layer_index_for(client, wg_layer)
    if wg_index is None:
        return []
    result = client.call("shape.query", {"cell": child_cell, "limit": 5000})
    shapes = result.get("shapes", [])
    size_dbu = int(round(stub_size_um / dbu))

    xs: list[float] = []
    ys: list[float] = []
    for shape in shapes:
        bbox = shape.get("bbox_dbu")
        if bbox:
            xs.extend((bbox[0], bbox[2]))
            ys.extend((bbox[1], bbox[3]))
    if not xs:
        return []
    center = ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)

    stubs = []
    for shape in shapes:
        if shape.get("layer_index") != wg_index or shape.get("type") != "box":
            continue
        x0, y0, x1, y1 = shape["bbox_dbu"]
        if abs((x1 - x0) - size_dbu) > _EPS_DBU or abs((y1 - y0) - size_dbu) > _EPS_DBU:
            continue
        cx = (x0 + x1) / 2.0
        cy = (y0 + y1) / 2.0
        dx = cx - center[0]
        dy = cy - center[1]
        if abs(dx) >= abs(dy):
            orientation = 0.0 if dx >= 0 else 180.0
        else:
            orientation = 90.0 if dy >= 0 else 270.0
        # Port anchor sits on the stub's outer face, where the route starts.
        face = {
            0.0: (x1, cy),
            180.0: (x0, cy),
            90.0: (cx, y1),
            270.0: (cx, y0),
        }[orientation]
        stubs.append({
            "center_dbu": [face[0], face[1]],
            "orientation": orientation,
            "width_um": stub_size_um,
        })
    stubs.sort(key=lambda s: (s["center_dbu"][0], s["center_dbu"][1]))
    return stubs


def _apply_trans(point: Sequence[float], trans: dict, dbu: float) -> list[float]:
    """Child-local dbu point -> parent um coords through an instance trans."""
    x, y = float(point[0]), float(point[1])
    mag = float(trans.get("magnification", 1.0) or 1.0)
    x *= mag
    y *= mag
    if trans.get("mirror"):
        y = -y
    angle = math.radians(float(trans.get("rotation_deg", 0.0) or 0.0))
    xr = x * math.cos(angle) - y * math.sin(angle)
    yr = x * math.sin(angle) + y * math.cos(angle)
    return [
        (xr + float(trans.get("dx_dbu", 0))) * dbu,
        (yr + float(trans.get("dy_dbu", 0))) * dbu,
    ]


def _apply_trans_angle(orientation: float, trans: dict) -> float:
    angle = -orientation if trans.get("mirror") else orientation
    return (angle + float(trans.get("rotation_deg", 0.0) or 0.0)) % 360.0


def assign_stable_prefixes(
    instances: Sequence[dict],
    tags: dict[str, str],
) -> list[tuple[dict, str]]:
    """Assign a stable identity prefix to each tagged instance dict.

    Stamped ``klink_id`` user properties win — instance.query returns them
    and they survive save/reload and GUI drags. Instances without one
    (placed by older klink) fall back to ``{tag}{ordinal}`` counted in
    query order, skipping any ordinal already taken by a stamped id; that
    order can change when the layout is saved and reloaded, so the
    fallback emits a RuntimeWarning.

    Returns ``(inst, prefix)`` pairs in query order. Raises ValueError on
    a duplicate stamped id (typically a GUI copy of an imported instance).
    """
    tagged = [inst for inst in instances
              if str(inst.get("child") or "") in tags]
    stamped: set[str] = set()
    for inst in tagged:
        kid = inst.get("klink_id")
        if kid:
            if kid in stamped:
                raise ValueError(
                    "duplicate klink_id %r on instances of %r — likely a "
                    "GUI copy of an imported instance; delete the copy, or "
                    "re-run the import (it rebuilds the cell with unique "
                    "ids)" % (kid, inst.get("child")))
            stamped.add(str(kid))
    counters: dict[str, int] = {}
    out: list[tuple[dict, str]] = []
    legacy = 0
    for inst in tagged:
        kid = inst.get("klink_id")
        if kid:
            out.append((inst, str(kid)))
            continue
        tag = tags[str(inst.get("child") or "")]
        ordinal = counters.get(tag, 0)
        while "%s%d" % (tag, ordinal) in stamped:
            ordinal += 1
        counters[tag] = ordinal + 1
        out.append((inst, "%s%d" % (tag, ordinal)))
        legacy += 1
    if legacy:
        warnings.warn(
            "%d instance(s) carry no stamped klink_id; ordinal assignment "
            "falls back to instance.query order, which may change across "
            "save/reload — re-run the import to stamp stable ids"
            % legacy, RuntimeWarning, stacklevel=2)
    return out


def harvest_instance_ports(
    client,
    parent_cell: str,
    *,
    tags: dict[str, str],
    wg_layer: str,
    stub_size_um: float,
    nets: dict[str, str] | None = None,
    port_layer: str = "999/99",
) -> list[dict[str, Any]]:
    """Build port.mark param dicts for all tagged blackbox instances.

    `tags` maps child cell name -> short tag (children not listed are
    skipped). `nets` maps harvested port name -> net string; unlisted ports
    get an empty net (routable later or candidate for assignment).
    Returns the list of port.mark params (not yet sent).
    """
    dbu = float(client.layout_info().get("dbu", 0.001))
    templates: dict[str, list[dict]] = {}
    marks: list[dict[str, Any]] = []
    nets = nets or {}

    result = client.call("instance.query", {"parent": parent_cell, "limit": 5000})
    for inst, prefix in assign_stable_prefixes(result.get("instances", []), tags):
        child = str(inst.get("child") or "")
        if child not in templates:
            templates[child] = stub_template(
                client, child, wg_layer=wg_layer, stub_size_um=stub_size_um, dbu=dbu
            )
        trans = inst.get("trans") or {}
        for index, stub in enumerate(templates[child]):
            name = f"{prefix}_{index}"
            center_um = _apply_trans(stub["center_dbu"], trans, dbu)
            marks.append({
                "cell": parent_cell,
                "layer": port_layer,
                "name": name,
                "label": name,
                "center_um": [round(center_um[0], 4), round(center_um[1], 4)],
                "orientation": _apply_trans_angle(stub["orientation"], trans),
                "width_um": stub["width_um"],
                "port_type": "optical",
                "net": nets.get(name, ""),
                "target_layer": wg_layer,
                "access_mode": "point",
                "slide_allowed": False,
                "slide_edge": "",
                "show_label": True,
            })
    return marks


def mark_ports(client, marks: list[dict[str, Any]]) -> int:
    for params in marks:
        client.call("port.mark", params)
    return len(marks)

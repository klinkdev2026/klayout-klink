"""Generic L-Edit -> KLayout conversion. MECHANISM ONLY.

Selection-driven capability matching — nothing is name-matched or
device-specific, and no object is silently dropped:

1. kind "box"    + bbox_um             -> KLayout box item
2. kind "wire"   + points_um+width_um  -> KLayout path item
3. kind "circle" + center_um+radius_um -> Basic.CIRCLE PCell (parametric)
4. ANY object with a points_um outline -> KLayout polygon (fallback)
5. no usable geometry                  -> reported failure entry

Layer identity comes from L-Edit's own GDS table (``get_layers``); layers
without a GDS number get auto-assigned free numbers, reported to the
caller. Special/system layers are excluded from mapping.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Sequence, Tuple

LayerOf = Callable[[str], Tuple[int, int]]

#: fallback for layer names simply absent from the table (klink debug layer)
UNKNOWN_LAYER = (999, 99)


def build_layer_map(layer_table: Sequence[Dict[str, Any]]
                    ) -> Tuple[Dict[str, Tuple[int, int]], Dict[str, int]]:
    """L-Edit layer name -> (gds_layer, gds_datatype).

    Special layers are skipped; unmapped mask layers (gds < 0) get free
    numbers above the used range. Returns (mapping, auto_assigned)."""
    mapping: Dict[str, Tuple[int, int]] = {}
    auto: Dict[str, int] = {}
    rows = [e for e in layer_table if not e.get("special")]
    used = {e["gds_layer"] for e in rows if e.get("gds_layer", -1) >= 0}
    next_free = (max(used) + 1) if used else 1
    for e in sorted(rows, key=lambda x: str(x.get("name", ""))):
        name = str(e.get("name", ""))
        gl = int(e.get("gds_layer", -1))
        gd = max(int(e.get("gds_datatype", 0)), 0)
        if gl < 0:
            gl, gd = next_free, 0
            while gl in used:
                gl += 1
            used.add(gl)
            next_free = gl + 1
            auto[name] = gl
        mapping[name] = (gl, gd)
    return mapping, auto


def convert_object(obj: Dict[str, Any], layer_of: LayerOf
                   ) -> Tuple[str, Any]:
    """One bridge object -> ('shape', item) | ('pcell', item) |
    ('fail', reason). Pure function; capability-matched, no hardcoding."""
    gl, gd = layer_of(str(obj.get("layer", "")))
    kind = str(obj.get("kind", "?"))
    pts = obj.get("points_um")
    if kind == "box" and "bbox_um" in obj:
        return "shape", {"kind": "box", "layer": gl, "datatype": gd,
                         "bbox_um": obj["bbox_um"]}
    if kind == "wire" and pts and obj.get("width_um"):
        return "shape", {"kind": "path", "layer": gl, "datatype": gd,
                         "points_um": pts, "width_um": obj["width_um"]}
    if kind == "circle" and "center_um" in obj and obj.get("radius_um"):
        r = float(obj["radius_um"])
        return "pcell", {
            "pcell": "CIRCLE", "library": "Basic",
            "params": {"layer": {"layer": gl, "datatype": gd},
                       "radius": r, "actual_radius": r,
                       "handle": {"point_um": [-r, 0]}, "npoints": 32},
            "position_um": obj["center_um"]}
    if pts and len(pts) >= 3:   # generic outline fallback (torus, pie, ...)
        return "shape", {"kind": "polygon", "layer": gl, "datatype": gd,
                         "points_um": pts}
    return "fail", (f"kind={kind} has no usable geometry "
                    f"(keys: {sorted(obj)})")


def selection_to_items(objects: Sequence[Dict[str, Any]], layer_of: LayerOf
                       ) -> Tuple[List[dict], List[dict], List[str]]:
    """Split converted objects into (shapes, pcells, failures)."""
    shapes: List[dict] = []
    pcells: List[dict] = []
    failures: List[str] = []
    for obj in objects:
        route, payload = convert_object(obj, layer_of)
        if route == "shape":
            shapes.append(payload)
        elif route == "pcell":
            pcells.append(payload)
        else:
            failures.append(payload)
    return shapes, pcells, failures


def nest_properties(flat: Dict[str, Any]) -> Dict[str, Any]:
    """L-Edit reports property trees as FLAT dotted names ("A.B.C").
    Rebuild the nesting: group nodes become dicts; a group that also
    carries its own value keeps it under the "" key.

    {"System": "<x>", "System.Hide In Lists": True}
      -> {"System": {"": "<x>", "Hide In Lists": True}}
    """
    out: Dict[str, Any] = {}
    for key in sorted(flat):
        parts = key.split(".")
        node = out
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {"": child} if part in node else {}
                node[part] = child
            node = child
        leaf = parts[-1]
        if isinstance(node.get(leaf), dict):
            node[leaf][""] = flat[key]
        else:
            node[leaf] = flat[key]
    return out


def merge_layer_name(existing: str, incoming: str, sep: str = "|") -> str:
    """Name policy for layers that already exist on the KLayout side:

    - existing empty          -> take the incoming name (pure gain)
    - same / already contains -> keep as is (idempotent)
    - different               -> append ``existing|incoming`` (owner ruling:
      never overwrite a user's own name, never lose the source name)
    """
    existing = (existing or "").strip()
    incoming = (incoming or "").strip()
    if not incoming:
        return existing
    if not existing:
        return incoming
    if incoming in existing.split(sep):
        return existing
    return f"{existing}{sep}{incoming}"


def harvest_boxes(get_cell_result: Dict[str, Any], scale: int = 1000
                  ) -> Dict[str, List[List[int]]]:
    """get_cell result -> {layer_name: sorted integer boxes} for byte-exact
    comparison (default scale 1000 = um -> nm). Non-box outlines use their
    bbox; instances are ignored (harvest the child cell instead)."""
    out: Dict[str, List[List[int]]] = {}
    for o in get_cell_result.get("objects", []):
        if o.get("kind") == "instance":
            continue
        lname = str(o.get("layer", "?"))
        if o.get("kind") == "box" and "bbox_um" in o:
            b = [int(round(v * scale)) for v in o["bbox_um"]]
        else:
            pts = o.get("points_um") or []
            if not pts:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            b = [int(round(min(xs) * scale)), int(round(min(ys) * scale)),
                 int(round(max(xs) * scale)), int(round(max(ys) * scale))]
        out.setdefault(lname, []).append(b)
    for boxes in out.values():
        boxes.sort()
    return out

"""Writefield boundary patch generation.

Ported from Klayout-Router (MIT, Legendrexial), rewritten for klink.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .._deps import load_kdb
from .writefield import WritefieldPlan

BBox = list[float]


def _bbox_intersection(a: Sequence[float], b: Sequence[float]) -> BBox | None:
    x0 = max(float(a[0]), float(b[0]))
    y0 = max(float(a[1]), float(b[1]))
    x1 = min(float(a[2]), float(b[2]))
    y1 = min(float(a[3]), float(b[3]))
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _patch_crosses_other_boundary(patch: Sequence[float], boundaries: Sequence[dict], intended: dict) -> bool:
    for boundary in boundaries:
        if boundary is intended:
            continue
        axis = boundary["axis"]
        at = float(boundary["at_um"])
        if axis == "x" and float(patch[0]) < at < float(patch[2]):
            return True
        if axis == "y" and float(patch[1]) < at < float(patch[3]):
            return True
    return False


def generate_wf_patches(
    electrode_boxes_um: str | Path | Sequence[Sequence[float]],
    wf_plan: WritefieldPlan | dict,
    *,
    patch_size_um: float = 5.0,
    patch_layer: str = "113/0",
    electrode_layer: str = "10/0",
    top_cell: str | None = None,
) -> dict:
    """Generate square patches where electrodes cross writefield boundaries.

    This pure-geometry API mirrors the useful part of Klayout-Router's
    Auto-patching macro without writing to KLayout.  Callers can write the
    returned boxes with ``shape.insert_boxes`` or convert ``shape_items`` to
    ``shape.insert_many`` payloads.  ``electrode_boxes_um`` may also be a GDS
    or OAS path; in that case klayout.db is loaded lazily and bboxes are read
    from ``electrode_layer``.
    """

    if patch_size_um <= 0:
        raise ValueError("patch_size_um must be positive")
    electrode_boxes = _coerce_electrode_boxes(
        electrode_boxes_um,
        electrode_layer=electrode_layer,
        top_cell=top_cell,
    )
    if isinstance(wf_plan, WritefieldPlan):
        plan = wf_plan.to_dict()
    else:
        plan = wf_plan
    boundaries = list(plan.get("boundary_segments_um") or [])
    half = float(patch_size_um) / 2.0
    patches: list[BBox] = []
    seen: set[tuple[float, float, float, float]] = set()

    for electrode in electrode_boxes:
        for boundary in boundaries:
            axis = boundary["axis"]
            at = float(boundary["at_um"])
            span0, span1 = [float(v) for v in boundary["span_um"]]
            if axis == "x":
                stripe = [at - half, span0, at + half, span1]
            else:
                stripe = [span0, at - half, span1, at + half]
            hit = _bbox_intersection(electrode, stripe)
            if hit is None:
                continue
            cx = (hit[0] + hit[2]) / 2.0
            cy = (hit[1] + hit[3]) / 2.0
            patch = [cx - half, cy - half, cx + half, cy + half]
            if _patch_crosses_other_boundary(patch, boundaries, boundary):
                continue
            key = tuple(round(v, 9) for v in patch)
            if key not in seen:
                seen.add(key)
                patches.append(patch)

    layer, datatype = _parse_layer(patch_layer)
    shape_items = [{"kind": "box", "layer": layer, "datatype": datatype, "bbox_um": box} for box in patches]
    return {
        "patch_boxes_um": patches,
        "shape_items": shape_items,
        "report": {
            "patch_count": len(patches),
            "electrode_count": len(electrode_boxes),
            "boundary_count": len(boundaries),
            "patch_layer": patch_layer,
            "electrode_layer": electrode_layer,
        },
    }


def _coerce_electrode_boxes(
    electrode_source: str | Path | Sequence[Sequence[float]],
    *,
    electrode_layer: str,
    top_cell: str | None,
) -> list[BBox]:
    if isinstance(electrode_source, (str, Path)):
        return _electrode_boxes_from_gds(Path(electrode_source), electrode_layer=electrode_layer, top_cell=top_cell)
    return [[float(v) for v in box] for box in electrode_source]


def _electrode_boxes_from_gds(path: Path, *, electrode_layer: str, top_cell: str | None = None) -> list[BBox]:
    kdb = load_kdb()
    if not path.exists():
        raise FileNotFoundError(path)
    layout = kdb.Layout()
    layout.read(str(path))
    layer, datatype = _parse_layer(electrode_layer)
    layer_index = layout.find_layer(kdb.LayerInfo(layer, datatype))
    if layer_index < 0:
        return []
    cell = layout.cell(top_cell) if top_cell else layout.top_cell()
    if cell is None:
        raise ValueError(f"cell not found in {path}: {top_cell!r}")
    dbu = float(layout.dbu)
    boxes: list[BBox] = []
    iterator = cell.begin_shapes_rec(layer_index)
    for item in iterator:
        box = item.shape().bbox()
        try:
            box = box.transformed(item.trans())
        except Exception:
            pass
        boxes.append([box.left * dbu, box.bottom * dbu, box.right * dbu, box.top * dbu])
    return boxes


def _parse_layer(layer: str) -> tuple[int, int]:
    parts = str(layer).split("/")
    if len(parts) == 1:
        return int(parts[0]), 0
    if len(parts) == 2:
        return int(parts[0]), int(parts[1])
    raise ValueError(f"invalid layer string: {layer!r}")

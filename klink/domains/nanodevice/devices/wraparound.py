"""Reusable wraparound EBL demo generator."""

from __future__ import annotations

import math

from ..ebl.marks import corner_alignment_marks
from ..ebl.patching import generate_wf_patches
from ..ebl.validation import validate_route_centerline_overlaps, validate_writefield_crossings
from ..ebl.writefield import CrossingWindow, plan_writefields


CELL = "NANODEVICE_EBL_WRAPAROUND"
PORT_LAYER = "999/99"
ANCHOR_LAYER = "999/1"
# Process layers are example-owned: build_wraparound_demo(layers) receives them
# (flake/m1/m2/pad/via/label as (L, D) tuples, patch as 'L/D'). klink ships none.
# (999/99 Port + 999/1 Anchor above stay as klink's reserved marker layers.)


def build_wraparound_demo(layers: dict) -> dict:
    """Build a complex explicit EBL wraparound bundle.

    Reusable visual/regression fixture, not a routing backend. Process layers
    are example-owned: pass a ``layers`` mapping (flake/m1/m2/pad/via/label as
    (L, D) tuples, patch as 'L/D'); klink ships none.
    """
    LAYER_FLAKE = layers["flake"]
    LAYER_M1 = layers["m1"]
    LAYER_M2 = layers["m2"]
    LAYER_PAD = layers["pad"]
    LAYER_VIA = layers["via"]
    LAYER_LABEL = layers["label"]
    LAYER_PATCH = layers["patch"]

    def _lstr(t) -> str:
        return f"{t[0]}/{t[1]}"

    chip = [-230.0, -170.0, 230.0, 170.0]
    wf = plan_writefields(
        chip,
        writefield_size_um=115.0,
        origin_um=[0.0, 0.0],
        stitch_margin_um=1.2,
        crossing_windows=[
            CrossingWindow("x", -115.0, 0.0, 72.0, "WF_XL_MID"),
            CrossingWindow("x", 0.0, 110.0, 70.0, "WF_X0_TOP"),
            CrossingWindow("x", 0.0, -110.0, 70.0, "WF_X0_BOT"),
            CrossingWindow("x", 115.0, 0.0, 72.0, "WF_XR_MID"),
            CrossingWindow("y", -115.0, -165.0, 70.0, "WF_YB_LEFT"),
            CrossingWindow("y", -115.0, 0.0, 70.0, "WF_YB_MID"),
            CrossingWindow("y", -115.0, 165.0, 70.0, "WF_YB_RIGHT"),
            CrossingWindow("y", 0.0, 0.0, 390.0, "WF_Y0_WIDE"),
            CrossingWindow("y", 115.0, -165.0, 70.0, "WF_YT_LEFT"),
            CrossingWindow("y", 115.0, 0.0, 70.0, "WF_YT_MID"),
            CrossingWindow("y", 115.0, 165.0, 70.0, "WF_YT_RIGHT"),
        ],
    )

    items: list[dict] = []
    items.extend(corner_alignment_marks(chip, inset_um=18.0, arm_length_um=18.0, arm_width_um=2.0, layer=_lstr(LAYER_LABEL)))
    items.append(_poly(LAYER_FLAKE, _flake_polygon()))
    items.append(_box(LAYER_LABEL, [-54, -44, 54, 44]))
    items.append(_text("flake + local keepout", [-52, 48], 4.0, layer=LAYER_LABEL))

    contacts = [
        ("C0", "wrap_0", [-42, 18], [-74, 76], [-176, 128], 180.0),
        ("C1", "wrap_1", [-42, -12], [-92, -76], [-176, -128], 180.0),
        ("C2", "wrap_2", [-12, 36], [-42, 92], [-70, 142], 90.0),
        ("C3", "wrap_3", [18, 36], [42, 92], [70, 142], 90.0),
        ("C4", "wrap_4", [44, 14], [92, 76], [176, 128], 0.0),
        ("C5", "wrap_5", [44, -14], [92, -76], [176, -128], 0.0),
        ("C6", "wrap_6", [-12, -36], [-42, -92], [-70, -142], 270.0),
        ("C7", "wrap_7", [18, -36], [42, -92], [70, -142], 270.0),
    ]

    route_boxes: list[list[float]] = []
    route_paths: list[dict] = []
    port_marks: list[dict] = []
    anchor_marks: list[dict] = []
    narrow_w = 3.0
    ring_w = 4.0
    pad_neck_w = 8.0
    pad_w = 34.0
    pad_h = 24.0

    for idx, (name, net, contact, via, pad_center, pad_orientation) in enumerate(contacts):
        contact_box = [contact[0] - 3, contact[1] - 3, contact[0] + 3, contact[1] + 3]
        items.append(_box(LAYER_M1, contact_box))
        m1_points = [contact, via]
        items.append(_path(LAYER_M1, m1_points, narrow_w))
        route_boxes.extend(_segment_boxes(m1_points, narrow_w))
        route_paths.append({"net": net, "layer": _lstr(LAYER_M1), "points_um": m1_points, "width_um": narrow_w})

        items.append(_box(LAYER_VIA, [via[0] - 3, via[1] - 3, via[0] + 3, via[1] + 3]))
        neck = _pad_neck_point(pad_center, pad_orientation, pad_h)
        route_points = _wrap_path(via, neck, idx)
        items.append(_path(LAYER_M2, route_points[:-1], ring_w))
        items.append(_poly(LAYER_M2, _taper_polygon(route_points[-2], route_points[-1], ring_w, pad_neck_w)))
        route_boxes.extend(_segment_boxes(route_points, max(ring_w, pad_neck_w)))
        route_paths.append({"net": net, "layer": _lstr(LAYER_M2), "points_um": route_points, "width_um": ring_w})

        items.append(_box(LAYER_PAD, _pad_bbox(pad_center, pad_orientation, pad_w, pad_h)))
        items.append(_text(name, [pad_center[0] - 7, pad_center[1] - 2], 3.0, layer=LAYER_LABEL))

        port_marks.append(_port(name, net, contact, _contact_orientation(contact), narrow_w, target_layer=_lstr(LAYER_M1)))
        port_marks.append(_port(f"PAD_{name}", net, neck, pad_orientation, pad_neck_w, target_layer=_lstr(LAYER_M2)))
        anchor_marks.append(_anchor(f"ANCH_{name}", net, via))

    patch = generate_wf_patches(route_boxes, wf, patch_size_um=7.0, patch_layer=LAYER_PATCH)
    items.extend(patch["shape_items"])
    for box in wf.obstacle_boxes_um:
        items.append(_box((900, 0), box))

    wf_validation = validate_writefield_crossings(route_paths, wf.to_dict())
    overlap_validation = validate_route_centerline_overlaps(route_paths)
    return {
        "cell": CELL,
        "shape_items": items,
        "port_marks": port_marks,
        "anchor_marks": [*anchor_marks, *wf.corridor_anchor_specs],
        "writefield": wf.to_dict(),
        "route_paths": route_paths,
        "wf_validation": wf_validation,
        "overlap_validation": overlap_validation,
        "patch_report": patch["report"],
        "report": {
            "shape_items": len(items),
            "ports": len(port_marks),
            "anchors": len(anchor_marks) + len(wf.corridor_anchor_specs),
            "patches": patch["report"]["patch_count"],
            "wf_obstacles": len(wf.obstacle_boxes_um),
            "wf_crossings": wf_validation["crossing_count"],
            "wf_crossing_violations": len(wf_validation["violations"]),
            "route_centerline_overlaps": len(overlap_validation["overlaps"]),
        },
    }


def _box(layer: tuple[int, int], bbox: list[float]) -> dict:
    return {"kind": "box", "layer": layer[0], "datatype": layer[1], "bbox_um": bbox}


def _path(layer: tuple[int, int], points: list[list[float]], width: float) -> dict:
    return {
        "kind": "path",
        "layer": layer[0],
        "datatype": layer[1],
        "points_um": points,
        "width_um": width,
        "begin_ext_um": width / 2.0,
        "end_ext_um": width / 2.0,
        "round_ends": False,
    }


def _poly(layer: tuple[int, int], points: list[list[float]]) -> dict:
    return {"kind": "polygon", "layer": layer[0], "datatype": layer[1], "points_um": points}


def _text(text: str, xy: list[float], size: float = 4.0, *, layer: tuple[int, int]) -> dict:
    return {"kind": "text", "layer": layer[0], "datatype": layer[1], "text": text, "position_um": xy, "size_um": size}


def _taper_polygon(a: list[float], b: list[float], width_a: float, width_b: float) -> list[list[float]]:
    dx = float(b[0]) - float(a[0])
    dy = float(b[1]) - float(a[1])
    length = math.hypot(dx, dy)
    if length < 1e-9:
        raise ValueError("taper endpoints must differ")
    nx = -dy / length
    ny = dx / length
    return [
        [a[0] + nx * width_a / 2.0, a[1] + ny * width_a / 2.0],
        [b[0] + nx * width_b / 2.0, b[1] + ny * width_b / 2.0],
        [b[0] - nx * width_b / 2.0, b[1] - ny * width_b / 2.0],
        [a[0] - nx * width_a / 2.0, a[1] - ny * width_a / 2.0],
    ]


def _segment_boxes(points: list[list[float]], width: float) -> list[list[float]]:
    half = float(width) / 2.0
    return [
        [min(a[0], b[0]) - half, min(a[1], b[1]) - half, max(a[0], b[0]) + half, max(a[1], b[1]) + half]
        for a, b in zip(points, points[1:])
    ]


def _flake_polygon() -> list[list[float]]:
    return [[-30, -18], [-12, -28], [18, -24], [34, -8], [26, 18], [4, 30], [-24, 20], [-38, 2]]


def _wrap_path(via: list[float], neck: list[float], idx: int) -> list[list[float]]:
    if idx == 0:
        return [via, [-74, 18], [-115, 18], [-176, 18], neck]
    if idx == 1:
        return [via, [-92, -18], [-115, -18], [-176, -18], neck]
    if idx == 2:
        return [via, [-18, 92], [-18, 115], [-18, 130], neck]
    if idx == 3:
        return [via, [18, 92], [18, 115], [18, 130], neck]
    if idx == 4:
        return [via, [92, 18], [115, 18], [176, 18], neck]
    if idx == 5:
        return [via, [92, -18], [115, -18], [176, -18], neck]
    if idx == 6:
        return [via, [-18, -92], [-18, -115], [-18, -130], neck]
    return [via, [18, -92], [18, -115], [18, -130], neck]


def _pad_neck_point(center: list[float], orientation: float, pad_h: float) -> list[float]:
    if orientation == 90.0:
        return [center[0], center[1] - pad_h / 2.0]
    if orientation == 270.0:
        return [center[0], center[1] + pad_h / 2.0]
    if orientation == 0.0:
        return [center[0] - pad_h / 2.0, center[1]]
    return [center[0] + pad_h / 2.0, center[1]]


def _pad_bbox(center: list[float], orientation: float, pad_w: float, pad_h: float) -> list[float]:
    if orientation in (90.0, 270.0):
        return [center[0] - pad_w / 2.0, center[1] - pad_h / 2.0, center[0] + pad_w / 2.0, center[1] + pad_h / 2.0]
    return [center[0] - pad_h / 2.0, center[1] - pad_w / 2.0, center[0] + pad_h / 2.0, center[1] + pad_w / 2.0]


def _contact_orientation(contact: list[float]) -> float:
    x, y = contact
    if abs(x) > abs(y):
        return 180.0 if x < 0 else 0.0
    return 270.0 if y < 0 else 90.0


def _port(name: str, net: str, center: list[float], orientation: float, width: float, *, target_layer: str = "10/0") -> dict:
    return {
        "layer": PORT_LAYER,
        "name": name,
        "center_um": center,
        "orientation": orientation,
        "width_um": width,
        "port_type": "electrical",
        "net": net,
        "target_layer": target_layer,
        "access_mode": "point",
        "show_label": True,
    }


def _anchor(aid: str, net: str, center: list[float]) -> dict:
    return {
        "layer": ANCHOR_LAYER,
        "id": aid,
        "center_um": center,
        "kind": "waypoint_region",
        "mode": "fixed",
        "net": net,
        "label": "via",
        "show_label": True,
        "required": True,
        "priority": 0,
        "width_um": 8.0,
        "height_um": 8.0,
        "path_points": "",
    }

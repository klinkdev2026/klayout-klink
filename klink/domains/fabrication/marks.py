"""Fabrication alignment mark shape libraries.

The presets here are starting points for internal examples.  Real fabrication
flows often have house-specific alignment and metrology rules; do not treat
these defaults as universal fab acceptance criteria.
"""

from __future__ import annotations

from typing import Literal, Sequence


GLOBAL_MARKS = {"layer": "6/0", "generator": "cross_in_box", "size_um": 200.0, "line_width_um": 8.0, "box_margin_um": 30.0}
CHIP_MARKS = {"layer": "6/0", "generator": "cross", "size_um": 100.0, "line_width_um": 5.0}
FIELD_MARKS = {"layer": "6/0", "generator": "cross", "size_um": 20.0, "line_width_um": 1.0}
CORNER_COMPOSITE_MARKS = {
    "groups": [
        {"layer": "6/0", "generator": "cross_in_box", "size_um": 180.0, "line_width_um": 6.0, "box_margin_um": 20.0},
        {"layer": "7/0", "generator": "l_mark", "arm_um": 120.0, "line_width_um": 5.0},
        {"layer": "8/0", "generator": "vernier_pair", "pitch_a_um": 8.0, "pitch_b_um": 8.5, "count": 7, "line_width_um": 1.0, "axis": "x"},
    ]
}
PRESETS = {"global": GLOBAL_MARKS, "chip": CHIP_MARKS, "field": FIELD_MARKS}
PRESET_GROUPS = {"global": [GLOBAL_MARKS], "chip": [CHIP_MARKS], "field": [FIELD_MARKS], "corner_composite": CORNER_COMPOSITE_MARKS["groups"]}


def cross(size_um: float, line_width_um: float) -> list[dict]:
    _require_positive(size_um=size_um, line_width_um=line_width_um)
    half = float(size_um) / 2.0
    half_w = float(line_width_um) / 2.0
    return [
        _box([-half, -half_w, half, half_w]),
        _box([-half_w, -half, half_w, half]),
    ]


def cross_in_box(size_um: float, line_width_um: float, box_margin_um: float) -> list[dict]:
    _require_positive(size_um=size_um, line_width_um=line_width_um)
    if box_margin_um < 0:
        raise ValueError("box_margin_um must be non-negative")
    outer = float(size_um) + 2.0 * float(box_margin_um)
    half = outer / 2.0
    return [
        *cross(size_um, line_width_um),
        _box([-half, -half, half, half]),
    ]


def l_mark(arm_um: float, line_width_um: float) -> list[dict]:
    _require_positive(arm_um=arm_um, line_width_um=line_width_um)
    half_w = float(line_width_um) / 2.0
    half_arm = float(arm_um) / 2.0
    return [
        _box([-half_arm, -half_w, half_arm, half_w]),
        _box([-half_w, -half_arm, half_w, half_arm]),
    ]


def vernier_pair(
    pitch_a_um: float,
    pitch_b_um: float,
    count: int,
    line_width_um: float,
    axis: Literal["x", "y"] = "x",
) -> list[dict]:
    _require_positive(pitch_a_um=pitch_a_um, pitch_b_um=pitch_b_um, line_width_um=line_width_um)
    if count <= 0:
        raise ValueError("count must be positive")
    if axis not in {"x", "y"}:
        raise ValueError("axis must be 'x' or 'y'")
    items = []
    for row, pitch in enumerate((float(pitch_a_um), float(pitch_b_um))):
        y = (row - 0.5) * float(line_width_um) * 4.0
        start = -((count - 1) * pitch) / 2.0
        for i in range(count):
            c = start + i * pitch
            if axis == "x":
                bbox = [c - line_width_um / 2.0, y - line_width_um / 2.0, c + line_width_um / 2.0, y + line_width_um / 2.0]
            else:
                bbox = [y - line_width_um / 2.0, c - line_width_um / 2.0, y + line_width_um / 2.0, c + line_width_um / 2.0]
            items.append(_box(bbox))
    return items


def box_in_box(outer_um: float, inner_um: float) -> list[dict]:
    _require_positive(outer_um=outer_um, inner_um=inner_um)
    if inner_um >= outer_um:
        raise ValueError("inner_um must be smaller than outer_um")
    return [_box(_centered_square(outer_um)), _box(_centered_square(inner_um))]


def from_preset(preset: str, **overrides) -> list[dict]:
    groups = from_preset_groups(preset, **overrides)
    flattened = []
    for group in groups:
        flattened.extend(group["shape_items"])
    return flattened


def from_preset_groups(preset: str, **overrides) -> list[dict]:
    try:
        groups = [dict(group) for group in PRESET_GROUPS[preset]]
    except KeyError as exc:
        raise ValueError(f"unknown mark preset {preset!r}; supported: {', '.join(sorted(PRESET_GROUPS))}") from exc
    result = []
    for group in groups:
        cfg = dict(group)
        layer = str(overrides.get("layer", cfg.pop("layer", "6/0")))
        cfg.update({k: v for k, v in overrides.items() if k != "layer"})
        gen = cfg.pop("generator")
        result.append({"layer": layer, "shape_items": _generator(gen)(**cfg)})
    return result


def placed_mark_items(items: Sequence[dict], center_um: Sequence[float], layer: str) -> list[dict]:
    layer_num, datatype = parse_layer(layer)
    cx, cy = float(center_um[0]), float(center_um[1])
    placed = []
    for item in items:
        bbox = item["bbox_um"]
        placed.append({
            "kind": "box",
            "layer": layer_num,
            "datatype": datatype,
            "bbox_um": [bbox[0] + cx, bbox[1] + cy, bbox[2] + cx, bbox[3] + cy],
        })
    return placed


def placed_mark_group_items(groups: Sequence[dict], center_um: Sequence[float]) -> list[dict]:
    placed = []
    for group in groups:
        placed.extend(placed_mark_items(group["shape_items"], center_um, str(group["layer"])))
    return placed


def parse_layer(layer: str) -> tuple[int, int]:
    parts = str(layer).split("/")
    if len(parts) == 1:
        return int(parts[0]), 0
    if len(parts) == 2:
        return int(parts[0]), int(parts[1])
    raise ValueError(f"invalid layer string: {layer!r}")


def _box(bbox: Sequence[float]) -> dict:
    return {"kind": "box", "bbox_um": [float(v) for v in bbox]}


def _generator(name: str):
    return {
        "cross": cross,
        "cross_in_box": cross_in_box,
        "l_mark": l_mark,
        "vernier_pair": vernier_pair,
        "box_in_box": box_in_box,
    }[name]


def _centered_square(size_um: float) -> list[float]:
    half = float(size_um) / 2.0
    return [-half, -half, half, half]


def _require_positive(**values: float) -> None:
    for name, value in values.items():
        if float(value) <= 0:
            raise ValueError(f"{name} must be positive")

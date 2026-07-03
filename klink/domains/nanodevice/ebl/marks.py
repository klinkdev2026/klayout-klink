"""Alignment mark shape generators for batch RPC payloads."""

from __future__ import annotations

from typing import Sequence


def _parse_layer(layer: str | Sequence[int]) -> tuple[int, int]:
    if isinstance(layer, str):
        parts = layer.split("/")
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    return int(layer[0]), int(layer[1])


def alignment_cross_items(
    center_um: Sequence[float],
    *,
    arm_length_um: float = 20.0,
    arm_width_um: float = 2.0,
    layer: str | Sequence[int] = "6/0",
    label: str | None = None,
) -> list[dict]:
    """Return two box items forming a cross, plus an optional text label."""

    cx, cy = [float(v) for v in center_um]
    half_l = float(arm_length_um) / 2.0
    half_w = float(arm_width_um) / 2.0
    layer_num, datatype = _parse_layer(layer)
    items = [
        {"kind": "box", "layer": layer_num, "datatype": datatype, "bbox_um": [cx - half_l, cy - half_w, cx + half_l, cy + half_w]},
        {"kind": "box", "layer": layer_num, "datatype": datatype, "bbox_um": [cx - half_w, cy - half_l, cx + half_w, cy + half_l]},
    ]
    if label:
        items.append({
            "kind": "text",
            "layer": layer_num,
            "datatype": datatype,
            "text": label,
            "position_um": [cx + half_l + arm_width_um, cy + half_l + arm_width_um],
            "size_um": max(arm_width_um * 2.0, 1.0),
        })
    return items


def corner_alignment_marks(
    chip_bbox_um: Sequence[float],
    *,
    inset_um: float = 20.0,
    arm_length_um: float = 20.0,
    arm_width_um: float = 2.0,
    layer: str | Sequence[int] = "6/0",
) -> list[dict]:
    """Return corner alignment crosses inside a chip bbox."""

    x0, y0, x1, y1 = [float(v) for v in chip_bbox_um]
    centers = [
        [x0 + inset_um, y0 + inset_um],
        [x1 - inset_um, y0 + inset_um],
        [x1 - inset_um, y1 - inset_um],
        [x0 + inset_um, y1 - inset_um],
    ]
    items: list[dict] = []
    for idx, center in enumerate(centers):
        items.extend(alignment_cross_items(center, arm_length_um=arm_length_um, arm_width_um=arm_width_um, layer=layer, label=f"AM{idx}"))
    return items


def field_alignment_marks(
    field_bboxes_um: Sequence[Sequence[float]],
    *,
    inset_um: float = 10.0,
    arm_length_um: float = 10.0,
    arm_width_um: float = 1.0,
    layer: str | Sequence[int] = "6/0",
) -> list[dict]:
    """Return one cross near the lower-left corner of each writefield."""

    items: list[dict] = []
    for idx, bbox in enumerate(field_bboxes_um):
        x0, y0, _x1, _y1 = [float(v) for v in bbox]
        items.extend(
            alignment_cross_items(
                [x0 + inset_um, y0 + inset_um],
                arm_length_um=arm_length_um,
                arm_width_um=arm_width_um,
                layer=layer,
                label=f"FM{idx}",
            )
        )
    return items

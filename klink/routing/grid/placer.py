"""Pure data-flow-aware column placer."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


class PlacementError(ValueError):
    """Placement input is malformed or impossible under the given pitches."""


def place_columns(
    groups: Sequence[Any],
    device_bbox_um: Mapping[str, Sequence[float]] | Sequence[float],
    *,
    col_pitch_um: float,
    row_pitch_um: float,
    y_top_um: float = 0.0,
) -> dict[str, tuple[float, float]]:
    """Place each group in one column, preserving caller-provided order."""

    col_pitch = _positive(col_pitch_um, "col_pitch_um")
    row_pitch = _positive(row_pitch_um, "row_pitch_um")
    y_top = _number(y_top_um, "y_top_um")
    normalized_groups = [_group_instances(group, index) for index, group in enumerate(groups)]
    if not normalized_groups:
        raise PlacementError("groups must contain at least one column")

    bboxes = _bbox_map(device_bbox_um, normalized_groups)
    max_width = max(_width(bbox) for bbox in bboxes.values())
    max_height = max(_height(bbox) for bbox in bboxes.values())
    if col_pitch < max_width:
        raise PlacementError("col_pitch_um is smaller than the widest device bbox")
    if row_pitch < max_height:
        raise PlacementError("row_pitch_um is smaller than the tallest device bbox")

    placed: dict[str, tuple[float, float]] = {}
    for col_index, instances in enumerate(normalized_groups):
        x = col_index * col_pitch
        for row_index, instance_id in enumerate(instances):
            if instance_id in placed:
                raise PlacementError(f"instance {instance_id!r} appears in more than one placement slot")
            placed[instance_id] = (x, y_top - row_index * row_pitch)
    return placed


def _group_instances(group: Any, index: int) -> tuple[str, ...]:
    if isinstance(group, Mapping):
        raw = group.get("instances")
    else:
        raw = group
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise PlacementError(f"group {index} must be a sequence or an object with instances")
    out = tuple(_nonempty_string(item, f"group {index} instance") for item in raw)
    if not out:
        raise PlacementError(f"group {index} must contain at least one instance")
    return out


def _bbox_map(value: Mapping[str, Sequence[float]] | Sequence[float], groups: Sequence[Sequence[str]]) -> dict[str, tuple[float, float, float, float]]:
    instances = [iid for group in groups for iid in group]
    if isinstance(value, Mapping):
        return {iid: _bbox(value.get(iid), f"device_bbox_um[{iid!r}]") for iid in instances}
    bbox = _bbox(value, "device_bbox_um")
    return {iid: bbox for iid in instances}


def _bbox(value: Any, label: str) -> tuple[float, float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        raise PlacementError(f"{label} must be a 4-item bbox")
    x1, y1, x2, y2 = (_number(item, label) for item in value)
    if x1 >= x2 or y1 >= y2:
        raise PlacementError(f"{label} must satisfy x1<x2 and y1<y2")
    return (x1, y1, x2, y2)


def _width(bbox: Sequence[float]) -> float:
    return float(bbox[2]) - float(bbox[0])


def _height(bbox: Sequence[float]) -> float:
    return float(bbox[3]) - float(bbox[1])


def _positive(value: Any, label: str) -> float:
    result = _number(value, label)
    if result <= 0:
        raise PlacementError(f"{label} must be positive")
    return result


def _number(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise PlacementError(f"{label} must be a number")
    return float(value)


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlacementError(f"{label} must be a non-empty string")
    return value

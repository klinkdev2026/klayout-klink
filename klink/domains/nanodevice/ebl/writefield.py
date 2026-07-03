"""Writefield planning expressed as klink obstacles and corridor anchors."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import ceil, floor
from typing import Iterable, Literal, Sequence


BBox = list[float]
Point = list[float]
Axis = Literal["x", "y"]


@dataclass(frozen=True)
class CrossingWindow:
    """A permitted crossing window on one writefield boundary."""

    axis: Axis
    boundary_um: float
    center_um: float
    span_um: float
    id: str = ""
    nets: tuple[str, ...] = ()


@dataclass(frozen=True)
class WritefieldPlan:
    """JSON-serializable writefield plan."""

    wf_grid: list[dict]
    boundary_segments_um: list[dict]
    obstacle_boxes_um: list[BBox]
    corridor_anchor_specs: list[dict]
    report: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _bbox(bbox: Sequence[float]) -> tuple[float, float, float, float]:
    if len(bbox) != 4:
        raise ValueError("bbox must be [xmin, ymin, xmax, ymax]")
    x0, y0, x1, y1 = [float(v) for v in bbox]
    if x1 <= x0 or y1 <= y0:
        raise ValueError("bbox max values must exceed min values")
    return x0, y0, x1, y1


def _grid_lines(min_v: float, max_v: float, origin: float, pitch: float) -> list[float]:
    first = floor((min_v - origin) / pitch) + 1
    last = ceil((max_v - origin) / pitch) - 1
    return [origin + i * pitch for i in range(first, last + 1) if min_v < origin + i * pitch < max_v]


def _field_ranges(min_v: float, max_v: float, origin: float, pitch: float) -> list[tuple[float, float, int]]:
    starts = [min_v]
    starts.extend(_grid_lines(min_v, max_v, origin, pitch))
    ends = starts[1:] + [max_v]
    return [(a, b, idx) for idx, (a, b) in enumerate(zip(starts, ends))]


def _normalize_windows(crossing_windows: Iterable[CrossingWindow | dict] | None) -> list[CrossingWindow]:
    windows: list[CrossingWindow] = []
    for idx, item in enumerate(crossing_windows or []):
        if isinstance(item, CrossingWindow):
            win = item
        else:
            win = CrossingWindow(
                axis=str(item["axis"]),  # type: ignore[arg-type]
                boundary_um=float(item["boundary_um"]),
                center_um=float(item["center_um"]),
                span_um=float(item["span_um"]),
                id=str(item.get("id") or f"wf_cross_{idx}"),
                nets=tuple(str(n) for n in item.get("nets", ())),
            )
        if win.axis not in ("x", "y"):
            raise ValueError("crossing window axis must be 'x' or 'y'")
        if win.span_um <= 0:
            raise ValueError("crossing window span_um must be positive")
        windows.append(win if win.id else CrossingWindow(win.axis, win.boundary_um, win.center_um, win.span_um, f"wf_cross_{idx}", win.nets))
    return windows


def _auto_windows(
    chip: tuple[float, float, float, float],
    x_lines: Sequence[float],
    y_lines: Sequence[float],
    span_um: float,
) -> list[CrossingWindow]:
    x0, y0, x1, y1 = chip
    windows: list[CrossingWindow] = []
    for idx, x in enumerate(x_lines):
        windows.append(CrossingWindow("x", x, (y0 + y1) / 2.0, span_um, f"wf_x_{idx}"))
    for idx, y in enumerate(y_lines):
        windows.append(CrossingWindow("y", y, (x0 + x1) / 2.0, span_um, f"wf_y_{idx}"))
    return windows


def _segments_minus_windows(start: float, end: float, windows: Sequence[CrossingWindow]) -> list[tuple[float, float]]:
    cuts = sorted((max(start, w.center_um - w.span_um / 2.0), min(end, w.center_um + w.span_um / 2.0)) for w in windows)
    pieces: list[tuple[float, float]] = []
    cursor = start
    for a, b in cuts:
        if b <= cursor:
            continue
        if a > cursor:
            pieces.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < end:
        pieces.append((cursor, end))
    return [(a, b) for a, b in pieces if b - a > 1e-9]


def _window_anchor(win: CrossingWindow, margin_um: float) -> dict:
    if win.axis == "x":
        center = [win.boundary_um, win.center_um]
        width = max(margin_um * 4.0, 1.0)
        height = win.span_um
        path = f"0,{-win.span_um / 2.0};0,{win.span_um / 2.0}"
    else:
        center = [win.center_um, win.boundary_um]
        width = win.span_um
        height = max(margin_um * 4.0, 1.0)
        path = f"{-win.span_um / 2.0},0;{win.span_um / 2.0},0"
    return {
        "id": win.id,
        "center_um": center,
        "kind": "corridor",
        "mode": "flexible",
        "net": ",".join(win.nets),
        "required": True,
        "priority": 0,
        "width_um": width,
        "height_um": height,
        "path_points": path,
    }


def plan_writefields(
    chip_bbox_um: Sequence[float],
    *,
    writefield_size_um: float | Sequence[float] = 100.0,
    origin_um: Sequence[float] = (0.0, 0.0),
    stitch_margin_um: float = 1.0,
    crossing_windows: Iterable[CrossingWindow | dict] | None = None,
    auto_crossing_window_span_um: float | None = None,
) -> WritefieldPlan:
    """Plan writefield stitch walls without routing.

    Stitch walls are emitted as obstacle boxes on the caller's keepout layer.
    Crossing windows become corridor anchor parameter dictionaries.
    """

    x0, y0, x1, y1 = _bbox(chip_bbox_um)
    if isinstance(writefield_size_um, (int, float)):
        wx = wy = float(writefield_size_um)
    else:
        wx, wy = [float(v) for v in writefield_size_um]
    if wx <= 0 or wy <= 0:
        raise ValueError("writefield size must be positive")
    ox, oy = [float(v) for v in origin_um]
    margin = float(stitch_margin_um)
    if margin <= 0:
        raise ValueError("stitch_margin_um must be positive")

    x_lines = _grid_lines(x0, x1, ox, wx)
    y_lines = _grid_lines(y0, y1, oy, wy)
    windows = _normalize_windows(crossing_windows)
    if auto_crossing_window_span_um is not None:
        windows.extend(_auto_windows((x0, y0, x1, y1), x_lines, y_lines, float(auto_crossing_window_span_um)))

    wf_grid = []
    for xr0, xr1, ix in _field_ranges(x0, x1, ox, wx):
        for yr0, yr1, iy in _field_ranges(y0, y1, oy, wy):
            wf_grid.append({"id": f"wf_{ix}_{iy}", "bbox_um": [xr0, yr0, xr1, yr1], "ix": ix, "iy": iy})

    boundary_segments = []
    obstacle_boxes: list[BBox] = []
    for x in x_lines:
        boundary_segments.append({"axis": "x", "at_um": x, "span_um": [y0, y1]})
        axis_windows = [w for w in windows if w.axis == "x" and abs(w.boundary_um - x) <= 1e-9]
        for a, b in _segments_minus_windows(y0, y1, axis_windows):
            obstacle_boxes.append([x - margin, a, x + margin, b])
    for y in y_lines:
        boundary_segments.append({"axis": "y", "at_um": y, "span_um": [x0, x1]})
        axis_windows = [w for w in windows if w.axis == "y" and abs(w.boundary_um - y) <= 1e-9]
        for a, b in _segments_minus_windows(x0, x1, axis_windows):
            obstacle_boxes.append([a, y - margin, b, y + margin])

    anchors = [_window_anchor(w, margin) for w in windows]
    wall_area = sum(max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1]) for b in obstacle_boxes)
    report = {
        "field_count": len(wf_grid),
        "boundary_count": len(boundary_segments),
        "window_count": len(windows),
        "obstacle_count": len(obstacle_boxes),
        "wall_area_um2": wall_area,
        "warnings": _planner_warnings(wx, wy, margin, windows),
    }
    return WritefieldPlan(wf_grid, boundary_segments, obstacle_boxes, anchors, report)


def _planner_warnings(wx: float, wy: float, margin: float, windows: Sequence[CrossingWindow]) -> list[str]:
    warnings: list[str] = []
    if min(wx, wy) <= 100.0:
        warnings.append("small_writefields: keep per-field start/target matching outside this planner")
    if wx != wy:
        warnings.append("mixed_writefield_sizes: keep large and small fields in separate route batches")
    if any(w.span_um < margin * 4.0 for w in windows):
        warnings.append("narrow_crossing_window: congestion risk; route through explicit corridor anchors")
    return warnings

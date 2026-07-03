"""Pure single-layer feature-grid routing.

F1 builds a Hanan-style graph from terminal and obstacle features, adds
spacing-escape lines, and runs deterministic A* over rectilinear edges.  It is
intentionally independent of the existing router backends.
"""

from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from math import isfinite
from typing import Any, Iterable, Mapping, Sequence

NM_PER_UM = 1000

Point = tuple[float, float]
NodeKey = tuple[int, int]
BBox = tuple[float, float, float, float]
BBoxNm = tuple[int, int, int, int]


class FeatureGridError(ValueError):
    """Raised for malformed feature-grid input."""


@dataclass(frozen=True)
class FeatureGrid:
    nodes: tuple[NodeKey, ...]
    edges: tuple[tuple[NodeKey, NodeKey, int], ...]
    adjacency: Mapping[NodeKey, tuple[tuple[NodeKey, int], ...]]


def build_feature_grid(
    terminals: Sequence[Mapping[str, Any]],
    obstacles: Sequence[Sequence[float]],
    *,
    width_um: float,
    min_spacing_um: float,
    include_escape_lines: bool = True,
    extra_x_lines_nm: Sequence[int] = (),
    extra_y_lines_nm: Sequence[int] = (),
) -> FeatureGrid:
    """Single-layer Hanan grid + A*.

    ``extra_x_lines_nm`` / ``extra_y_lines_nm`` (default empty: byte-
    identical to the F1 goldens) inject additional grid lines so a
    multilayer caller (F2) can build every layer on a SHARED line basis,
    guaranteeing via points align across planes. Quantized integer nm.
    """
    terminal_points = [_terminal_point(term, index) for index, term in enumerate(_terminals(terminals))]
    obstacle_bboxes = [_bbox(obstacle, index) for index, obstacle in enumerate(obstacles)]
    margin = _clearance_margin(width_um, min_spacing_um)

    x_lines: set[int] = set(int(v) for v in extra_x_lines_nm)
    y_lines: set[int] = set(int(v) for v in extra_y_lines_nm)
    for point in terminal_points:
        x_lines.add(_quantize_um(point[0]))
        y_lines.add(_quantize_um(point[1]))
    for x1, y1, x2, y2 in obstacle_bboxes:
        for x in (x1, x2):
            x_lines.add(_quantize_um(x))
        for y in (y1, y2):
            y_lines.add(_quantize_um(y))
        if include_escape_lines:
            for x in (x1 - margin, x1 + margin, x2 - margin, x2 + margin):
                x_lines.add(_quantize_um(x))
            for y in (y1 - margin, y1 + margin, y2 - margin, y2 + margin):
                y_lines.add(_quantize_um(y))

    expanded = [_expanded_bbox_nm(bbox, margin) for bbox in obstacle_bboxes]
    x_sorted = tuple(sorted(x_lines))
    y_sorted = tuple(sorted(y_lines))
    nodes = tuple(
        (x, y)
        for y in y_sorted
        for x in x_sorted
        if not _point_inside_any_expanded((x, y), expanded)
    )
    node_set = set(nodes)
    edge_set: set[tuple[NodeKey, NodeKey, int]] = set()

    for y in y_sorted:
        row_nodes = [(x, y) for x in x_sorted if (x, y) in node_set]
        for a, b in zip(row_nodes, row_nodes[1:]):
            if _segment_clear_nm(a, b, expanded):
                edge_set.add(_edge(a, b))
    for x in x_sorted:
        col_nodes = [(x, y) for y in y_sorted if (x, y) in node_set]
        for a, b in zip(col_nodes, col_nodes[1:]):
            if _segment_clear_nm(a, b, expanded):
                edge_set.add(_edge(a, b))

    edges = tuple(sorted(edge_set, key=lambda item: (item[0], item[1], item[2])))
    adjacency: dict[NodeKey, list[tuple[NodeKey, int]]] = {node: [] for node in nodes}
    for a, b, cost in edges:
        adjacency[a].append((b, cost))
        adjacency[b].append((a, cost))
    frozen_adjacency = {node: tuple(sorted(neighbors, key=lambda item: item[0])) for node, neighbors in adjacency.items()}
    return FeatureGrid(nodes=nodes, edges=edges, adjacency=frozen_adjacency)


def shortest_path(graph: FeatureGrid, start_node: NodeKey, goal_node: NodeKey) -> list[Point] | None:
    start = _node_key(start_node, "start_node")
    goal = _node_key(goal_node, "goal_node")
    if start not in graph.adjacency or goal not in graph.adjacency:
        return None

    heap: list[tuple[int, int, NodeKey]] = []
    heappush(heap, (_manhattan_nm(start, goal), 0, start))
    came_from: dict[NodeKey, NodeKey] = {}
    best_cost: dict[NodeKey, int] = {start: 0}
    visited: set[NodeKey] = set()

    while heap:
        _, cost, node = heappop(heap)
        if node in visited:
            continue
        visited.add(node)
        if node == goal:
            return [_point_um(key) for key in _reconstruct(came_from, goal)]
        for neighbor, edge_cost in graph.adjacency[node]:
            new_cost = cost + edge_cost
            if new_cost < best_cost.get(neighbor, 10**30):
                best_cost[neighbor] = new_cost
                came_from[neighbor] = node
                priority = new_cost + _manhattan_nm(neighbor, goal)
                heappush(heap, (priority, new_cost, neighbor))
    return None


def route_net(
    terminals: Sequence[Mapping[str, Any]],
    obstacles: Sequence[Sequence[float]],
    *,
    width_um: float,
    min_spacing_um: float,
) -> dict[str, Any]:
    terminal_list = _terminals(terminals)
    if len(terminal_list) != 2:
        return {
            "problems": [
                {
                    "type": "unsupported_terminal_count",
                    "message": "feature-grid F1 routes exactly two terminals; split multi-terminal nets first",
                    "terminal_count": len(terminal_list),
                }
            ]
        }
    start = _quantized_terminal_point(terminal_list[0], 0)
    goal = _quantized_terminal_point(terminal_list[1], 1)
    graph = build_feature_grid(terminals, obstacles, width_um=width_um, min_spacing_um=min_spacing_um)
    path = shortest_path(graph, start, goal)
    if path is None:
        return {
            "problems": [
                {
                    "type": "no_path",
                    "message": "no feature-grid path exists between the two terminals",
                    "start": _point_um(start),
                    "goal": _point_um(goal),
                }
            ]
        }
    compact_path = _compress_collinear(path)
    return {
        "points_um": compact_path,
        "length_um": _path_length_um(compact_path),
        "segment_count": max(0, len(compact_path) - 1),
    }


def _terminals(terminals: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if not isinstance(terminals, Sequence) or isinstance(terminals, (str, bytes)):
        raise FeatureGridError("terminals must be a sequence of terminal mappings")
    if len(terminals) < 2:
        raise FeatureGridError("feature-grid routing requires at least two terminals")
    normalized: list[Mapping[str, Any]] = []
    for index, terminal in enumerate(terminals):
        if not isinstance(terminal, Mapping):
            raise FeatureGridError(f"terminal {index} must be a mapping")
        name = terminal.get("name")
        if not isinstance(name, str) or not name.strip():
            raise FeatureGridError(f"terminal {index}.name must be a non-empty string")
        _terminal_point(terminal, index)
        normalized.append(terminal)
    return normalized


def _terminal_point(terminal: Mapping[str, Any], index: int) -> Point:
    return _point(terminal.get("point_um"), f"terminal {index}.point_um")


def _quantized_terminal_point(terminal: Mapping[str, Any], index: int) -> NodeKey:
    point = _terminal_point(terminal, index)
    return (_quantize_um(point[0]), _quantize_um(point[1]))


def _point(value: Any, label: str) -> Point:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        raise FeatureGridError(f"{label} must be a 2-item coordinate")
    return (_finite_number(value[0], f"{label}[0]"), _finite_number(value[1], f"{label}[1]"))


def _bbox(value: Sequence[float], index: int) -> BBox:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        raise FeatureGridError(f"obstacle {index} must be a 4-item bbox")
    x1, y1, x2, y2 = (_finite_number(v, f"obstacle {index}") for v in value)
    if x1 >= x2 or y1 >= y2:
        raise FeatureGridError(f"obstacle {index} bbox must satisfy x1<x2 and y1<y2")
    return (x1, y1, x2, y2)


def _clearance_margin(width_um: float, min_spacing_um: float) -> float:
    width = _finite_number(width_um, "width_um")
    spacing = _finite_number(min_spacing_um, "min_spacing_um")
    if width <= 0:
        raise FeatureGridError("width_um must be > 0")
    if spacing < 0:
        raise FeatureGridError("min_spacing_um must be >= 0")
    return width / 2.0 + spacing


def _finite_number(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise FeatureGridError(f"{label} must be a finite number")
    result = float(value)
    if not isfinite(result):
        raise FeatureGridError(f"{label} must be a finite number")
    return result


def _quantize_um(value: float) -> int:
    return int(round(value * NM_PER_UM))


def _expanded_bbox_nm(bbox: BBox, margin_um: float) -> BBoxNm:
    x1, y1, x2, y2 = bbox
    return (
        _quantize_um(x1 - margin_um),
        _quantize_um(y1 - margin_um),
        _quantize_um(x2 + margin_um),
        _quantize_um(y2 + margin_um),
    )


def _point_inside_any_expanded(node: NodeKey, expanded_bboxes: Iterable[BBoxNm]) -> bool:
    x, y = node
    return any(x1 < x < x2 and y1 < y < y2 for x1, y1, x2, y2 in expanded_bboxes)


def _segment_clear_nm(a: NodeKey, b: NodeKey, expanded_bboxes: Iterable[BBoxNm]) -> bool:
    ax, ay = a
    bx, by = b
    if ax != bx and ay != by:
        raise FeatureGridError("feature-grid edges must be rectilinear")
    for x1, y1, x2, y2 in expanded_bboxes:
        if ay == by:
            y = ay
            if y1 < y < y2 and max(ax, bx) > x1 and min(ax, bx) < x2:
                return False
        else:
            x = ax
            if x1 < x < x2 and max(ay, by) > y1 and min(ay, by) < y2:
                return False
    return True


def _edge(a: NodeKey, b: NodeKey) -> tuple[NodeKey, NodeKey, int]:
    first, second = sorted((a, b))
    return (first, second, _manhattan_nm(first, second))


def _node_key(value: Any, label: str) -> NodeKey:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        raise FeatureGridError(f"{label} must be a 2-item integer-nm node key")
    x, y = value
    if not isinstance(x, int) or not isinstance(y, int) or isinstance(x, bool) or isinstance(y, bool):
        raise FeatureGridError(f"{label} must be a 2-item integer-nm node key")
    return (x, y)


def _manhattan_nm(a: NodeKey, b: NodeKey) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _point_um(node: NodeKey) -> Point:
    return (node[0] / NM_PER_UM, node[1] / NM_PER_UM)


def _reconstruct(came_from: Mapping[NodeKey, NodeKey], goal: NodeKey) -> list[NodeKey]:
    path = [goal]
    while path[-1] in came_from:
        path.append(came_from[path[-1]])
    path.reverse()
    return path


def _path_length_um(points: Sequence[Point]) -> float:
    total = 0.0
    for a, b in zip(points, points[1:]):
        total += abs(a[0] - b[0]) + abs(a[1] - b[1])
    return total


def _compress_collinear(points: Sequence[Point]) -> list[Point]:
    if len(points) <= 2:
        return list(points)
    compact = [points[0]]
    for prev, current, nxt in zip(points, points[1:], points[2:]):
        same_x = prev[0] == current[0] == nxt[0]
        same_y = prev[1] == current[1] == nxt[1]
        if not (same_x or same_y):
            compact.append(current)
    compact.append(points[-1])
    return compact

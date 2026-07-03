"""Equivalence tests for the fast visibility-graph adjacency build.

_axis_aligned_adjacency replaced a brute-force O(V^2 * R) double loop in
_shortest_visibility_path. These tests keep the original loop as a reference
implementation and assert the fast build produces an identical adjacency
(same edges, same costs, same per-node ordering) on randomized lattice
configurations for both manhattan and fortyfive modes, with obstacles.
"""

from __future__ import annotations

import random

from klink.routing.geom.geometric import (
    _angle_allowed,
    _axis_aligned_adjacency,
    _distance,
    _point_in_bbox,
    _point_key,
    _segment_clear,
)


def _brute_force_adjacency(nodes, blocked_bboxes, angle_mode):
    """The original adjacency build, kept verbatim as the reference."""
    adjacency = [[] for _ in nodes]
    for i, a in enumerate(nodes):
        for j in range(i + 1, len(nodes)):
            b = nodes[j]
            if not _angle_allowed(a, b, angle_mode):
                continue
            if not _segment_clear(a, b, blocked_bboxes):
                continue
            cost = _distance(a, b)
            adjacency[i].append((j, cost))
            adjacency[j].append((i, cost))
    return adjacency


def _random_config(rng):
    lattice = [round(v * 0.5, 1) for v in range(0, 41)]  # 0.0 .. 20.0
    blocked = []
    for _ in range(rng.randint(1, 6)):
        x1, x2 = sorted(rng.sample(lattice, 2))
        y1, y2 = sorted(rng.sample(lattice, 2))
        if x1 == x2 or y1 == y2:
            continue
        blocked.append([x1, y1, x2, y2])

    seen = {}
    for _ in range(rng.randint(8, 40)):
        point = [rng.choice(lattice), rng.choice(lattice)]
        if any(_point_in_bbox(point, bbox) for bbox in blocked):
            continue
        seen[_point_key(point)] = point
    return list(seen.values()), blocked


def test_fast_adjacency_matches_brute_force_manhattan():
    rng = random.Random(424242)
    for _ in range(30):
        nodes, blocked = _random_config(rng)
        assert _axis_aligned_adjacency(nodes, blocked, "manhattan") == \
            _brute_force_adjacency(nodes, blocked, "manhattan")


def test_fast_adjacency_matches_brute_force_fortyfive():
    rng = random.Random(525252)
    for _ in range(30):
        nodes, blocked = _random_config(rng)
        assert _axis_aligned_adjacency(nodes, blocked, "fortyfive") == \
            _brute_force_adjacency(nodes, blocked, "fortyfive")


def test_fast_adjacency_handles_no_obstacles_and_dense_collinear():
    # A dense cross: many collinear nodes on one row and one column.
    nodes = [[float(i), 5.0] for i in range(10)] + [[3.0, float(j)] for j in range(10) if j != 5]
    for mode in ("manhattan", "fortyfive"):
        assert _axis_aligned_adjacency(nodes, [], mode) == \
            _brute_force_adjacency(nodes, [], mode)

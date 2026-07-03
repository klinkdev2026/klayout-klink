"""Port validation policy.

The plugin supplies current port parameter dictionaries and applies returned
edits. This module decides what is invalid and what repair is appropriate.
"""

from __future__ import annotations

from .inference import angle_diff, snap_to_angle_grid
from .naming import auto_name


def duplicate_name_groups(port_params: list[dict]) -> dict:
    groups: dict = {}
    for port in port_params:
        name = str(port.get("name", ""))
        groups.setdefault(name, []).append(port)
    return groups


def duplicate_names(port_params: list[dict]) -> list[dict]:
    groups = duplicate_name_groups(port_params)
    return [
        {"name": name, "count": len(items)}
        for name, items in sorted(groups.items())
        if name and len(items) > 1
    ]


def duplicate_name_repairs(port_params: list[dict]) -> list[dict]:
    groups = duplicate_name_groups(port_params)
    existing_names = set(groups.keys())
    repairs: list[dict] = []
    for name, items in sorted(groups.items()):
        if not name or len(items) <= 1:
            continue
        for port in items[1:]:
            new_name = auto_name(0.0, existing_names, "electrical",
                                 index=0, style="index")
            existing_names.add(new_name)
            repairs.append({
                "old_name": name,
                "new_name": new_name,
                "port": port,
            })
    return repairs


def off_grid_orientations(port_params: list[dict],
                          tolerance_deg: float = 0.001) -> list[dict]:
    result: list[dict] = []
    for port in port_params:
        try:
            orient = float(port.get("orientation", 0.0))
        except Exception:
            continue
        snapped = snap_to_angle_grid(orient, step=45.0)
        delta = angle_diff(orient, snapped)
        if delta > tolerance_deg:
            result.append({
                "name": str(port.get("name", "")),
                "orientation": orient,
                "nearest_grid_orientation": snapped,
                "delta_deg": delta,
                "port": port,
            })
    return result

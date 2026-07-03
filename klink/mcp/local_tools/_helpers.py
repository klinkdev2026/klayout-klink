"""Pure helper functions shared by the domain handler modules.

Result compaction (keep large router/route payloads small for the agent),
transfer-selection snapshotting, dbu extraction, and selection age stamping.
No dependency on the bridge or the registry, so domain modules and the bridge
can both import these without a cycle.
"""

from __future__ import annotations

import sys

from ...transfer import TransferError
from ..interaction_context import age_seconds


def _gdsfactory_unavailable_message(detail: str) -> str:
    return (
        f"gdsfactory backend unavailable in the MCP interpreter "
        f"({sys.executable}): {detail}. Fix: \"{sys.executable}\" -m pip "
        "install gdsfactory — or re-register klink-mcp "
        "with an interpreter that has gdsfactory installed. Check "
        "klink.status -> capabilities."
    )


def _layout_dbu_um(info: dict) -> float:
    for key in ("dbu", "dbu_um"):
        value = info.get(key)
        if value is not None:
            return float(value)
    layout = info.get("layout")
    if isinstance(layout, dict):
        for key in ("dbu", "dbu_um"):
            value = layout.get(key)
            if value is not None:
                return float(value)
    raise TransferError("layout.info did not report dbu")


def _selected_instance_snapshot(source_client, selection: dict) -> dict:
    objects = selection.get("objects")
    if not isinstance(objects, list) or not objects:
        raise TransferError("empty selection")
    instances = []
    for index, obj in enumerate(objects):
        if not (obj.get("is_cell_inst") or obj.get("kind") == "instance"):
            raise TransferError("shallow_instance copy requires only selected instances")
        parent = obj.get("cell")
        child = obj.get("target_cell") or obj.get("child")
        bbox = obj.get("bbox_dbu")
        if not isinstance(parent, str) or not isinstance(child, str):
            raise TransferError(f"selected instance {index} is missing parent/child cell")
        query_args = {"child": child, "limit": 10}
        if isinstance(bbox, list) and len(bbox) == 4:
            query_args["bbox_dbu"] = bbox
        result = source_client.instance_query(parent, **query_args)
        matches = result.get("instances", [])
        if len(matches) != 1:
            raise TransferError(
                f"selected instance {index} matched {len(matches)} source instances; "
                "select a less ambiguous instance"
            )
        inst = dict(matches[0])
        inst["parent"] = parent
        instances.append(inst)
    return {"instances": instances}


def _compact_route_result(result: dict) -> dict:
    compact_groups = []
    for group in result.get("groups", []):
        write = group.get("write")
        compact_write = None
        if isinstance(write, dict):
            compact_write = {
                "mode": write.get("mode"),
                "route_count": write.get("route_count"),
                "paths": write.get("paths"),
                "patches": write.get("patches"),
                "polygons": write.get("polygons"),
                "deleted": write.get("deleted"),
            }
        compact_groups.append({
            "route_layer": group.get("route_layer"),
            "ok": group.get("ok"),
            "route_count": group.get("route_count"),
            "lane_reports": [_compact_lane_report(r) for r in group.get("lane_reports", [])],
            "sibling_overlap_count": len(group.get("sibling_overlaps", []) or []),
            "obstacle_hit_count": len(group.get("obstacle_hits", []) or []),
            "planning_errors": group.get("planning_errors", []),
            "route_order": group.get("route_order"),
            "route_order_attempts": group.get("route_order_attempts"),
            "errors": group.get("errors", []),
            "write": compact_write,
        })
    return {
        "ok": result.get("ok"),
        "backend": result.get("backend"),
        "cell": result.get("cell"),
        "port_count": result.get("port_count"),
        "anchor_count": result.get("anchor_count"),
        "pair_count": result.get("pair_count"),
        "angle_mode": result.get("angle_mode"),
        "damping_distance_um": result.get("damping_distance_um"),
        "safe_distance_um": result.get("safe_distance_um"),
        "obstacle_layers": result.get("obstacle_layers", []),
        "obstacle_count": len(result.get("obstacle_bboxes", []) or []),
        "candidate_assignment": result.get("candidate_assignment", []),
        "corridor_assignment": result.get("corridor_assignment", []),
        "planning_errors": result.get("planning_errors", []),
        "errors": result.get("errors", []),
        "groups": compact_groups,
    }


def _compact_polygon_route_result(result: dict) -> dict:
    compact_groups = []
    for group in result.get("groups", []):
        write = group.get("write")
        compact_write = None
        if isinstance(write, dict):
            compact_write = {
                "route_layer": write.get("route_layer"),
                "deleted": write.get("deleted"),
                "inserted_polygons": write.get("inserted_polygons"),
                "inserted_paths_fallback": write.get("inserted_paths_fallback"),
            }
        compact_groups.append({
            "route_layer": group.get("route_layer"),
            "ok": group.get("ok"),
            "route_count": group.get("route_count"),
            "validation_count": len(group.get("validations", []) or []),
            "planning_errors": group.get("planning_errors", []),
            "obstacle_hit_count": len(group.get("obstacle_hits", []) or []),
            "sibling_overlaps": group.get("sibling_overlaps", 0),
            "errors": group.get("errors", []),
            "write": compact_write,
        })
    return {
        "ok": result.get("ok"),
        "backend": result.get("backend"),
        "cell": result.get("cell"),
        "port_count": result.get("port_count"),
        "anchor_count": result.get("anchor_count"),
        "pair_count": result.get("pair_count"),
        "obstacle_layers": result.get("obstacle_layers", []),
        "angle_mode": result.get("angle_mode"),
        "damping_distance_um": result.get("damping_distance_um"),
        "obstacle_count": len(result.get("obstacle_bboxes", []) or []),
        "planning_errors": result.get("planning_errors", []),
        "errors": result.get("errors", []),
        "groups": compact_groups,
    }


def _compact_gdsfactory_route_result(result: dict) -> dict:
    writeback = result.get("writeback")
    compact_writeback = None
    if isinstance(writeback, dict):
        compact_writeback = {
            key: writeback.get(key)
            for key in ("cell", "route_layer", "inserted", "paths", "polygons", "deleted")
            if key in writeback
        }
    return {
        "ok": result.get("ok"),
        "backend": result.get("backend"),
        "cell": result.get("cell"),
        "route_count": len(result.get("routes", []) or []),
        "ports1": [p.get("name") for p in result.get("ports1", []) if isinstance(p, dict)],
        "ports2": [p.get("name") for p in result.get("ports2", []) if isinstance(p, dict)],
        "crossing_count": len(result.get("crossings", []) or []),
        "output_mode": result.get("output_mode"),
        "writeback": compact_writeback,
    }


def _compact_steiner_result(result: dict) -> dict:
    compact_groups = []
    for group in result.get("groups", []) or []:
        write = group.get("write")
        compact_write = None
        if isinstance(write, dict):
            compact_write = {
                "cell": write.get("cell"),
                "route_layer": write.get("route_layer"),
                "deleted": write.get("deleted"),
                "inserted": write.get("inserted"),
            }
        compact_groups.append({
            "ok": group.get("ok"),
            "net": group.get("net"),
            "root": group.get("root"),
            "port_count": group.get("port_count"),
            "route_count": group.get("route_count"),
            "route_layer": group.get("route_layer"),
            "trunk_axis": group.get("trunk_axis"),
            "obstacle_hit_count": len(group.get("obstacle_hits", []) or []),
            "errors": group.get("errors", []),
            "write": compact_write,
        })
    return {
        "ok": result.get("ok"),
        "backend": result.get("backend"),
        "cell": result.get("cell"),
        "port_count": result.get("port_count"),
        "anchor_count": result.get("anchor_count"),
        "angle_mode": result.get("angle_mode"),
        "damping_distance_um": result.get("damping_distance_um"),
        "obstacle_layers": result.get("obstacle_layers", []),
        "obstacle_count": len(result.get("obstacle_bboxes", []) or []),
        "groups": compact_groups,
        "errors": result.get("errors", []),
    }


def _compact_multilayer_result(result: dict) -> dict:
    write = result.get("write")
    compact_write = None
    if isinstance(write, dict):
        compact_write = {
            "route_layer": write.get("route_layer"),
            "bridge_layer": write.get("bridge_layer"),
            "via_layer": write.get("via_layer"),
            "deleted": write.get("deleted"),
            "primary_paths": write.get("primary_paths"),
            "bridge_paths": write.get("bridge_paths"),
            "vias": write.get("vias"),
        }
    return {
        "ok": result.get("ok"),
        "backend": result.get("backend"),
        "cell": result.get("cell"),
        "port_count": result.get("port_count"),
        "pair_count": result.get("pair_count"),
        "route_count": result.get("route_count"),
        "route_layer": result.get("route_layer"),
        "bridge_layer": result.get("bridge_layer"),
        "via_layer": result.get("via_layer"),
        "obstacle_layers": result.get("obstacle_layers", []),
        "obstacle_count": len(result.get("obstacle_bboxes", []) or []),
        "obstacle_hit_count": len(result.get("obstacle_hits", []) or []),
        "errors": result.get("errors", []),
        "write": compact_write,
    }


def _compact_lane_report(report: dict) -> dict:
    offsets = [float(v) for v in (report.get("offsets_um", []) or [])]
    compact = {
        "corridor_id": report.get("corridor_id"),
        "net_count": report.get("net_count"),
        "pitch_um": report.get("pitch_um"),
        "capacity_ok": report.get("capacity_ok"),
    }
    if offsets:
        compact["offset_count"] = len(offsets)
        compact["offset_min_um"] = min(offsets)
        compact["offset_max_um"] = max(offsets)
    if report.get("capacity_issue"):
        compact["capacity_issue"] = report.get("capacity_issue")
    return compact


def _with_age(record: dict | None) -> dict | None:
    if record is None:
        return None
    out = dict(record)
    age = age_seconds(out)
    if age is not None:
        out["age_s"] = round(age, 3)
    return out

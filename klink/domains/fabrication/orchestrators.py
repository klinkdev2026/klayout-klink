"""One-call fabrication annotation and site-array orchestrators."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Sequence

from .mapping import SiteMap, default_site_paths, write_sites_csv, write_sites_json
from .marks import CHIP_MARKS, from_preset_groups, parse_layer, placed_mark_group_items
from .sites import Site, generate_circular_die_sites, generate_grid_sites, number_sites


TOOL_VERSION = "fabrication-annotation-v1"


def place_site_array(
    client,
    *,
    cell: str,
    site_layout: dict,
    marks: dict | None = None,
    label_layer: str = "6/0",
    instance: dict | None = None,
    numbering: dict | None = None,
    state_dir: str | Path = Path(".klink") / "fabrication",
    dry_run: bool = False,
) -> dict:
    timings: dict[str, float] = {}
    problems = _validate_inputs(cell, site_layout, marks, label_layer, instance, numbering)
    if problems:
        return _failure("invalid_input", problems)

    try:
        sites = _phase(timings, "generate_sites", lambda: _build_sites(site_layout))
        numbering = numbering or {"scheme": "rowcol"}
        sites = number_sites(sites, **numbering)
        if not sites:
            return _failure(
                "empty_site_array",
                ["site_layout produced zero sites."],
                "Adjust region, pitch, edge_exclusion_um, or footprint_um so at least one full footprint fits; then call this again.",
                timings,
            )
        mark_records, mark_items, mark_instances = _phase(timings, "build_marks", lambda: _build_marks(sites, marks, site_layout))
        label_items = _build_labels(sites, label_layer)
        instance_items = [*_build_instances(sites, instance), *mark_instances]
        _validate_label_collisions(sites)
    except Exception as exc:
        return _failure(
            "prepare_failed",
            [f"{type(exc).__name__}: {exc}"],
            "Fix the site_layout, mark, layer, numbering, or instance parameters and call place_site_array again.",
            timings,
        )

    meta = {
        "tool_version": TOOL_VERSION,
        "created_at": _utc_timestamp(),
        "site_layout": dict(site_layout),
        "numbering": dict(numbering),
        "marks": dict(marks or {}),
        "label_layer": label_layer,
        "instance": dict(instance or {}),
        "honesty": "Parameters are recorded for downstream tools; no fab acceptance is implied.",
    }
    site_map = SiteMap(cell=cell, sites=list(sites), marks=mark_records, meta=meta)
    json_path, csv_path = default_site_paths(state_dir, cell)
    shape_items = [*mark_items, *label_items]

    if dry_run:
        return {
            "ok": True,
            "committed": False,
            "dry_run": True,
            "cell": cell,
            "site_count": len(sites),
            "shape_item_count": len(shape_items),
            "instance_count": len(instance_items),
            "site_map": site_map.to_dict(),
            "state_paths": {"json": str(json_path), "csv": str(csv_path)},
            "timings_s": timings,
            "problems": [],
            "next_action": "Dry run passed. Call again with dry_run=false to write marks, labels, instances, and site-map files.",
        }

    created = False
    try:
        _phase(timings, "delete_old_cell", lambda: _delete_cell(client, cell))
        _phase(timings, "cell_create", lambda: client.cell_create(cell))
        created = True
        _phase(timings, "ensure_layers", lambda: _ensure_item_layers(client, shape_items))
        insert_shapes = _phase(timings, "shape_insert_many", lambda: client.shape_insert_many(cell, shape_items)) if shape_items else {"inserted": 0}
        insert_instances = (
            _phase(timings, "instance_insert_many", lambda: client.instance_insert_many(cell, instance_items))
            if instance_items
            else {"inserted": 0}
        )
        _phase(timings, "write_sites_json", lambda: write_sites_json(site_map, json_path))
        _phase(timings, "write_sites_csv", lambda: write_sites_csv(site_map, csv_path))
        _phase(timings, "show_cell", lambda: client.show_cell(cell, zoom_fit=True))
    except Exception as exc:
        if created:
            _delete_cell(client, cell)
        _delete_path(json_path)
        _delete_path(csv_path)
        return _failure(
            "live_commit_failed",
            [f"{type(exc).__name__}: {exc}"],
            "The site map validated, but KLayout writeback or facts-file emission failed. Reconnect/check paths and call this again; no state files are written before live writes start.",
            timings,
            extra={"cell": cell},
        )

    return {
        "ok": True,
        "committed": True,
        "cell": cell,
        "site_count": len(sites),
        "shape_item_count": len(shape_items),
        "instance_count": len(instance_items),
        "writeback": {"shapes": insert_shapes, "instances": insert_instances},
        "state_paths": {"json": str(json_path), "csv": str(csv_path)},
        "site_map": site_map.to_dict(),
        "timings_s": timings,
        "problems": [],
        "next_action": f"Fabrication site array committed to {cell}; facts files written to {json_path} and {csv_path}.",
    }


def _validate_inputs(cell, site_layout, marks, label_layer, instance, numbering) -> list[str]:
    problems = []
    if not str(cell).strip():
        problems.append("cell must be a non-empty string.")
    if not isinstance(site_layout, dict) or "kind" not in site_layout:
        problems.append("site_layout must be a dict with kind='grid' or kind='circular'.")
    else:
        if site_layout.get("kind") not in {"grid", "circular"}:
            problems.append("site_layout.kind must be 'grid' or 'circular'.")
    mark_mode = (marks or {}).get("mode", "generated")
    for label, layer in [("label_layer", label_layer), ("marks.layer", (marks or {}).get("layer", "6/0"))]:
        if mark_mode == "cells" and label == "marks.layer":
            continue
        try:
            parse_layer(layer)
        except Exception:
            problems.append(f"{label} must be a valid layer string like '6/0'.")
    if mark_mode not in {"generated", "cells"}:
        problems.append("marks.mode must be 'cells' or 'generated'.")
    if mark_mode == "cells":
        corners = (marks or {}).get("corners")
        required = {"leftdown", "leftup", "rightdown", "rightup"}
        if not isinstance(corners, dict):
            problems.append("marks.corners must map leftdown/leftup/rightdown/rightup to existing mark cell names.")
        else:
            missing = sorted(required - set(corners))
            if missing:
                problems.append(f"marks.corners is missing required corner key(s): {', '.join(missing)}.")
            values = [str(corners.get(key, "")).strip() for key in sorted(required)]
            if any(not value for value in values):
                problems.append("marks.corners values must be non-empty mark cell names.")
            if len(set(values)) != len(values):
                problems.append("marks.corners must use four distinct process-proven corner mark cells; do not place one identical mark at all corners.")
    if instance is not None and not str(instance.get("child", "")).strip():
        problems.append("instance.child must name the child cell to place at every site.")
    if numbering is not None and numbering.get("scheme", "rowcol") not in {"rowcol", "serpentine", "sequential", "prefix"}:
        problems.append("numbering.scheme must be rowcol, serpentine, sequential, or prefix.")
    return problems


def _build_sites(site_layout: dict) -> list[Site]:
    kind = site_layout.get("kind")
    if kind == "grid":
        return generate_grid_sites(
            site_layout["region_bbox_um"],
            site_layout["pitch_x_um"],
            site_layout["pitch_y_um"],
            margin_um=site_layout.get("margin_um", 0.0),
            rotation_deg=site_layout.get("rotation_deg", 0.0),
        )
    if kind == "circular":
        return generate_circular_die_sites(
            site_layout.get("center_um", [0.0, 0.0]),
            site_layout["diameter_um"],
            site_layout["pitch_x_um"],
            site_layout["pitch_y_um"],
            site_layout["edge_exclusion_um"],
            footprint_um=site_layout.get("footprint_um", 0.0),
        )
    raise ValueError("site_layout.kind must be 'grid' or 'circular'")


def _build_marks(sites: Sequence[Site], marks: dict | None, site_layout: dict) -> tuple[list[dict], list[dict], list[dict]]:
    # No marks unless the caller (your example) asks for them: klink injects no
    # default mark layer/preset. To generate marks, pass a `marks` config with
    # your layer, e.g. {"mode": "generated", "preset": "chip", "layer": "6/0"};
    # named presets are an overridable starter library (see fabrication.marks).
    if not marks:
        return [], [], []
    cfg = {"mode": "generated", "placement": "corners", "preset": "chip"}
    cfg.update(marks)
    mode = cfg.get("mode", "generated")
    placement = cfg.get("placement", "corners")
    if placement == "none":
        return [], [], []
    centers_by_corner = _mark_corner_centers(sites, site_layout)
    if mode == "cells":
        return _build_cell_mark_instances(cfg, centers_by_corner)
    preset = str(cfg.get("preset", "chip"))
    override = {k: v for k, v in cfg.items() if k not in {"mode", "placement", "preset"}}
    groups = from_preset_groups(preset, **override)
    centers = _mark_centers(sites, placement, site_layout)
    records = [
        {
            "kind": "generated_alignment_mark",
            "preset": preset,
            "center_um": [float(c[0]), float(c[1])],
            "layers": [group["layer"] for group in groups],
        }
        for c in centers
    ]
    items = []
    for center in centers:
        items.extend(placed_mark_group_items(groups, center))
    return records, items, []


def _build_cell_mark_instances(cfg: dict, centers_by_corner: dict[str, tuple[float, float]]) -> tuple[list[dict], list[dict], list[dict]]:
    corners = cfg["corners"]
    records = []
    instances = []
    for key in ("leftdown", "leftup", "rightdown", "rightup"):
        child = str(corners[key])
        center = centers_by_corner[key]
        records.append({
            "kind": "cell_alignment_mark",
            "corner": key,
            "cell": child,
            "center_um": [float(center[0]), float(center[1])],
        })
        instances.append({
            "child": child,
            "position_um": [float(center[0]), float(center[1])],
            "rotation_deg": float(cfg.get("rotation_deg", 0.0)),
            "mirror": bool(cfg.get("mirror", False)),
            "magnification": float(cfg.get("magnification", 1.0)),
        })
    return records, [], instances


def _mark_centers(sites: Sequence[Site], placement: str, site_layout: dict) -> list[tuple[float, float]]:
    if placement == "all_sites":
        return [site.center_um for site in sites]
    if placement == "corners":
        if site_layout.get("kind") == "grid":
            x0, y0, x1, y1 = [float(v) for v in site_layout["region_bbox_um"]]
            return [(x0, y0), (x0, y1), (x1, y0), (x1, y1)]
        xs = [site.center_um[0] for site in sites]
        ys = [site.center_um[1] for site in sites]
        return [(min(xs), min(ys)), (min(xs), max(ys)), (max(xs), min(ys)), (max(xs), max(ys))]
    raise ValueError("marks.placement must be corners, all_sites, or none")


def _mark_corner_centers(sites: Sequence[Site], site_layout: dict) -> dict[str, tuple[float, float]]:
    if site_layout.get("kind") == "grid":
        x0, y0, x1, y1 = [float(v) for v in site_layout["region_bbox_um"]]
    else:
        xs = [site.center_um[0] for site in sites]
        ys = [site.center_um[1] for site in sites]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
    return {
        "leftdown": (x0, y0),
        "leftup": (x0, y1),
        "rightdown": (x1, y0),
        "rightup": (x1, y1),
    }


def _build_labels(sites: Sequence[Site], label_layer: str) -> list[dict]:
    layer, datatype = parse_layer(label_layer)
    return [
        {
            "kind": "text",
            "layer": layer,
            "datatype": datatype,
            "text": site.site_id,
            "position_um": [site.center_um[0], site.center_um[1]],
            "size_um": 25.0,
        }
        for site in sites
    ]


def _build_instances(sites: Sequence[Site], instance: dict | None) -> list[dict]:
    if not instance:
        return []
    child = str(instance["child"])
    return [
        {
            "child": child,
            "position_um": [site.center_um[0], site.center_um[1]],
            "rotation_deg": float(instance.get("rotation_deg", site.rotation_deg)),
            "mirror": bool(instance.get("mirror", False)),
            "magnification": float(instance.get("magnification", 1.0)),
        }
        for site in sites
    ]


def _validate_label_collisions(sites: Sequence[Site]) -> None:
    ids = [site.site_id for site in sites]
    if len(ids) != len(set(ids)):
        raise ValueError("numbering produced duplicate site_id labels")


def _delete_cell(client, cell: str) -> None:
    try:
        client.cell_delete(cell, recursive=True)
    except Exception:
        pass


def _ensure_item_layers(client, items: Sequence[dict]) -> None:
    seen: set[tuple[int, int]] = set()
    for item in items:
        key = (int(item["layer"]), int(item.get("datatype", 0)))
        if key in seen:
            continue
        seen.add(key)
        client.layer_ensure(key[0], key[1], name=f"FABRICATION_{key[0]}_{key[1]}")


def _delete_path(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _phase(timings: dict, name: str, fn):
    start = time.perf_counter()
    result = fn()
    timings[name] = round(time.perf_counter() - start, 4)
    return result


def _failure(reason: str, problems: list[str], next_action: str | None = None, timings: dict | None = None, extra: dict | None = None) -> dict:
    payload = {
        "ok": False,
        "committed": False,
        "reason": reason,
        "problems": problems,
        "next_action": next_action or "Read the problems list, fix the inputs exactly as described, and call place_site_array again.",
    }
    if timings is not None:
        payload["timings_s"] = timings
    if extra:
        payload.update(extra)
    return payload


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

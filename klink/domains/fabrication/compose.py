"""Complete-mask fabrication composer.

Payload cell in, die cell plus wafer cell out.  The composer only places
existing cells and optional text labels; it never edits the payload cell.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .marks import parse_layer
from .sites import Site, generate_circular_die_sites, generate_grid_sites


CORNER_KEYS = ("leftdown", "leftup", "rightdown", "rightup")
TOOL_VERSION = "fabrication-composer-v1"


@dataclass(frozen=True)
class DieTemplate:
    payload_position_um: tuple[float, float]
    bonding: dict[str, Any]
    corner_marks: dict[str, Any]
    die_size_um: tuple[float, float]
    global_marks: list[dict[str, Any]] = field(default_factory=list)
    labels: list[dict[str, Any]] = field(default_factory=list)
    allow_identical_corners: bool = False


@dataclass(frozen=True)
class WaferTemplate:
    mode: str
    numbering: dict[str, Any] = field(default_factory=dict)
    wafer_size_um: tuple[float, float] | None = None
    wafer_diameter_um: float | None = None
    exclusion_margin_um: float = 0.0
    die_pitch_um: tuple[float, float] | None = None
    center_um: tuple[float, float] = (0.0, 0.0)
    position_um: tuple[float, float] = (0.0, 0.0)
    region_bbox_um: tuple[float, float, float, float] | None = None
    rotation_deg: float = 0.0


def compose_complete_mask(
    client,
    payload_cell: str,
    *,
    name: str,
    die: DieTemplate,
    wafer: WaferTemplate,
    facts_dir: str = ".klink/fabrication",
) -> dict:
    die_cell = f"{name}_die"
    wafer_cell = f"{name}_wafer"
    problems = _validate_static(payload_cell, name, die, wafer)
    if problems:
        return _failure("invalid_input", die_cell, wafer_cell, problems)

    try:
        existing_cells = _existing_cells(client)
    except Exception as exc:
        return _failure(
            "cell_catalog_failed",
            die_cell,
            wafer_cell,
            [f"{type(exc).__name__}: could not list cells before writing: {exc}"],
            "Reconnect to the target layout and call compose_complete_mask again.",
        )

    problems = _validate_against_layout(payload_cell, die_cell, wafer_cell, die, wafer, existing_cells)
    if problems:
        return _failure("invalid_input", die_cell, wafer_cell, problems)

    try:
        die_instances, die_shapes, die_placements = _build_die(payload_cell, die, die_cell)
        wafer_sites = _build_wafer_sites(wafer, die)
        wafer_instances, wafer_placements = _build_wafer(die_cell, wafer, wafer_sites, wafer_cell)
        layer_keys = sorted({(int(item["layer"]), int(item.get("datatype", 0))) for item in die_shapes})
        facts = _facts_payload(
            payload_cell=payload_cell,
            name=name,
            die=die,
            wafer=wafer,
            die_cell=die_cell,
            wafer_cell=wafer_cell,
            placements=[*die_placements, *wafer_placements],
            wafer_sites=wafer_sites,
        )
    except Exception as exc:
        return _failure(
            "prepare_failed",
            die_cell,
            wafer_cell,
            [f"{type(exc).__name__}: {exc}"],
            "Fix the template values named in the error and call compose_complete_mask again.",
        )

    created: list[str] = []
    facts_path = Path(facts_dir) / f"{name}.facts.json"
    meta_path = Path(facts_dir) / f"{name}.run_meta.json"
    try:
        created_name = _create_exact_cell(client, die_cell)
        created.append(created_name)
        created_name = _create_exact_cell(client, wafer_cell)
        created.append(created_name)
        for layer, datatype in layer_keys:
            client.layer_ensure(layer, datatype, name=f"FAB_COMPOSE_{layer}_{datatype}")
        if die_shapes:
            client.shape_insert_many(die_cell, die_shapes)
        client.instance_insert_many(die_cell, die_instances)
        client.instance_insert_many(wafer_cell, wafer_instances)
        facts_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(facts_path, facts)
        _write_json(
            meta_path,
            {
                "tool_version": TOOL_VERSION,
                "facts_path": str(facts_path),
                "timestamp_utc": _utc_timestamp(),
            },
        )
    except Exception as exc:
        for cell in reversed(created):
            _delete_cell_best_effort(client, cell)
        _delete_path(facts_path)
        _delete_path(meta_path)
        return _failure(
            "commit_failed",
            die_cell,
            wafer_cell,
            [f"{type(exc).__name__}: {exc}"],
            "The layout write failed; fix the named missing cell/layer/path issue and retry with the same inputs. Created composer cells and partial facts were removed.",
        )

    return {
        "ok": True,
        "die_cell": die_cell,
        "wafer_cell": wafer_cell,
        "placements": facts["placements"],
        "facts_path": str(facts_path),
        "problems": [],
    }


def _validate_static(payload_cell: str, name: str, die: DieTemplate, wafer: WaferTemplate) -> list[str]:
    problems: list[str] = []
    if not str(payload_cell).strip():
        problems.append("payload_cell must be a non-empty existing cell name.")
    if not str(name).strip():
        problems.append("name must be a non-empty prefix for created cells.")
    if wafer.mode not in {"single", "grid", "circular"}:
        problems.append("wafer.mode must be one of: single, grid, circular.")
    if _pair(die.die_size_um)[0] <= 0 or _pair(die.die_size_um)[1] <= 0:
        problems.append("die.die_size_um must be [width, height] with positive values.")
    if not _is_pair(die.payload_position_um):
        problems.append("die.payload_position_um must be [x, y].")
    elif not _within_die(die.payload_position_um, die.die_size_um):
        problems.append(
            f"die.payload_position_um={list(die.payload_position_um)} is outside die_size_um {list(die.die_size_um)}; move it inside the die frame."
        )
    problems.extend(_validate_bonding(die))
    problems.extend(_validate_corner_marks(die))
    problems.extend(_validate_global_marks(die))
    problems.extend(_validate_labels(die))
    problems.extend(_validate_wafer(wafer))
    return problems


def _validate_bonding(die: DieTemplate) -> list[str]:
    problems = []
    bonding = die.bonding
    if not isinstance(bonding, dict) or not str(bonding.get("cell", "")).strip():
        return ["die.bonding.cell must name an existing bonding cell."]
    positions = bonding.get("positions_um")
    if not isinstance(positions, list):
        return ["die.bonding.positions_um must be a list of [x, y] positions."]
    for i, pos in enumerate(positions):
        if not _is_pair(pos):
            problems.append(f"die.bonding.positions_um[{i}] must be [x, y].")
        elif not _within_die(pos, die.die_size_um):
            problems.append(
                f"die.bonding.positions_um[{i}]={list(pos)} is outside die_size_um {list(die.die_size_um)}; move it onto or inside the die frame."
            )
    return problems


def _validate_corner_marks(die: DieTemplate) -> list[str]:
    marks = die.corner_marks
    if not isinstance(marks, dict) or marks.get("mode") != "cells":
        return ["die.corner_marks.mode must be 'cells' with a per-corner cell mapping."]
    corners = marks.get("corners")
    if not isinstance(corners, dict):
        return ["die.corner_marks.corners must map leftdown/leftup/rightdown/rightup to mark cells."]
    problems = []
    missing = [key for key in CORNER_KEYS if key not in corners]
    if missing:
        problems.append(f"die.corner_marks.corners is missing: {', '.join(missing)}.")
    values = [str(corners.get(key, "")).strip() for key in CORNER_KEYS]
    if any(not value for value in values):
        problems.append("die.corner_marks.corners values must be non-empty cell names.")
    allow_identical = bool(marks.get("allow_identical_corners", die.allow_identical_corners))
    if not allow_identical and len(set(values)) != len(values):
        problems.append(
            "die.corner_marks.corners must use four distinct corner mark cells; set allow_identical_corners=True only for an explicit non-production exception."
        )
    return problems


def _validate_global_marks(die: DieTemplate) -> list[str]:
    problems = []
    for i, mark in enumerate(die.global_marks):
        if not str(mark.get("cell", "")).strip():
            problems.append(f"die.global_marks[{i}].cell must name an existing mark cell.")
        pos = mark.get("position_um")
        if not _is_pair(pos):
            problems.append(f"die.global_marks[{i}].position_um must be [x, y].")
        elif not _within_die(pos, die.die_size_um):
            problems.append(
                f"die.global_marks[{i}].position_um={list(pos)} is outside die_size_um {list(die.die_size_um)}; move it inside the die frame."
            )
    return problems


def _validate_labels(die: DieTemplate) -> list[str]:
    problems = []
    for i, label in enumerate(die.labels):
        if "text" not in label:
            problems.append(f"die.labels[{i}].text is required.")
        if not _is_pair(label.get("position_um")):
            problems.append(f"die.labels[{i}].position_um must be [x, y].")
        else:
            if not _within_die(label["position_um"], die.die_size_um):
                problems.append(f"die.labels[{i}].position_um is outside the die frame.")
        try:
            parse_layer(str(label.get("layer", "")))
        except Exception:
            problems.append(f"die.labels[{i}].layer must be a valid layer string like '101/0'.")
        if float(label.get("size_um", 0.0)) <= 0:
            problems.append(f"die.labels[{i}].size_um must be positive.")
    return problems


def _validate_wafer(wafer: WaferTemplate) -> list[str]:
    problems = []
    if wafer.mode == "grid":
        if wafer.region_bbox_um is None and wafer.wafer_size_um is None:
            problems.append("wafer.mode='grid' requires wafer.region_bbox_um or wafer.wafer_size_um.")
    if wafer.mode == "circular":
        if wafer.wafer_diameter_um is None or float(wafer.wafer_diameter_um) <= 0:
            problems.append("wafer.mode='circular' requires positive wafer_diameter_um.")
    if float(wafer.exclusion_margin_um) < 0:
        problems.append("wafer.exclusion_margin_um must be non-negative.")
    numbering = wafer.numbering or {}
    cells = numbering.get("cells", {})
    if cells and not isinstance(cells, dict):
        problems.append("wafer.numbering.cells must map digit/index names to existing cell names.")
    positions = numbering.get("positions_um", [])
    if positions != "per_site":
        if not isinstance(positions, list):
            problems.append("wafer.numbering.positions_um must be a list of [x, y] positions or 'per_site'.")
        else:
            for i, pos in enumerate(positions):
                if not _is_pair(pos):
                    problems.append(f"wafer.numbering.positions_um[{i}] must be [x, y].")
    return problems


def _validate_against_layout(
    payload_cell: str,
    die_cell: str,
    wafer_cell: str,
    die: DieTemplate,
    wafer: WaferTemplate,
    existing_cells: set[str],
) -> list[str]:
    required = {str(payload_cell), str(die.bonding["cell"])}
    required.update(str(die.corner_marks["corners"][key]) for key in CORNER_KEYS)
    required.update(str(mark["cell"]) for mark in die.global_marks)
    numbering_cells = {str(cell) for cell in (wafer.numbering or {}).get("cells", {}).values()}
    required.update(numbering_cells)
    missing = sorted(cell for cell in required if cell not in existing_cells)
    problems = [f"missing cell {cell!r}; create/import it before calling compose_complete_mask." for cell in missing]
    for cell in (die_cell, wafer_cell):
        if cell in existing_cells:
            problems.append(f"created cell {cell!r} already exists; choose a different name prefix.")
    return problems


def _build_die(payload_cell: str, die: DieTemplate, die_cell: str) -> tuple[list[dict], list[dict], list[dict]]:
    instances = []
    shapes = []
    placements = []

    def add_instance(child: str, pos: Sequence[float], source: str, rotation: float = 0.0) -> None:
        item = _instance_item(child, pos, rotation)
        instances.append(item)
        placements.append(_placement(die_cell, child, pos, rotation, source))

    add_instance(payload_cell, die.payload_position_um, "die.payload_position_um")
    for i, pos in enumerate(die.bonding["positions_um"]):
        add_instance(str(die.bonding["cell"]), pos, f"die.bonding.positions_um[{i}]")

    corner_positions = _corner_positions(die.die_size_um)
    for corner in CORNER_KEYS:
        add_instance(str(die.corner_marks["corners"][corner]), corner_positions[corner], f"die.corner_marks.corners.{corner}")

    for i, mark in enumerate(die.global_marks):
        add_instance(str(mark["cell"]), mark["position_um"], f"die.global_marks[{i}]")

    for i, label in enumerate(die.labels):
        layer, datatype = parse_layer(str(label["layer"]))
        pos = _pair(label["position_um"])
        shapes.append(
            {
                "kind": "text",
                "layer": layer,
                "datatype": datatype,
                "text": str(label["text"]),
                "position_um": [pos[0], pos[1]],
                "size_um": float(label["size_um"]),
            }
        )
        placements.append(
            {
                "parent": die_cell,
                "cell": None,
                "position_um": [pos[0], pos[1]],
                "rotation": 0.0,
                "source_template_field": f"die.labels[{i}]",
                "kind": "text",
                "text": str(label["text"]),
                "layer": f"{layer}/{datatype}",
            }
        )
    _validate_point_collisions(placements)
    return instances, shapes, placements


def _build_wafer_sites(wafer: WaferTemplate, die: DieTemplate) -> list[Site]:
    if wafer.mode == "single":
        x, y = _pair(wafer.position_um)
        return [Site("0", 0, 0, (x, y), float(wafer.rotation_deg))]
    pitch_x, pitch_y = _pitch(wafer, die)
    if wafer.mode == "grid":
        if wafer.region_bbox_um is not None:
            region = list(wafer.region_bbox_um)
        else:
            width, height = _pair(wafer.wafer_size_um)
            region = [0.0, 0.0, width, height]
        return generate_grid_sites(
            region,
            pitch_x,
            pitch_y,
            margin_um=float(wafer.exclusion_margin_um),
            rotation_deg=float(wafer.rotation_deg),
        )
    return generate_circular_die_sites(
        wafer.center_um,
        float(wafer.wafer_diameter_um),
        pitch_x,
        pitch_y,
        float(wafer.exclusion_margin_um),
        footprint_um=die.die_size_um,
    )


def _build_wafer(die_cell: str, wafer: WaferTemplate, sites: list[Site], wafer_cell: str) -> tuple[list[dict], list[dict]]:
    instances = []
    placements = []
    for i, site in enumerate(sites):
        instances.append(_instance_item(die_cell, site.center_um, site.rotation_deg))
        placements.append(_placement(wafer_cell, die_cell, site.center_um, site.rotation_deg, f"wafer.sites[{i}]"))

    numbering = wafer.numbering or {}
    cells = {str(key): str(value) for key, value in numbering.get("cells", {}).items()}
    positions = numbering.get("positions_um", [])
    if cells and positions == "per_site":
        for i, site in enumerate(sites):
            key = str(site.site_id)
            child = cells.get(key)
            if child is None:
                continue
            instances.append(_instance_item(child, site.center_um, 0.0))
            placements.append(_placement(wafer_cell, child, site.center_um, 0.0, f"wafer.numbering.per_site[{key}]"))
    elif cells and isinstance(positions, list):
        ordered = sorted(cells.items(), key=lambda item: item[0])
        for i, pos in enumerate(positions):
            if i >= len(ordered):
                break
            key, child = ordered[i]
            instances.append(_instance_item(child, pos, 0.0))
            placements.append(_placement(wafer_cell, child, pos, 0.0, f"wafer.numbering.positions_um[{i}]/{key}"))
    return instances, placements


def _existing_cells(client) -> set[str]:
    result = client.cell_list(limit=5000)
    return {str(cell["name"]) for cell in result.get("cells", [])}


def _create_exact_cell(client, cell: str) -> str:
    result = client.cell_create(cell)
    effective = str(result.get("name", cell)) if isinstance(result, dict) else cell
    if effective != cell:
        _delete_cell_best_effort(client, effective)
        raise RuntimeError(f"cell {cell!r} was auto-renamed to {effective!r}; choose a different name prefix.")
    return effective


def _instance_item(child: str, pos: Sequence[float], rotation: float) -> dict:
    x, y = _pair(pos)
    return {
        "child": str(child),
        "position_um": [x, y],
        "rotation": float(rotation),
        "mirror": False,
        "magnification": 1.0,
    }


def _placement(parent: str, cell: str, pos: Sequence[float], rotation: float, source: str) -> dict:
    x, y = _pair(pos)
    return {
        "parent": str(parent),
        "cell": str(cell),
        "position_um": [x, y],
        "rotation": float(rotation),
        "source_template_field": str(source),
        "kind": "cell_instance",
    }


def _facts_payload(
    *,
    payload_cell: str,
    name: str,
    die: DieTemplate,
    wafer: WaferTemplate,
    die_cell: str,
    wafer_cell: str,
    placements: list[dict],
    wafer_sites: list[Site],
) -> dict:
    return {
        "tool_version": TOOL_VERSION,
        "name": str(name),
        "payload_cell": str(payload_cell),
        "die_cell": die_cell,
        "wafer_cell": wafer_cell,
        "die_template": _jsonable(asdict(die)),
        "wafer_template": _jsonable(asdict(wafer)),
        "wafer_sites": [site.to_dict() for site in wafer_sites],
        "placements": sorted(
            placements,
            key=lambda item: (
                item["parent"],
                str(item.get("cell")),
                item["source_template_field"],
                item["position_um"],
                item["kind"],
            ),
        ),
        "honesty": "Facts record composer parameters, assumptions, sources, and placements; they are not fabrication validity claims.",
    }


def _validate_point_collisions(placements: list[dict]) -> None:
    seen: dict[tuple[str, float, float], dict] = {}
    for placement in placements:
        if placement["kind"] != "cell_instance":
            continue
        key = (placement["parent"], placement["position_um"][0], placement["position_um"][1])
        prior = seen.get(key)
        if prior is not None:
            raise ValueError(
                "colliding placement positions in die template: "
                f"{prior['source_template_field']} and {placement['source_template_field']} both place at {placement['position_um']}; move one position."
            )
        seen[key] = placement


def _corner_positions(die_size_um: Sequence[float]) -> dict[str, tuple[float, float]]:
    w, h = _pair(die_size_um)
    return {
        "leftdown": (0.0, 0.0),
        "leftup": (0.0, h),
        "rightdown": (w, 0.0),
        "rightup": (w, h),
    }


def _pitch(wafer: WaferTemplate, die: DieTemplate) -> tuple[float, float]:
    if wafer.die_pitch_um is not None:
        return _pair(wafer.die_pitch_um)
    return _pair(die.die_size_um)


def _pair(value: Sequence[float] | None) -> tuple[float, float]:
    if value is None or len(value) != 2:  # type: ignore[arg-type]
        raise ValueError("expected [x, y]")
    return (float(value[0]), float(value[1]))


def _is_pair(value: Any) -> bool:
    try:
        _pair(value)
        return True
    except Exception:
        return False


def _within_die(pos: Sequence[float], die_size_um: Sequence[float]) -> bool:
    x, y = _pair(pos)
    w, h = _pair(die_size_um)
    eps = 1e-12
    return -eps <= x <= w + eps and -eps <= y <= h + eps


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(value[key]) for key in sorted(value)}
    return value


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _delete_cell_best_effort(client, cell: str) -> None:
    try:
        client.cell_delete(cell, recursive=True)
    except Exception:
        pass


def _delete_path(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _failure(
    reason: str,
    die_cell: str,
    wafer_cell: str,
    problems: list[str],
    next_action: str | None = None,
) -> dict:
    return {
        "ok": False,
        "die_cell": die_cell,
        "wafer_cell": wafer_cell,
        "placements": [],
        "facts_path": None,
        "problems": problems,
        "reason": reason,
        "next_action": next_action or "Fix the named template/cell issue exactly as described and call compose_complete_mask again.",
    }


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

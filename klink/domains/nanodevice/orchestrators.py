"""One-call nanodevice domain orchestrators for agent-facing tools."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .devices.hallbar import HallBarSpec
from .flake import (
    build_klayoutclaw_detections_manifest,
    layout_payload_from_traces,
    run_klayoutclaw_stage_script,
    run_klayoutclaw_transform,
    trace_bundle_from_path,
    trace_material_summary,
)
from .pipeline import build_hallbar_bundle, route_hallbar_offline


DEFAULT_STATE_DIR = Path(".klink") / "nanodevice"
DEFAULT_HALLBAR_CELL = "NANODEVICE_HALLBAR_AGENT"
DEFAULT_FLAKE_CELL = "NANODEVICE_FLAKE_AGENT"


def build_and_route_hallbar(
    client,
    *,
    cell: str = DEFAULT_HALLBAR_CELL,
    spec: HallBarSpec | dict | None = None,
    writefield: dict | None = None,
    state_dir: str | Path = DEFAULT_STATE_DIR,
    route_layer: str | None = None,   # None -> use the spec's (required) route_layer
    spacing_um: float = 4.0,
    keep: bool = True,
    show: bool = True,
    dry_run: bool = False,
) -> dict:
    """Build, route, validate, commit, and persist one Hall bar workflow.

    The function validates all pure layout/routing state before touching the
    live KLayout client.  It only writes the state manifest after live commit
    succeeds.
    """

    timings: dict[str, float] = {}
    problems = _validate_cell_name(cell)
    if problems:
        return _failure("invalid_input", problems)

    try:
        hallbar_spec = _coerce_hallbar_spec(spec)
        route_layer = route_layer or hallbar_spec.route_layer
        bundle = _phase(timings, "build_hallbar_bundle", lambda: build_hallbar_bundle(hallbar_spec, writefield=writefield))
        route_result = _phase(timings, "route_plan", lambda: route_hallbar_offline(bundle, spacing_um=spacing_um))
    except Exception as exc:
        return _failure(
            "prepare_failed",
            [f"{type(exc).__name__}: {exc}"],
            "Fix the HallBarSpec/writefield parameters and call nanodevice.build_and_route_hallbar again.",
            timings,
        )

    validation = _hallbar_validation(route_result)
    if not validation["ok"]:
        return _failure(
            "route_validation_failed",
            validation["problems"],
            "Adjust the Hall bar spec, writefield, or spacing_um until routing reports zero errors, overlaps, and wall crossings; then call this again.",
            timings,
            extra={"routing": _route_summary(route_result), "layout": bundle.get("report", {})},
        )

    if dry_run:
        return {
            "ok": True,
            "committed": False,
            "dry_run": True,
            "cell": cell,
            "layout": bundle.get("report", {}),
            "routing": _route_summary(route_result),
            "timings_s": timings,
            "problems": [],
            "next_action": "Dry run passed. Call again with dry_run=false to write the Hall bar into KLayout.",
        }

    from klink.routing.backends.geometric.tapered_segments import commit_tapered_hybrid_many

    live = None
    created = False
    try:
        _phase(timings, "delete_old_cell", lambda: _delete_cell(client, cell))
        _phase(timings, "cell_create", lambda: client.cell_create(cell))
        created = True
        _phase(timings, "ensure_layers", lambda: _ensure_item_layers(client, bundle["shape_items"]))
        inserted = _phase(timings, "shape_insert_many", lambda: client.shape_insert_many(cell, bundle["shape_items"]))
        obstacles = bundle.get("obstacle_boxes_um") or []
        _phase(timings, "obstacle_insert", lambda: _insert_obstacles(client, cell, obstacles))
        _phase(timings, "port_mark", lambda: _mark_ports(client, cell, bundle["port_marks"]))
        _phase(timings, "anchor_mark", lambda: _mark_anchors(client, cell, bundle["anchor_marks"]))
        write = _phase(
            timings,
            "route_commit",
            lambda: commit_tapered_hybrid_many(client, cell, route_result, route_layer=route_layer, clear=True),
        )
        if show:
            _phase(timings, "show_cell", lambda: client.show_cell(cell, zoom_fit=True))
        live = {"inserted": inserted, "write": write}
    except Exception as exc:
        if created and not keep:
            _cleanup_after_failure(client, cell)
        return _failure(
            "live_commit_failed",
            [f"{type(exc).__name__}: {exc}"],
            "The pure Hall bar plan was valid but KLayout writeback failed. Check the live KLayout session, reconnect if needed, and call this again.",
            timings,
            extra={"cell": cell},
        )

    state = {
        "kind": "nanodevice.hallbar",
        "cell": cell,
        "spec": _dataclass_or_dict(hallbar_spec),
        "writefield": bundle.get("writefield"),
        "layout": bundle.get("report", {}),
        "routing": _route_summary(route_result),
        "live": live,
        "updated_at": _utc_timestamp(),
    }
    state_path = _write_state(state_dir, cell, "hallbar", state)
    if not keep:
        _delete_cell(client, cell)
    return {
        "ok": True,
        "committed": True,
        "cell": cell,
        "state_path": str(state_path),
        "layout": state["layout"],
        "routing": state["routing"],
        "live": live,
        "timings_s": timings,
        "problems": [],
        "next_action": f"Hall bar committed to {cell}. State was written to {state_path}.",
    }


def detect_and_commit(
    client,
    *,
    cell: str = DEFAULT_FLAKE_CELL,
    traces_path: str | Path | None = None,
    root: str | Path | None = None,
    stage_inputs: dict[str, dict] | None = None,
    align_dir: str | Path | None = None,
    image: str | Path | None = None,
    pixel_size_um: float | None = None,   # required with image; your microscope's um/pixel
    output_dir: str | Path = Path("test_outputs") / "nanodevice_agent",
    stage_cache_dir: str | Path | None = Path("test_outputs") / "klayoutclaw_stage_cache",
    coordinate: str = "um",
    state_dir: str | Path = DEFAULT_STATE_DIR,
    keep: bool = True,
    show: bool = True,
    dry_run: bool = False,
) -> dict:
    """Detect or load flake traces, commit polygons, and persist state."""

    timings: dict[str, float] = {}
    problems = _validate_cell_name(cell)
    if coordinate not in {"um", "gds"}:
        problems.append("coordinate must be 'um' or 'gds'.")
    if image is not None and pixel_size_um is None:
        problems.append(
            "image detection needs pixel_size_um (your microscope's um/pixel); "
            "klink ships no default.")
    if problems:
        return _failure("invalid_input", problems)

    try:
        prepared = _phase(
            timings,
            "prepare_traces",
            lambda: _prepare_traces(
                traces_path=traces_path,
                root=root,
                stage_inputs=stage_inputs,
                align_dir=align_dir,
                image=image,
                pixel_size_um=pixel_size_um,
                output_dir=output_dir,
                stage_cache_dir=stage_cache_dir,
            ),
        )
        traces_file = Path(prepared["traces_path"])
        traces = json.loads(traces_file.read_text(encoding="utf-8"))
        payload = _phase(
            timings,
            "layout_payload_from_traces",
            lambda: layout_payload_from_traces(traces, source_path=traces_file, coordinate=coordinate),
        )
        bundle = trace_bundle_from_path(traces_file)
    except Exception as exc:
        return _failure(
            "prepare_failed",
            [f"{type(exc).__name__}: {exc}"],
            "Provide either an existing traces_path or root/stage_inputs/align_dir/image for a full KlayoutClaw stage pipeline, then call nanodevice.detect_and_commit again.",
            timings,
        )

    if payload.shape_item_count <= 0:
        return _failure(
            "empty_layout_payload",
            [f"{traces_file} produced zero polygon shape_items."],
            "Inspect the traces.json materials/layer_map and rerun detection before calling this again.",
            timings,
            extra={"traces_path": str(traces_file)},
        )

    if dry_run:
        return {
            "ok": True,
            "committed": False,
            "dry_run": True,
            "cell": cell,
            "traces_path": str(traces_file),
            "layout_payload": payload.to_dict(),
            "summary": bundle.summary,
            "timings_s": timings,
            "problems": [],
            "next_action": "Dry run passed. Call again with dry_run=false to write the flake polygons into KLayout.",
        }

    live = None
    created = False
    try:
        _phase(timings, "delete_old_cell", lambda: _delete_cell(client, cell))
        _phase(timings, "cell_create", lambda: client.cell_create(cell))
        created = True
        _phase(timings, "ensure_layers", lambda: _ensure_item_layers(client, payload.shape_items))
        inserted = _phase(timings, "shape_insert_many", lambda: client.shape_insert_many(cell, payload.shape_items))
        if show:
            _phase(timings, "show_cell", lambda: client.show_cell(cell, zoom_fit=True))
        live = {"inserted": inserted}
    except Exception as exc:
        if created and not keep:
            _cleanup_after_failure(client, cell)
        return _failure(
            "live_commit_failed",
            [f"{type(exc).__name__}: {exc}"],
            "The flake payload was valid but KLayout writeback failed. Check the live KLayout session, reconnect if needed, and call this again.",
            timings,
            extra={"cell": cell, "traces_path": str(traces_file)},
        )

    state = {
        "kind": "nanodevice.flake",
        "cell": cell,
        "traces_path": str(traces_file),
        "coordinate": coordinate,
        "summary": trace_material_summary(traces),
        "trace_bundle": bundle.to_dict(),
        "layout_payload": payload.to_dict(),
        "pipeline": prepared,
        "live": live,
        "updated_at": _utc_timestamp(),
    }
    state_path = _write_state(state_dir, cell, "flake", state)
    if not keep:
        _delete_cell(client, cell)
    return {
        "ok": True,
        "committed": True,
        "cell": cell,
        "state_path": str(state_path),
        "traces_path": str(traces_file),
        "summary": state["summary"],
        "layout_payload": payload.to_dict(),
        "live": live,
        "timings_s": timings,
        "problems": [],
        "next_action": f"Flake polygons committed to {cell}. State was written to {state_path}.",
    }


def _prepare_traces(
    *,
    traces_path: str | Path | None,
    root: str | Path | None,
    stage_inputs: dict[str, dict] | None,
    align_dir: str | Path | None,
    image: str | Path | None,
    pixel_size_um: float,
    output_dir: str | Path,
    stage_cache_dir: str | Path | None,
) -> dict:
    if traces_path is not None:
        path = Path(traces_path)
        if not path.exists():
            raise FileNotFoundError(path)
        return {"mode": "traces_path", "traces_path": str(path)}
    missing = []
    if root is None:
        missing.append("root")
    if not stage_inputs:
        missing.append("stage_inputs")
    if align_dir is None:
        missing.append("align_dir")
    if image is None:
        missing.append("image")
    if missing:
        raise ValueError(f"missing required full-pipeline parameter(s): {', '.join(missing)}")

    out = Path(output_dir)
    detect_dir = out / "detect"
    combine_dir = out / "combine"
    stage_outputs = {}
    for material, kwargs in stage_inputs.items():
        args = dict(kwargs)
        result = run_klayoutclaw_stage_script(
            root,
            material,
            pixel_size_um=pixel_size_um,
            output_dir=detect_dir,
            cache_dir=stage_cache_dir,
            **args,
        )
        stage_outputs[material] = result.get("stage_artifact") or result.get("stage", {})
    manifest = build_klayoutclaw_detections_manifest(detect_dir, pixel_size_um=pixel_size_um)
    transform = run_klayoutclaw_transform(
        root,
        detections=manifest.path,
        align_dir=align_dir,
        image=image,
        pixel_size_um=pixel_size_um,
        output_dir=combine_dir,
    )
    return {
        "mode": "stage_pipeline",
        "stage_outputs": stage_outputs,
        "detections_manifest": manifest.to_dict(),
        "transform": transform.get("transform_artifact") or transform,
        "traces_path": transform["traces_path"],
    }


def _validate_cell_name(cell: str) -> list[str]:
    if not str(cell).strip():
        return ["cell must be a non-empty string."]
    return []


def _coerce_hallbar_spec(spec: HallBarSpec | dict | None) -> HallBarSpec:
    if spec is None:
        return HallBarSpec()
    if isinstance(spec, HallBarSpec):
        return spec
    return HallBarSpec(**dict(spec))


def _hallbar_validation(route_result: dict) -> dict:
    problems = []
    if not route_result.get("ok"):
        errors = route_result.get("errors") or ["route_result ok=false"]
        problems.extend(str(e) for e in errors)
    if route_result.get("sibling_overlaps"):
        problems.append(f"{len(route_result['sibling_overlaps'])} sibling route overlap(s) detected.")
    if route_result.get("obstacle_hits"):
        problems.append(f"{len(route_result['obstacle_hits'])} writefield/obstacle crossing(s) detected.")
    return {"ok": not problems, "problems": problems}


def _route_summary(route_result: dict) -> dict:
    return {
        "backend": route_result.get("backend"),
        "ok": bool(route_result.get("ok")),
        "route_count": int(route_result.get("route_count", 0)),
        "errors": list(route_result.get("errors") or []),
        "sibling_overlaps": len(route_result.get("sibling_overlaps") or []),
        "obstacle_hits": len(route_result.get("obstacle_hits") or []),
        "writefield_wall_crossings": len(route_result.get("obstacle_hits") or []),
    }


def _ensure_item_layers(client, items: list[dict]) -> None:
    seen: set[tuple[int, int]] = set()
    for item in items:
        key = (int(item["layer"]), int(item.get("datatype", 0)))
        if key in seen:
            continue
        seen.add(key)
        client.layer_ensure(key[0], key[1], name=f"NANODEVICE_{key[0]}_{key[1]}")


def _insert_obstacles(client, cell: str, obstacles: list[list[float]]) -> None:
    if not obstacles:
        return
    client.layer_ensure(900, 0, name="KLINK_WF_KEEPOUT")
    client.shape_insert_boxes(cell, layer=900, datatype=0, boxes_um=obstacles)


def _mark_ports(client, cell: str, ports: list[dict]) -> None:
    for port in ports:
        payload = dict(port)
        payload["cell"] = cell
        client.call("port.mark", payload)


def _mark_anchors(client, cell: str, anchors: list[dict]) -> None:
    for anchor in anchors:
        payload = dict(anchor)
        payload["cell"] = cell
        client.call("anchor.mark", payload)


def _delete_cell(client, cell: str) -> None:
    try:
        client.cell_delete(cell, recursive=True)
    except Exception:
        pass


def _cleanup_after_failure(client, cell: str) -> None:
    try:
        _delete_cell(client, cell)
    except Exception:
        pass


def _write_state(state_dir: str | Path, cell: str, suffix: str, payload: dict) -> Path:
    root = Path(state_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{_safe_name(cell)}.{suffix}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in value)


def _dataclass_or_dict(value: Any) -> dict:
    if is_dataclass(value):
        return asdict(value)
    return dict(value)


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
        "next_action": next_action or "Read the problems list, fix the inputs exactly as described, and call this tool again.",
    }
    if timings is not None:
        payload["timings_s"] = timings
    if extra:
        payload.update(extra)
    return payload


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

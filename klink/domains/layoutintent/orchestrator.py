"""prepare / apply / regenerate orchestration for array_labeled intents.

Two-phase protocol (docs/REGION_INTENT_DESIGN.md §9): ``prepare`` is pure
analysis + candidate (nothing written, no Run recorded); ``apply`` consumes
a cached plan by plan_id + plan_hash + confirm, creates the Run (WAL-lite:
``prepared`` lands before any mutation) and commits through the single
plugin primitive ``intent.apply_managed_plan``.

The plan cache is process-local by design: a plan is a one-shot candidate
bound to the live layout state; restarting the MCP process simply requires
a fresh prepare.
"""

from __future__ import annotations

from .planner import PlanError, plan_array_labeled
from .store import LayoutIntentStore

_PLAN_CACHE: dict[str, dict] = {}
_PLAN_CACHE_MAX = 32


class OrchestratorError(RuntimeError):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


def _source_cell_bbox(client, cell_name: str) -> list[int]:
    listing = client.call("cell.list", {"with_bbox": True, "limit": 5000})
    for entry in listing.get("cells", []):
        if entry.get("name") == cell_name:
            bbox = entry.get("bbox_dbu")
            if not bbox:
                raise OrchestratorError(
                    "source cell %r has no geometry (empty bbox)" % cell_name,
                    hint="draw or import the source cell first")
            return [int(v) for v in bbox]
    raise OrchestratorError(
        "source cell %r not found" % cell_name,
        hint="cell.list shows the available cells")


def prepare(
    client,
    store: LayoutIntentStore,
    *,
    region: str,
    source_cell: str,
    pitch_um,
    numbering: dict,
    label: dict,
    obstacle_layers: list[str] | None = None,
    obstacle_cells: list[str] | None = None,
    extra_obstacles_um: list | None = None,
    clearance_um: float = 0.0,
    rotation_deg: int = 0,
    mirror: bool = False,
    allow_empty_obstacles: bool = False,
    intent_id: str | None = None,
    instruction: str = "",
) -> dict:
    obstacle_layers = list(obstacle_layers or [])
    obstacle_cells = list(obstacle_cells or [])
    extra_obstacles_um = list(extra_obstacles_um or [])
    if not (obstacle_layers or obstacle_cells or extra_obstacles_um
            or allow_empty_obstacles):
        raise OrchestratorError(
            "no obstacle source given — the plan would place on top of "
            "ANYTHING already in the region",
            hint="pass obstacle_layers (your design layers 'L/D') and/or "
                 "obstacle_cells (device/blackbox cell names) and/or "
                 "extra_obstacles_um; for a genuinely empty region pass "
                 "allow_empty_obstacles=true explicitly")
    got = client.call("region.get", {"name": region})
    # an intent's own previous output must not count as its own obstacle
    exclude_cells = []
    if intent_id:
        prior_intent = store.read_intent(intent_id)
        if prior_intent and (prior_intent.get("output") or {}).get("container_cell"):
            exclude_cells = [prior_intent["output"]["container_cell"]]
    occ = client.call("region.occupancy",
                      {"name": region, "layers": obstacle_layers,
                       "obstacle_cells": obstacle_cells,
                       "exclude_cells": exclude_cells})
    if occ.get("truncated"):
        raise OrchestratorError(
            "occupancy query truncated; the region is too busy to plan "
            "safely", hint="shrink the region or reduce obstacle layers")

    dbu = float(occ["dbu_um"])
    obstacles = []
    for layer_entry in occ.get("layers", []):
        obstacles.extend(layer_entry.get("polygons", []))
    if occ.get("obstacle_cells"):
        obstacles.extend(occ["obstacle_cells"].get("polygons", []))
    # user-supplied free-form obstacle polygons (um -> dbu)
    for poly_um in extra_obstacles_um:
        obstacles.append({
            "hull_dbu": [[int(round(float(x) / dbu)),
                          int(round(float(y) / dbu))] for x, y in poly_um],
            "holes_dbu": [],
        })

    footprint = _source_cell_bbox(client, source_cell)

    # label SLOT mode: the user circled a text slot INSIDE the unit cell
    # (a Region claimed in the SOURCE cell). Resolve it live each prepare,
    # so dragging the slot and regenerating moves every number.
    planner_label = dict(label)
    if "slot_region" in planner_label:
        slot = client.call("region.get",
                           {"name": str(planner_label.pop("slot_region"))})
        if slot["cell"] != source_cell:
            raise OrchestratorError(
                "label slot region %r lives in cell %r, not in the source "
                "cell %r" % (slot["name"], slot["cell"], source_cell),
                hint="circle the text slot INSIDE the unit cell (open the "
                     "source cell, drag a ruler, region.claim {cell: '%s'})"
                % source_cell)
        planner_label["slot_bbox_dbu"] = slot["bbox_dbu"]

    try:
        plan = plan_array_labeled(
            region_hull_dbu=got["hull_dbu"],
            region_holes_dbu=got.get("holes_dbu", []),
            dbu_um=dbu,
            obstacles_dbu=obstacles,
            source_cell=source_cell,
            footprint_bbox_dbu=footprint,
            pitch_um=pitch_um,
            numbering=numbering,
            label=planner_label,
            rotation_deg=rotation_deg,
            mirror=mirror,
            clearance_um=clearance_um,
        )
    except PlanError as exc:
        raise OrchestratorError(str(exc), hint=exc.hint)

    # intent record (revisioned; regenerate reuses the id)
    if intent_id:
        intent = store.read_intent(intent_id)
        if intent is None:
            raise OrchestratorError(
                "unknown intent_id %r" % intent_id,
                hint="intent.list shows the stored intents")
        intent["revision"] = int(intent.get("revision", 1)) + 1
    else:
        intent = {
            "schema_version": "klink_layout_intent_v2",
            "intent_id": store.new_id("int"),
            "revision": 1,
            "output": None,
        }
    intent.update({
        "region": region,
        "instruction": instruction,
        "executor": {"name": "klink.array_labeled", "version": 2},
        "parameters": {
            "source_cell": source_cell,
            "obstacle_layers": obstacle_layers,
            "obstacle_cells": obstacle_cells,
            "extra_obstacles_um": extra_obstacles_um,
            "clearance_um": float(clearance_um),
            "rotation_deg": int(rotation_deg),
            "mirror": bool(mirror),
            "allow_empty_obstacles": bool(allow_empty_obstacles),
            "pitch_um": list(pitch_um),
            "numbering": dict(numbering),
            "label": dict(label),
        },
    })
    store.write_intent(intent)

    plan_id = store.new_id("plan")
    if len(_PLAN_CACHE) >= _PLAN_CACHE_MAX:
        _PLAN_CACHE.pop(next(iter(_PLAN_CACHE)))
    _PLAN_CACHE[plan_id] = {
        "intent_id": intent["intent_id"],
        "intent_revision": intent["revision"],
        "region": region,
        "obstacle_layers": obstacle_layers,
        "obstacle_cells": obstacle_cells,
        "exclude_cells": exclude_cells,
        "scope_hash": occ["scope_hash"],
        "target_cell": got["cell"],
        "plan": plan,
    }

    preview = {
        "plan_id": plan_id,
        "plan_hash": plan.plan_hash,
        "intent_id": intent["intent_id"],
        "intent_revision": intent["revision"],
        "region": region,
        "target_cell": got["cell"],
        "rows": plan.rows,
        "cols": plan.cols,
        "placed": len(plan.instances),
        "labels": [i["site_id"] for i in plan.instances][:8],
        "label_range": (
            [plan.instances[0]["site_id"], plan.instances[-1]["site_id"]]
            if plan.instances else []),
        "rejected": len(plan.rejected),
        "rejected_reasons": _reason_counts(plan.rejected),
        "label_height_um": round(plan.label_height_um, 4),
        "free_area_um2": occ.get("free_area_um2"),
        "problems": list(plan.problems),
    }
    if plan.problems:
        preview["next_action"] = (
            "plan has blocking problems; fix the parameters and re-run "
            "intent.prepare")
    elif not plan.instances:
        preview["next_action"] = (
            "nothing fits: %s — enlarge the region, reduce pitch, or check "
            "obstacle layers" % preview["rejected_reasons"])
    else:
        preview["next_action"] = (
            "review the counts, then intent.apply {plan_id: '%s', "
            "plan_hash: '%s', confirm: '%s'}"
            % (plan_id, plan.plan_hash, plan_id))
    return preview


def _reason_counts(rejected: list) -> dict:
    counts: dict[str, int] = {}
    for item in rejected:
        counts[item["reason"]] = counts.get(item["reason"], 0) + 1
    return counts


def apply(client, store: LayoutIntentStore, *, plan_id: str, plan_hash: str,
          confirm: str) -> dict:
    entry = _PLAN_CACHE.get(plan_id)
    if entry is None:
        raise OrchestratorError(
            "unknown or expired plan_id %r" % plan_id,
            hint="plans are one-shot and process-local; re-run "
                 "intent.prepare")
    plan = entry["plan"]
    if confirm != plan_id:
        raise OrchestratorError(
            "confirm must equal the plan_id",
            hint="pass confirm='%s' to acknowledge the previewed plan"
                 % plan_id)
    if plan_hash != plan.plan_hash:
        raise OrchestratorError(
            "plan_hash mismatch",
            hint="pass the plan_hash returned by intent.prepare verbatim")
    if plan.problems:
        raise OrchestratorError(
            "plan has blocking problems: %s" % "; ".join(plan.problems),
            hint="fix the parameters and re-run intent.prepare")
    if not plan.instances:
        raise OrchestratorError(
            "plan places nothing; refusing an empty apply",
            hint="see the prepare preview's rejected_reasons")

    intent = store.read_intent(entry["intent_id"])
    if intent is None:
        raise OrchestratorError("intent record vanished; re-run prepare")

    prior = intent.get("output") or None
    applied_seq = int(intent.get("applied_seq", 0)) + 1
    container = "KLINK_I_%s_r%d" % (
        intent["intent_id"].split("_", 1)[1], applied_seq)
    root_klink_id = "intent:%s:root" % intent["intent_id"]

    mode = "create"
    request = {
        "mode": mode,
        "parent_cell": entry["target_cell"],
        "container_cell": container,
        "root_klink_id": root_klink_id,
        "scope_check": {
            "region_name": entry["region"],
            "layers": entry["obstacle_layers"],
            "obstacle_cells": entry.get("obstacle_cells", []),
            "exclude_cells": entry.get("exclude_cells", []),
            "expected_hash": entry["scope_hash"],
        },
        "payload": plan.payload(),
        "plan_hash": plan.plan_hash,
    }
    if prior:
        mode = "replace"
        request["mode"] = mode
        request["expected_root_klink_id"] = prior["root_klink_id"]
        request["expected_managed_digest"] = prior["managed_digest"]

    run_id = store.new_id("run")
    store.append_run_event(run_id, {
        "schema_version": "klink_intent_run_event_v2",
        "op": "prepared",
        "intent_id": intent["intent_id"],
        "intent_revision": entry["intent_revision"],
        "plan_id": plan_id,
        "plan_hash": plan.plan_hash,
        "mode": mode,
        "container_cell": container,
    })

    try:
        result = client.call("intent.apply_managed_plan", request)
    except Exception as exc:
        store.append_run_event(run_id, {"op": "failed", "error": str(exc)})
        raise

    store.append_run_event(run_id, {
        "op": "applied",
        "container_cell": result["container_cell"],
        "managed_digest": result["managed_digest"],
        "replaced_container": result.get("replaced_container"),
        "instances": result["instances_written"],
        "shapes": result["shapes_written"],
    })

    intent["applied_seq"] = applied_seq
    intent["output"] = {
        "root_klink_id": root_klink_id,
        "container_cell": result["container_cell"],
        "managed_digest": result["managed_digest"],
        "run_id": run_id,
    }
    store.write_intent(intent)
    _PLAN_CACHE.pop(plan_id, None)

    return {
        "run_id": run_id,
        "intent_id": intent["intent_id"],
        "mode": mode,
        "container_cell": result["container_cell"],
        "replaced_container": result.get("replaced_container"),
        "instances": result["instances_written"],
        "label_shapes": result["shapes_written"],
        "managed_digest": result["managed_digest"],
        "next_action": "one Ctrl+Z in KLayout undoes the whole apply; "
                       "intent.regenerate {intent_id: '%s'} re-plans after "
                       "changes" % intent["intent_id"],
    }


def output_condition(client, store: LayoutIntentStore, intent_id: str) -> dict:
    """Lazy undone/diverged check (§3.3): compare the container's current
    managed digest against the last applied one."""
    intent = store.read_intent(intent_id)
    if intent is None:
        raise OrchestratorError("unknown intent_id %r" % intent_id)
    output = intent.get("output")
    if not output:
        return {"condition": "never_applied"}
    try:
        current = client.call(
            "intent.managed_digest",
            {"container_cell": output["container_cell"]})["managed_digest"]
    except Exception:
        return {"condition": "undone_or_deleted",
                "detail": "container cell %r not found"
                          % output["container_cell"]}
    if current == output["managed_digest"]:
        return {"condition": "applied"}
    return {"condition": "diverged",
            "detail": "container content changed since the last apply"}


def regenerate(client, store: LayoutIntentStore, *, intent_id: str,
               parameters_patch: dict | None = None) -> dict:
    intent = store.read_intent(intent_id)
    if intent is None:
        raise OrchestratorError(
            "unknown intent_id %r" % intent_id,
            hint="intent.list shows the stored intents")
    cond = output_condition(client, store, intent_id)
    if cond["condition"] == "diverged":
        raise OrchestratorError(
            "managed output diverged (hand edits?): refusing the default "
            "overwrite", hint="undo the manual edits, or accept losing them "
            "by re-running intent.prepare explicitly (which re-plans against "
            "the current state)")
    params = dict(intent["parameters"])
    for key, value in (parameters_patch or {}).items():
        if key not in params:
            raise OrchestratorError(
                "unknown parameter %r" % key,
                hint="patchable: %s" % ", ".join(sorted(params)))
        if isinstance(params[key], dict) and isinstance(value, dict):
            merged = dict(params[key])
            merged.update(value)
            params[key] = merged
        else:
            params[key] = value
    return prepare(
        client, store,
        region=intent["region"],
        source_cell=params["source_cell"],
        obstacle_layers=params.get("obstacle_layers"),
        obstacle_cells=params.get("obstacle_cells"),
        extra_obstacles_um=params.get("extra_obstacles_um"),
        clearance_um=params.get("clearance_um", 0.0),
        rotation_deg=params.get("rotation_deg", 0),
        mirror=params.get("mirror", False),
        allow_empty_obstacles=params.get("allow_empty_obstacles", False),
        pitch_um=params["pitch_um"],
        numbering=params["numbering"],
        label=params["label"],
        intent_id=intent_id,
        instruction=intent.get("instruction", ""),
    )

"""intent.* local MCP tool handlers (Executable Layout Intent).

Thin adapters over klink.domains.layoutintent: the orchestrator does the
work, these translate arguments and errors. NO process facts here -- every
layer/pitch/size argument is required from the caller (project/example).

Sidecar root: <project>/.klink (resolved from KLINK_CONTEXT_ROOT's parent,
else cwd/.klink; override per call with project_root).
"""

from __future__ import annotations

import os
from pathlib import Path

from ..results import _error_result, _json_result
from . import local_tool

_STORES: dict[str, object] = {}


def _store(arguments: dict):
    from ...domains.layoutintent.store import LayoutIntentStore

    root = arguments.get("project_root")
    if root:
        root = Path(root) / ".klink"
    else:
        ctx_root = os.environ.get("KLINK_CONTEXT_ROOT", "")
        root = Path(ctx_root).parent if ctx_root else Path.cwd() / ".klink"
    key = str(root)
    if key not in _STORES:
        _STORES[key] = LayoutIntentStore(root)
    return _STORES[key]


def _client_or_error(ctx):
    if not ctx.ensure_connected() or ctx._client is None:
        return None
    return ctx._client


_PROJECT_ROOT_SCHEMA = {
    "type": "string",
    "description": "Project directory holding .klink/ (default: the "
                   "configured klink project).",
}


@local_tool(
    "intent.prepare",
    "Plan an array_labeled intent for a claimed Region: analyze occupancy, "
    "place a pitch grid of an existing source cell (any custom cell; "
    "rotation/mirror supported) with UNIQUE physical number labels (real "
    "polygon text), validate containment/obstacles, and return a preview + "
    "plan_id + plan_hash. Writes NOTHING to the layout. Obstacles are "
    "whatever YOU declare: layers, named device/blackbox cells, free-form "
    "polygons, plus an optional clearance margin -- klink ships no process "
    "defaults and assumes nothing. Apply with intent.apply after reviewing.",
    {
        "type": "object",
        "properties": {
            "region": {"type": "string", "description": "Region name, e.g. R001 (region.list)."},
            "source_cell": {"type": "string", "description": "Existing cell to array."},
            "obstacle_layers": {
                "type": "array", "items": {"type": "string"},
                "description": "Your design's obstacle layers 'L/D' (explicit; klink has no default).",
            },
            "obstacle_cells": {
                "type": "array", "items": {"type": "string"},
                "description": "Cell names whose every instance in the region is an obstacle (bbox footprint) -- custom devices/blackboxes.",
            },
            "extra_obstacles_um": {
                "type": "array",
                "items": {"type": "array",
                          "items": {"type": "array", "minItems": 2, "maxItems": 2}},
                "description": "Free-form obstacle polygons [[x,y],...] in um, target-cell coords.",
            },
            "clearance_um": {
                "type": "number", "default": 0.0,
                "description": "Keep-away margin grown around every obstacle.",
            },
            "rotation_deg": {
                "type": "integer", "enum": [0, 90, 180, 270], "default": 0,
                "description": "Orientation of every placed instance.",
            },
            "mirror": {"type": "boolean", "default": False},
            "allow_empty_obstacles": {
                "type": "boolean", "default": False,
                "description": "Required acknowledgement when passing NO obstacle source at all.",
            },
            "pitch_um": {
                "type": "array", "minItems": 2, "maxItems": 2,
                "description": "[x, y] CENTER-to-center pitch in um.",
            },
            "numbering": {
                "type": "object",
                "description": "Two modes. PREFIX: {prefix: 'S', width: 3, "
                               "start: 1, order: 'top_down'|'bottom_up'} -> "
                               "S001... PATTERN (any grid notation, "
                               "user-defined): {pattern: '{row}+{col}'} -> "
                               "1+2; '{row}-{col}' -> 1-3; 'R{row}C{col}'; "
                               "'{row:A}{col}' -> A1 (letters); "
                               "'S{index:03d}'. Optional per-axis specs: "
                               "row/col/index: {start, step, order} with "
                               "row order top_down|bottom_up, col order "
                               "left_to_right|right_to_left.",
                "properties": {
                    "prefix": {"type": "string"},
                    "width": {"type": "integer", "default": 3},
                    "start": {"type": "integer"},
                    "order": {"type": "string", "enum": ["top_down", "bottom_up"]},
                    "pattern": {"type": "string"},
                    "row": {"type": "object"},
                    "col": {"type": "object"},
                    "index": {"type": "object"},
                },
            },
            "label": {
                "type": "object",
                "description": "Two modes. OFFSET: {layer, height_um, "
                               "offset_um: [dx,dy] from footprint center}. "
                               "SLOT (preferred): {layer, slot_region: "
                               "'R00X'} where the user circled a text slot "
                               "INSIDE the unit cell (region.claim with "
                               "cell=<source_cell>); each array copy gets "
                               "its own number auto-fitted into that slot, "
                               "which moves/rotates with the instance.",
                "required": ["layer"],
                "properties": {
                    "layer": {"type": "string"},
                    "height_um": {"type": "number"},
                    "offset_um": {"type": "array", "minItems": 2, "maxItems": 2},
                    "slot_region": {"type": "string",
                                    "description": "Region name claimed inside the SOURCE cell."},
                    "margin_um": {"type": "number", "default": 0.0},
                    "min_height_um": {"type": "number",
                                      "description": "Refuse the plan if the auto-fitted text would be smaller."},
                },
            },
            "instruction": {"type": "string", "description": "The user's original wording (audit)."},
            "intent_id": {"type": "string", "description": "Existing intent to revise (else a new one is created)."},
            "project_root": _PROJECT_ROOT_SCHEMA,
        },
        "required": ["region", "source_cell", "pitch_um",
                     "numbering", "label"],
        "additionalProperties": False,
    },
)
def _tool_intent_prepare(ctx, arguments: dict) -> dict:
    client = _client_or_error(ctx)
    if client is None:
        return _error_result("not connected to klink: %s" % (ctx._last_error or "unknown"))
    try:
        from ...domains.layoutintent import orchestrator

        preview = orchestrator.prepare(
            client, _store(arguments),
            region=str(arguments["region"]),
            source_cell=str(arguments["source_cell"]),
            obstacle_layers=arguments.get("obstacle_layers"),
            obstacle_cells=arguments.get("obstacle_cells"),
            extra_obstacles_um=arguments.get("extra_obstacles_um"),
            clearance_um=float(arguments.get("clearance_um", 0.0)),
            rotation_deg=int(arguments.get("rotation_deg", 0)),
            mirror=bool(arguments.get("mirror", False)),
            allow_empty_obstacles=bool(
                arguments.get("allow_empty_obstacles", False)),
            pitch_um=list(arguments["pitch_um"]),
            numbering=dict(arguments["numbering"]),
            label=dict(arguments["label"]),
            intent_id=arguments.get("intent_id"),
            instruction=str(arguments.get("instruction", "")),
        )
        return _json_result(preview)
    except Exception as exc:
        hint = getattr(exc, "hint", "")
        return _error_result("intent.prepare failed: %s%s"
                             % (exc, (" -- " + hint) if hint else ""))


@local_tool(
    "intent.apply",
    "Commit a previewed plan in ONE KLayout transaction (fresh container "
    "cell + klink_id root; regenerate swaps the container). Requires the "
    "plan_id AND plan_hash from intent.prepare plus confirm=plan_id. "
    "Refuses stale plans (layout changed since prepare), diverged outputs, "
    "and plans with problems. One Ctrl+Z undoes the whole apply.",
    {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string"},
            "plan_hash": {"type": "string"},
            "confirm": {"type": "string", "description": "Must equal plan_id."},
            "project_root": _PROJECT_ROOT_SCHEMA,
        },
        "required": ["plan_id", "plan_hash", "confirm"],
        "additionalProperties": False,
    },
)
def _tool_intent_apply(ctx, arguments: dict) -> dict:
    client = _client_or_error(ctx)
    if client is None:
        return _error_result("not connected to klink: %s" % (ctx._last_error or "unknown"))
    try:
        from ...domains.layoutintent import orchestrator

        result = orchestrator.apply(
            client, _store(arguments),
            plan_id=str(arguments["plan_id"]),
            plan_hash=str(arguments["plan_hash"]),
            confirm=str(arguments["confirm"]),
        )
        return _json_result(result)
    except Exception as exc:
        hint = getattr(exc, "hint", "")
        return _error_result("intent.apply failed: %s%s"
                             % (exc, (" -- " + hint) if hint else ""))


@local_tool(
    "intent.regenerate",
    "Re-plan an applied intent (optionally patching parameters, e.g. a new "
    "numbering start or pitch) and return a fresh preview; the next "
    "intent.apply atomically replaces ONLY this intent's container. Refuses "
    "if the output was hand-edited (diverged) -- never a silent overwrite.",
    {
        "type": "object",
        "properties": {
            "intent_id": {"type": "string"},
            "parameters_patch": {
                "type": "object",
                "description": "Partial update of the stored parameters, "
                               "e.g. {numbering: {start: 201}}.",
            },
            "project_root": _PROJECT_ROOT_SCHEMA,
        },
        "required": ["intent_id"],
        "additionalProperties": False,
    },
)
def _tool_intent_regenerate(ctx, arguments: dict) -> dict:
    client = _client_or_error(ctx)
    if client is None:
        return _error_result("not connected to klink: %s" % (ctx._last_error or "unknown"))
    try:
        from ...domains.layoutintent import orchestrator

        preview = orchestrator.regenerate(
            client, _store(arguments),
            intent_id=str(arguments["intent_id"]),
            parameters_patch=arguments.get("parameters_patch"),
        )
        return _json_result(preview)
    except Exception as exc:
        hint = getattr(exc, "hint", "")
        return _error_result("intent.regenerate failed: %s%s"
                             % (exc, (" -- " + hint) if hint else ""))


@local_tool(
    "intent.list",
    "List stored intents (id, region, executor, revision, output container) "
    "with the lazy output condition: applied | diverged | undone_or_deleted "
    "| never_applied.",
    {
        "type": "object",
        "properties": {"project_root": _PROJECT_ROOT_SCHEMA},
        "additionalProperties": False,
    },
)
def _tool_intent_list(ctx, arguments: dict) -> dict:
    client = _client_or_error(ctx)
    try:
        from ...domains.layoutintent import orchestrator

        store = _store(arguments)
        out = []
        for intent in store.list_intents():
            row = {
                "intent_id": intent.get("intent_id"),
                "revision": intent.get("revision"),
                "region": intent.get("region"),
                "executor": (intent.get("executor") or {}).get("name"),
                "output": intent.get("output"),
            }
            if client is not None and intent.get("intent_id"):
                try:
                    row["condition"] = orchestrator.output_condition(
                        client, store, intent["intent_id"])["condition"]
                except Exception:
                    row["condition"] = "unknown"
            out.append(row)
        return _json_result({"count": len(out), "intents": out})
    except Exception as exc:
        return _error_result("intent.list failed: %s" % exc)


@local_tool(
    "intent.get",
    "One stored intent in full (parameters, output, lazy condition).",
    {
        "type": "object",
        "properties": {
            "intent_id": {"type": "string"},
            "project_root": _PROJECT_ROOT_SCHEMA,
        },
        "required": ["intent_id"],
        "additionalProperties": False,
    },
)
def _tool_intent_get(ctx, arguments: dict) -> dict:
    client = _client_or_error(ctx)
    try:
        from ...domains.layoutintent import orchestrator

        store = _store(arguments)
        intent = store.read_intent(str(arguments["intent_id"]))
        if intent is None:
            return _error_result(
                "unknown intent_id %r -- intent.list shows the stored ones"
                % arguments["intent_id"])
        out = dict(intent)
        if client is not None:
            try:
                out["condition"] = orchestrator.output_condition(
                    client, store, intent["intent_id"])
            except Exception as exc:
                out["condition"] = {"condition": "unknown", "detail": str(exc)}
        return _json_result(out)
    except Exception as exc:
        return _error_result("intent.get failed: %s" % exc)


@local_tool(
    "intent.retire",
    "Retire an intent. output_policy decides the geometry's fate: "
    "'preserve' (default) keeps the container as ordinary layout and just "
    "closes the intent; 'detach' additionally forgets the output binding "
    "(container stays, no longer managed); 'remove' deletes the container "
    "via the digest-guarded plugin primitive -- refused if the output was "
    "hand-edited (diverged). Never deletes user geometry.",
    {
        "type": "object",
        "properties": {
            "intent_id": {"type": "string"},
            "output_policy": {
                "type": "string",
                "enum": ["preserve", "detach", "remove"],
                "default": "preserve",
            },
            "confirm": {
                "type": "string",
                "description": "Required for output_policy=remove: must "
                               "equal the intent_id.",
            },
            "project_root": _PROJECT_ROOT_SCHEMA,
        },
        "required": ["intent_id"],
        "additionalProperties": False,
    },
)
def _tool_intent_retire(ctx, arguments: dict) -> dict:
    client = _client_or_error(ctx)
    try:
        store = _store(arguments)
        intent = store.read_intent(str(arguments["intent_id"]))
        if intent is None:
            return _error_result(
                "unknown intent_id %r" % arguments["intent_id"])
        policy = str(arguments.get("output_policy", "preserve"))
        output = intent.get("output")
        removed = None
        if policy == "remove" and output:
            if client is None:
                return _error_result(
                    "not connected to klink: %s"
                    % (ctx._last_error or "unknown"))
            if str(arguments.get("confirm", "")) != intent["intent_id"]:
                return _error_result(
                    "output_policy=remove deletes real geometry; pass "
                    "confirm='%s' to acknowledge" % intent["intent_id"])
            # target cell comes from the live region binding
            got = client.call("region.get", {"name": intent["region"]})
            removed = client.call("intent.remove_managed_output", {
                "parent_cell": got["cell"],
                "root_klink_id": output["root_klink_id"],
                "expected_managed_digest": output["managed_digest"],
            })["removed_container"]
            intent["output"] = None
        elif policy == "detach":
            intent["output"] = None
        intent["retired"] = True
        intent["revision"] = int(intent.get("revision", 1)) + 1
        store.write_intent(intent)
        return _json_result({
            "intent_id": intent["intent_id"],
            "retired": True,
            "output_policy": policy,
            "removed_container": removed,
        })
    except Exception as exc:
        hint = getattr(exc, "hint", "")
        return _error_result("intent.retire failed: %s%s"
                             % (exc, (" -- " + hint) if hint else ""))


@local_tool(
    "intent.rebind",
    "Explicitly bind an intent to a (new) Region name -- the ONLY path after "
    "a region was deleted/repaired. Verifies the region exists; never "
    "guesses.",
    {
        "type": "object",
        "properties": {
            "intent_id": {"type": "string"},
            "region": {"type": "string"},
            "project_root": _PROJECT_ROOT_SCHEMA,
        },
        "required": ["intent_id", "region"],
        "additionalProperties": False,
    },
)
def _tool_intent_rebind(ctx, arguments: dict) -> dict:
    client = _client_or_error(ctx)
    if client is None:
        return _error_result("not connected to klink: %s" % (ctx._last_error or "unknown"))
    try:
        store = _store(arguments)
        intent = store.read_intent(str(arguments["intent_id"]))
        if intent is None:
            return _error_result(
                "unknown intent_id %r" % arguments["intent_id"])
        region = str(arguments["region"])
        client.call("region.get", {"name": region})  # existence check
        intent["region"] = region
        intent["revision"] = int(intent.get("revision", 1)) + 1
        store.write_intent(intent)
        return _json_result({
            "intent_id": intent["intent_id"],
            "region": region,
            "revision": intent["revision"],
            "next_action": "intent.regenerate to re-plan against the new "
                           "region",
        })
    except Exception as exc:
        return _error_result("intent.rebind failed: %s" % exc)

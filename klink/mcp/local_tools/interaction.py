"""interaction.selection.* / interaction.context local MCP tool handlers.

Session-scoped selection memory (explicit SEND). Handlers are functions
(ctx, arguments); the selection store, subscription wiring, and the combined
interaction.context builder live on the bridge (ctx).
"""

from __future__ import annotations

from ..results import _error_result, _json_result
from . import local_tool
from ._helpers import _with_age


@local_tool(
    "interaction.selection.latest",
    "Return the latest explicit sent selection from this MCP session memory.",
    {"type": "object", "additionalProperties": False},
)
def _tool_interaction_selection_latest(ctx, arguments: dict) -> dict:
    try:
        ctx._ensure_interaction_subscription()
        latest = ctx._context.latest()
        return _json_result({
            "selection": _with_age(latest),
            "subscription_active": ctx._interaction_subscription_active,
            "subscription_error": ctx._event_subscription_error,
        })
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "interaction.selection.recent",
    "Return recent explicit sent selections by order, defaulting to the latest five.",
    {
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
        "additionalProperties": False,
    },
)
def _tool_interaction_selection_recent(ctx, arguments: dict) -> dict:
    try:
        ctx._ensure_interaction_subscription()
        limit = arguments.get("limit")
        return _json_result({
            "selections": [_with_age(r) for r in ctx._context.recent(limit)],
            "subscription_active": ctx._interaction_subscription_active,
            "subscription_error": ctx._event_subscription_error,
        })
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "interaction.selection.get",
    "Return one stored selection by stable selection id.",
    {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
        "additionalProperties": False,
    },
)
def _tool_interaction_selection_get(ctx, arguments: dict) -> dict:
    try:
        ctx._ensure_interaction_subscription()
        selection_id = str(arguments.get("id") or "")
        record = ctx._context.get(selection_id)
        if record is None:
            return _error_result(f"unknown selection id: {selection_id}")
        return _json_result({"selection": _with_age(record)})
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "interaction.selection.label",
    "Attach or update a label and description for a stored selection.",
    {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "label": {"type": "string"},
            "description": {"type": "string"},
        },
        "required": ["id"],
        "additionalProperties": False,
    },
)
def _tool_interaction_selection_label(ctx, arguments: dict) -> dict:
    try:
        record = ctx._context.label(
            str(arguments.get("id") or ""),
            arguments.get("label"),
            arguments.get("description"),
        )
        return _json_result({"selection": _with_age(record)})
    except KeyError as exc:
        return _error_result(f"unknown selection id: {exc.args[0]}")
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "interaction.selection.clear_session",
    "Clear this MCP session's persisted interaction context after explicit confirmation.",
    {
        "type": "object",
        "properties": {"confirm": {"type": "string"}},
        "required": ["confirm"],
        "additionalProperties": False,
    },
)
def _tool_interaction_selection_clear_session(ctx, arguments: dict) -> dict:
    try:
        confirm = arguments.get("confirm")
        if confirm != ctx._context.session_id:
            return _error_result(
                "clear_session requires confirm equal to session_id "
                f"({ctx._context.session_id})"
            )
        return _json_result(ctx._context.clear_session())
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "interaction.context",
    "Return current KLayout selection plus recent persisted interaction selections.",
    {
        "type": "object",
        "properties": {"include_current_selection": {"type": "boolean"}},
        "additionalProperties": False,
    },
)
def _tool_interaction_context(ctx, arguments: dict) -> dict:
    try:
        return _json_result(ctx._interaction_context(arguments))
    except Exception as exc:
        return _error_result(str(exc))

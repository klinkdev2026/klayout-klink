"""klink.* session/transfer/status + klink.guide local MCP tool handlers.

Bridge status/reconnect, session registry list/label/resolve/status/use,
klive-target selection, cross-session transfer prepare/commit, and the guide.
Handlers are functions (ctx, arguments); session resolution, label writes,
session-scoped clients, and connection mutation (session_use) go through the
bridge (ctx). Distinct from mcp/session.py (SessionOps), which holds the
helper logic these handlers call via ctx.
"""

from __future__ import annotations

from ...errors import KLinkError
from ...transfer import (
    TransferError,
    build_flat_selection_package,
    build_shallow_instance_package,
    commit_flat_selection_package,
    commit_shallow_instance_package,
)
from ..config import DEFAULT_KLINK_HOST
from ..results import _error_result, _json_result
from . import local_tool
from ._helpers import _layout_dbu_um, _selected_instance_snapshot


@local_tool(
    "klink.status",
    "Return MCP bridge connection status and last klink connection error.",
    {"type": "object", "additionalProperties": False},
)
def _tool_klink_status(ctx, arguments: dict) -> dict:
    return _json_result(ctx.status())


@local_tool(
    "klink.reconnect",
    "Close any stale klink client and try to reconnect to KLayout.",
    {"type": "object", "additionalProperties": False},
)
def _tool_klink_reconnect(ctx, arguments: dict) -> dict:
    ok = ctx.reconnect()
    return _json_result({"ok": ok, **ctx.status()})


@local_tool(
    "klink.session_list",
    "List discoverable KLayout/klink sessions from the local session registry.",
    {
        "type": "object",
        "properties": {"include_stale": {"type": "boolean", "default": False}},
        "additionalProperties": False,
    },
)
def _tool_klink_session_list(ctx, arguments: dict) -> dict:
    try:
        include_stale = bool(arguments.get("include_stale", False))
        sessions = ctx._sessions.list_sessions(include_stale=include_stale)
        sessions = ctx._enrich_sessions_with_labels(sessions)
        state = ctx._sessions.read_state()
        return _json_result({
            "sessions": sessions,
            "count": len(sessions),
            "active_session_id": ctx._active_session_id,
            "registry_state": state,
            "registry_root": str(ctx._sessions.root),
        })
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "klink.session_label",
    "Attach a human label and aliases to a registered KLayout session.",
    {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "label": {"type": "string"},
            "aliases": {"type": "array", "items": {"type": "string"}, "default": []},
            "description": {"type": "string"},
        },
        "required": ["session_id", "label"],
        "additionalProperties": False,
    },
)
def _tool_klink_session_label(ctx, arguments: dict) -> dict:
    try:
        session_id = ctx._resolve_session_id(str(arguments.get("session_id") or ""))
        label = str(arguments.get("label") or "").strip()
        if not label:
            return _error_result("label is required")
        aliases = [str(a).strip() for a in arguments.get("aliases", []) if str(a).strip()]
        description = str(arguments.get("description") or "").strip()
        return _json_result(ctx._write_session_label(session_id, label, aliases, description))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "klink.session_resolve",
    "Resolve a session id, human label, alias, active cell, or top cell to a KLayout session.",
    {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    },
)
def _tool_klink_session_resolve(ctx, arguments: dict) -> dict:
    try:
        return _json_result(ctx._resolve_session_query(str(arguments.get("query") or "")))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "klink.session_status",
    "Return one registered KLayout session record, defaulting to the active MCP session.",
    {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "include_stale": {"type": "boolean", "default": True},
        },
        "additionalProperties": False,
    },
)
def _tool_klink_session_status(ctx, arguments: dict) -> dict:
    try:
        session_id = arguments.get("session_id") or ctx._active_session_id
        if not session_id:
            return _json_result({
                "session": None,
                "active_session_id": ctx._active_session_id,
                "host": ctx._host,
                "port": ctx._port,
            })
        record = ctx._sessions.get(str(session_id), include_stale=bool(arguments.get("include_stale", True)))
        if record is None:
            return _error_result(f"unknown session id: {session_id}")
        return _json_result({"session": record, "active_session_id": ctx._active_session_id})
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "klink.session_use",
    "Switch this MCP bridge's active KLayout RPC target to a registered session.",
    {
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": ["session_id"],
        "additionalProperties": False,
    },
)
def _tool_klink_session_use(ctx, arguments: dict) -> dict:
    try:
        session_id = ctx._resolve_session_id(str(arguments.get("session_id") or ""))
        record = ctx._sessions.get(session_id)
        if record is None:
            return _error_result(f"unknown or stale session id: {session_id}")
        host = str(record.get("host") or DEFAULT_KLINK_HOST)
        port = int(record.get("rpc_port") or record.get("port"))
        ctx._close_client(clear_tools=True)
        ctx._host = host
        ctx._port = port
        ctx._active_session_id = session_id
        ok = ctx.ensure_connected()
        return _json_result({
            "ok": ok,
            "active_session_id": ctx._active_session_id,
            "session": record,
            "status": ctx.status(),
        })
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "klink.session_set_klive_target",
    "Set the registered KLayout session used by the klive-compatible 8082 entrypoint.",
    {
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": ["session_id"],
        "additionalProperties": False,
    },
)
def _tool_klink_session_set_klive_target(ctx, arguments: dict) -> dict:
    try:
        session_id = ctx._resolve_session_id(str(arguments.get("session_id") or ""))
        record = ctx._sessions.get(session_id)
        if record is None:
            return _error_result(f"unknown or stale session id: {session_id}")
        state = ctx._sessions.write_state({"klive_target_session": session_id})
        return _json_result({"ok": True, "klive_target_session": session_id, "session": record, "registry_state": state})
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "klink.transfer_prepare",
    "Prepare a flat-selection transfer package between two registered KLayout sessions and dry-run it on the target.",
    {
        "type": "object",
        "properties": {
            "source_session": {"type": "string"},
            "target_session": {"type": "string"},
            "target_cell": {"type": "string", "default": "TOP"},
            "copy_mode": {
                "type": "string",
                "enum": ["flat_selection", "shallow_instance"],
                "default": "flat_selection",
            },
            "layer_map": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Optional source 'L/D' to target 'L/D' mapping.",
            },
            "translate_um": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 2,
                "maxItems": 2,
                "default": [0, 0],
            },
        },
        "required": ["source_session", "target_session"],
        "additionalProperties": False,
    },
)
def _tool_klink_transfer_prepare(ctx, arguments: dict) -> dict:
    source_client = None
    target_client = None
    try:
        source_session = ctx._resolve_session_id(str(arguments.get("source_session") or ""))
        target_session = ctx._resolve_session_id(str(arguments.get("target_session") or ""))
        target_cell = str(arguments.get("target_cell") or "TOP")
        copy_mode = str(arguments.get("copy_mode") or "flat_selection")
        layer_map = arguments.get("layer_map") or {}
        translate_um = arguments.get("translate_um") or [0, 0]

        source_client = ctx._connect_session_client(source_session)
        target_client = ctx._connect_session_client(target_session)
        source_info = source_client.layout_info(verbosity="normal")
        source_dbu = _layout_dbu_um(source_info)
        selection = source_client.selection_get(limit=5000)
        if copy_mode == "flat_selection":
            source_layers = source_client.layer_list()
            package = build_flat_selection_package(
                selection,
                source_layers=source_layers,
                source_dbu_um=source_dbu,
                source_session=source_session,
                target_session=target_session,
                target_cell=target_cell,
                layer_map=layer_map,
                translate_um=translate_um,
            )
            target_dry_run = commit_flat_selection_package(target_client, package, dry_run=True)
        elif copy_mode == "shallow_instance":
            source_instances = _selected_instance_snapshot(source_client, selection)
            target_cells = target_client.cell_list(limit=5000)
            package = build_shallow_instance_package(
                source_instances,
                target_cells=target_cells,
                source_dbu_um=source_dbu,
                source_session=source_session,
                target_session=target_session,
                target_cell=target_cell,
                translate_um=translate_um,
            )
            target_dry_run = commit_shallow_instance_package(target_client, package, dry_run=True)
        else:
            raise TransferError(f"unsupported copy_mode: {copy_mode}")
        try:
            target_pending = target_client.transfer_pending_set(package)
        except AttributeError:
            target_pending = target_client.call("transfer.pending_set", {"package": package})
        ctx._pending_transfers[str(package["package_id"])] = package
        return _json_result({
            "ok": True,
            "pending": True,
            "package_id": package["package_id"],
            "source_session": source_session,
            "target_session": target_session,
            "review": package["review"],
            "target_dry_run": target_dry_run["write"],
            "target_pending": target_pending,
        })
    except (TransferError, KLinkError) as exc:
        return _error_result(str(exc))
    except Exception as exc:
        return _error_result(str(exc))
    finally:
        for client in (source_client, target_client):
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass


@local_tool(
    "klink.transfer_commit",
    "Commit a package previously created by klink.transfer_prepare.",
    {
        "type": "object",
        "properties": {
            "package_id": {"type": "string"},
            "dry_run": {"type": "boolean", "default": False},
        },
        "required": ["package_id"],
        "additionalProperties": False,
    },
)
def _tool_klink_transfer_commit(ctx, arguments: dict) -> dict:
    target_client = None
    try:
        package_id = str(arguments.get("package_id") or "")
        package = ctx._pending_transfers.get(package_id)
        if package is None:
            return _error_result(f"unknown pending transfer package: {package_id}")
        target_session = str(package["target_session"])
        target_client = ctx._connect_session_client(target_session)
        if package.get("copy_mode") == "flat_selection":
            result = commit_flat_selection_package(
                target_client,
                package,
                dry_run=bool(arguments.get("dry_run", False)),
            )
        elif package.get("copy_mode") == "shallow_instance":
            result = commit_shallow_instance_package(
                target_client,
                package,
                dry_run=bool(arguments.get("dry_run", False)),
            )
        else:
            raise TransferError(f"unsupported copy_mode: {package.get('copy_mode')}")
        if not result["dry_run"]:
            ctx._pending_transfers.pop(package_id, None)
        return _json_result(result)
    except (TransferError, KLinkError) as exc:
        return _error_result(str(exc))
    except Exception as exc:
        return _error_result(str(exc))
    finally:
        if target_client is not None:
            try:
                target_client.close()
            except Exception:
                pass


@local_tool(
    "klink.guide",
    "START HERE if you do not know this stack: reports what is open, "
    "what intent state already exists on disk (declared nets, LVS "
    "reports, spec files), the literal call for each available user "
    "intention, and a suggested next action. Call it whenever you are "
    "unsure what to do next — the workflow lives in tool results, not "
    "in your memory.",
    {"type": "object", "properties": {}, "additionalProperties": False},
)
def _tool_klink_guide(ctx, arguments: dict) -> dict:
    try:
        from ..guide import guide_payload

        connection: dict = {"connected": False,
                            "next_action": "klink.reconnect {}"}
        if ctx.ensure_connected() and ctx._client is not None:
            try:
                info = ctx._client.layout_info()
                connection = {
                    "connected": True,
                    "active_session": ctx._active_session_id,
                    "layout_file": info.get("file"),
                    "active_cell": info.get("cell"),
                    "top_cells": info.get("top_cells"),
                }
            except Exception as exc:
                connection = {"connected": True,
                              "layout_info_error": str(exc)}
        return _json_result(guide_payload(
            ctx._structdevice_spec_root(), connection=connection))
    except Exception as exc:
        return _error_result(str(exc))

"""Thin session-control RPCs."""

from __future__ import annotations

from ..errors import ErrorCode, RpcError
from ..registry import method


@method(
    "session.mark_klive_target",
    description="Mark this KLayout window as the klive/gdsfactory-compatible 8082 target.",
    params_schema={"type": "object", "additionalProperties": False},
    returns_schema={"type": "object"},
    mutates=False,
    tags=["session"],
)
def session_mark_klive_target(params, ctx):
    try:
        from ..server import instance as _server_instance

        srv = _server_instance()
        if srv is None:
            raise RpcError(ErrorCode.INTERNAL, "klink server instance is not available")
        return srv.mark_klive_target()
    except RpcError:
        raise
    except Exception as exc:
        raise RpcError(ErrorCode.INTERNAL, str(exc))


@method(
    "session.label_set",
    description="Set a human label and aliases for a registered KLayout session in the shared registry.",
    params_schema={
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
    returns_schema={"type": "object"},
    mutates=False,
    tags=["session"],
)
def session_label_set(params, ctx):
    session_id = str(params.get("session_id") or "").strip()
    label = str(params.get("label") or "").strip()
    if not session_id:
        raise RpcError(ErrorCode.INVALID_PARAMS, "session_id is required")
    if not label:
        raise RpcError(ErrorCode.INVALID_PARAMS, "label is required")

    aliases = [str(a).strip() for a in params.get("aliases", []) if str(a).strip()]
    description = str(params.get("description") or "").strip()
    try:
        from ..session_registry import KLayoutSessionRegistry

        registry = KLayoutSessionRegistry()
        state = registry.read_state()
        labels = state.get("session_labels", {})
        if not isinstance(labels, dict):
            labels = {}
        entry = {
            "label": label,
            "aliases": aliases,
        }
        if description:
            entry["description"] = description
        labels[session_id] = entry
        state = registry.write_state({"session_labels": labels})
        return {"ok": True, "session_id": session_id, **entry, "registry_state": state}
    except RpcError:
        raise
    except Exception as exc:
        raise RpcError(ErrorCode.INTERNAL, str(exc))

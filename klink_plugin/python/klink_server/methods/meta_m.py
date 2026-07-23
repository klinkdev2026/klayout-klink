"""
Meta methods: hello, meta.ping, meta.methods.

These are the methods a client (including an LLM agent) calls first to
discover what the server can do.
"""

from __future__ import annotations

import pya

from ..registry import method, all_specs

SERVER_NAME = "klink"
SERVER_VERSION = "0.1.5"
PROTOCOL_VERSION = 1


@method(
    "hello",
    description=(
        "Introduce the client and receive server info + capability list. "
        "Recommended as the first call on every new connection. Not "
        "required (the server works without it), but useful for "
        "version-gating features."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "client": {"type": "string", "description": "Client identifier string, e.g. 'klayout-klink/0.1.0'"},
            "protocol": {"type": "integer", "description": "Protocol version the client speaks"},
        },
        "additionalProperties": True,
    },
    returns_schema={
        "type": "object",
        "properties": {
            "server": {"type": "string"},
            "version": {"type": "string"},
            "protocol": {"type": "integer"},
            "klayout_version": {"type": "string"},
            "capabilities": {"type": "array", "items": {"type": "string"}},
        },
    },
    tags=["meta"],
)
def hello(params, ctx):
    return {
        "server": SERVER_NAME,
        "version": SERVER_VERSION,
        "protocol": PROTOCOL_VERSION,
        "klayout_version": pya.__version__,
        "capabilities": sorted({spec.name.split(".")[0] for spec in all_specs().values()}),
    }


@method(
    "meta.ping",
    description=(
        "Liveness probe. Echoes the supplied params back and includes "
        "the server-side trace id. Use this to measure round-trip time "
        "or check a connection is healthy."
    ),
    params_schema={"type": "object", "additionalProperties": True},
    returns_schema={
        "type": "object",
        "properties": {
            "echo": {"type": "object"},
            "trace_id": {"type": "string"},
        },
    },
    tags=["meta"],
)
def ping(params, ctx):
    return {"echo": params, "trace_id": ctx.trace_id}


@method(
    "meta.methods",
    description=(
        "Return the full RPC method catalogue with descriptions and "
        "JSON schemas. Designed to be directly consumable by LLM "
        "function-calling layers (tool definitions, MCP, OpenAI/Anthropic "
        "tools, etc.)."
    ),
    params_schema={"type": "object"},
    returns_schema={
        "type": "object",
        "properties": {
            "methods": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "params": {"type": "object"},
                        "returns": {"type": "object"},
                        "mutates": {"type": "boolean"},
                        "long_running": {"type": "boolean"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
    },
    tags=["meta"],
)
def meta_methods(params, ctx):
    return {
        "methods": [spec.to_public_dict() for spec in all_specs().values()]
    }


@method(
    "meta.debug_signals",
    description=(
        "Return SignalHub diagnostic log and optionally fire a synthetic "
        "event on a channel to verify the subscribe->emit->deliver path. "
        "Pass {'fire': 'selection_changed'} to test event delivery."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "fire": {"type": "string", "description": "Channel to synthesise an event on"},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "diagnostic": {"type": "array", "items": {"type": "string"}},
            "bound_views": {"type": "integer"},
            "current_view_is_tracked": {"type": "boolean"},
            "subscribers_per_channel": {"type": "object"},
            "fired": {"type": ["string", "null"]},
        },
    },
    tags=["meta"],
)
def meta_debug_signals(params, ctx):
    from ..server import instance as _srv_instance
    srv = _srv_instance()
    if srv is None:
        return {"diagnostic": ["no server instance"]}
    hub = getattr(srv, "signals", None)
    if hub is None:
        return {"diagnostic": ["no signal hub"]}

    subs = {
        ch: len(conns) for ch, conns in srv.events._subs.items()
    }

    current_tracked = False
    try:
        mw = pya.Application.instance().main_window()
        current_tracked = (mw is not None and mw.current_view() is hub._view and hub._view is not None)
    except Exception:
        pass

    fired = None
    fire = params.get("fire")
    if fire:
        srv.events.emit(fire, {"synthetic": True, "trace_id": ctx.trace_id})
        fired = fire

    current_view_id = None
    try:
        mw = pya.Application.instance().main_window()
        cv = mw.current_view() if mw is not None else None
        if cv is not None:
            current_view_id = id(cv)
    except Exception:
        pass

    return {
        "diagnostic": list(hub.diagnostic),
        "bound_views": len(hub._bound_view_ids),
        "current_view_id": current_view_id,
        "tracked_view_id": id(hub._view) if hub._view is not None else None,
        "current_view_is_tracked": current_tracked,
        "subscribers_per_channel": subs,
        "fire_counts": dict(hub.fire_counts),
        "fired": fired,
    }

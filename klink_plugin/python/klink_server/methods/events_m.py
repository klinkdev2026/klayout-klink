"""
Event subscription RPC methods.

Clients subscribe to channels using `events.subscribe`; the server then
pushes event frames (NDJSON lines with {"event":..., "data":...}) to
that connection whenever the corresponding pya signal fires. Pushing
is done in `SignalHub` (see server.py); this module only exposes the
subscribe/unsubscribe/list surface.
"""

from __future__ import annotations

from ..registry import method
from ..errors import RpcError, ErrorCode
from ..events import VALID_CHANNELS


def _broadcaster(ctx):
    """The dispatcher has a reference to the server; we reach through
    the request's emit_event closure to get the connection, but that
    doesn't expose the broadcaster. Instead, go through the module-level
    server instance."""
    from ..server import instance as _srv_instance
    srv = _srv_instance()
    if srv is None:
        raise RpcError(ErrorCode.INTERNAL, "server instance not available")
    return srv


def _conn_for_ctx(srv, ctx):
    conn = srv.conns.get(ctx.conn_id)
    if conn is None:
        raise RpcError(ErrorCode.INTERNAL, f"connection #{ctx.conn_id} not found")
    return conn


@method(
    "events.channels",
    description=(
        "List event channels the server can push. Subscribe to a subset "
        "via events.subscribe. Events are delivered as NDJSON frames "
        "with {\"event\": \"<name>\", \"data\": {...}}."
    ),
    params_schema={"type": "object"},
    returns_schema={
        "type": "object",
        "properties": {
            "channels": {"type": "array", "items": {"type": "string"}},
        },
    },
    tags=["events", "meta"],
)
def events_channels(params, ctx):
    return {"channels": sorted(VALID_CHANNELS)}


@method(
    "events.status",
    description=(
        "Return event subscription and SignalHub diagnostics for the calling "
        "connection. Use this to debug whether selection_changed and other "
        "interaction events are bound and subscribed."
    ),
    params_schema={"type": "object"},
    returns_schema={
        "type": "object",
        "properties": {
            "channels": {"type": "array", "items": {"type": "string"}},
            "active": {"type": "array", "items": {"type": "string"}},
            "subscribers_per_channel": {"type": "object"},
            "event_emit_counts": {"type": "object"},
            "event_delivered_counts": {"type": "object"},
            "signal_diagnostic": {"type": "array", "items": {"type": "string"}},
            "signal_fire_counts": {"type": "object"},
        },
    },
    tags=["events", "read"],
)
def events_status(params, ctx):
    srv = _broadcaster(ctx)
    conn = _conn_for_ctx(srv, ctx)
    hub = getattr(srv, "signals", None)
    return {
        "channels": sorted(VALID_CHANNELS),
        "active": sorted(conn.subscriptions),
        "subscribers_per_channel": {
            ch: len(conns) for ch, conns in srv.events._subs.items()
        },
        "event_emit_counts": dict(getattr(srv.events, "emit_counts", {}) or {}),
        "event_delivered_counts": dict(getattr(srv.events, "delivered_counts", {}) or {}),
        "signal_diagnostic": list(getattr(hub, "diagnostic", []) or []),
        "signal_fire_counts": dict(getattr(hub, "fire_counts", {}) or {}),
    }


@method(
    "events.subscribe",
    description=(
        "Subscribe the calling connection to one or more event channels. "
        "Unknown channels are silently ignored (check 'accepted' in the "
        "response). Call events.channels for the full list."
    ),
    params_schema={
        "type": "object",
        "required": ["channels"],
        "properties": {
            "channels": {"type": "array", "items": {"type": "string"}},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "accepted": {"type": "array", "items": {"type": "string"}},
            "active": {"type": "array", "items": {"type": "string"}},
        },
    },
    tags=["events"],
)
def events_subscribe(params, ctx):
    channels = params.get("channels")
    if not isinstance(channels, list):
        raise RpcError(ErrorCode.BAD_PARAMS, "channels must be a list of strings")
    srv = _broadcaster(ctx)
    conn = _conn_for_ctx(srv, ctx)
    accepted = srv.events.subscribe(conn, channels)
    return {
        "accepted": sorted(accepted),
        "active": sorted(conn.subscriptions),
    }


@method(
    "events.unsubscribe",
    description=(
        "Unsubscribe the calling connection from one or more channels. "
        "Pass an empty list or omit to keep state unchanged; pass '*' to "
        "drop all subscriptions."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "channels": {"type": "array", "items": {"type": "string"}},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "active": {"type": "array", "items": {"type": "string"}},
        },
    },
    tags=["events"],
)
def events_unsubscribe(params, ctx):
    channels = params.get("channels", [])
    srv = _broadcaster(ctx)
    conn = _conn_for_ctx(srv, ctx)
    if channels == "*" or (isinstance(channels, list) and "*" in channels):
        srv.events.unsubscribe_all(conn)
    elif isinstance(channels, list):
        srv.events.unsubscribe(conn, channels)
    else:
        raise RpcError(ErrorCode.BAD_PARAMS, "channels must be a list or '*'")
    return {"active": sorted(conn.subscriptions)}

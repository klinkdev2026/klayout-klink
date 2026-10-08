"""Diagnostics sub-object for KLinkMCPBridge.

Owns `klink.status` aggregation and the client/plugin version handshake.
Operates on the bridge as `ctx` (the shared state holder).
"""

from __future__ import annotations

import importlib.util
import sys


def _extensions_status() -> dict:
    """Installed klink.plugins extensions + failures (lazy, fault-isolated)."""
    try:
        from klink import ext
        return ext.status_summary()
    except Exception as exc:                       # never break status
        return {"installed": [], "failures": [
            {"package": "<extension scan>", "error": repr(exc)}]}


class Diagnostics:
    def __init__(self, ctx):
        self.ctx = ctx

    def status(self) -> dict:
        ctx = self.ctx
        handshake = self.version_handshake_status()
        return {
            "connected": ctx._client is not None,
            "host": ctx._host,
            "port": ctx._port,
            "interpreter": sys.executable,
            "capabilities": _optional_capabilities(),
            "editor_mode": _editor_mode_block(handshake),
            "active_session_id": ctx._active_session_id,
            "session_registry": str(ctx._sessions.root),
            "profiles": list(ctx._profiles),
            "tool_count": len(ctx._tools),
            "connect_count": ctx._connect_count,
            "last_error": ctx._last_error,
            "last_connect_attempt": ctx._last_connect_attempt,
            "call_timeout": ctx._call_timeout,
            "long_call_timeout": ctx._long_call_timeout,
            "last_call": ctx._last_call,
            "interaction_context": ctx._context.status(),
            "event_subscription_error": ctx._event_subscription_error,
            "interaction_subscription_active": ctx._interaction_subscription_active,
            "session_event_subscriptions": {
                "active": sorted(ctx._session_event_active),
                "errors": dict(ctx._session_event_errors),
            },
            "journal_catchup_counts": dict(ctx._journal_catchup_counts),
            "version_handshake": handshake,
            "extensions": _extensions_status(),
        }

    def version_handshake_status(self) -> dict:
        """Best-effort client/plugin version compatibility for klink.status.

        Surfaces a protocol mismatch with an instructive ``next_action``
        instead of letting a stale plugin fail later as ERR_UNKNOWN_METHOD.
        """
        from .. import PROTOCOL_VERSION, __version__, evaluate_handshake

        ctx = self.ctx
        if ctx._client is None:
            return evaluate_handshake(__version__, PROTOCOL_VERSION, {})
        try:
            return ctx._client.handshake()
        except Exception as exc:
            # Do NOT leave this looking like a verdict on the plugin: the
            # handshake needs a live round trip, while the `connected` field
            # above only says a client object exists, so the two can disagree
            # and read as self-contradiction. Say which one is stale.
            result = evaluate_handshake(__version__, PROTOCOL_VERSION, {})
            result["error"] = str(exc)
            result["stale"] = True
            result["next_action"] = (
                "this block could not be refreshed (the handshake needs a "
                "live round trip); it says nothing about the plugin. Call "
                "klink.reconnect, then read klink.status again")
            return result


def _editor_mode_block(handshake: dict) -> dict:
    """Top-level `editor_mode` for klink.status, lifted out of the live
    handshake so an agent sees viewer mode without digging.

    editable: True (editor mode, `klayout -e`), False (viewer mode: every
    write RPC is refused with ERR_VIEWER_MODE), None (unknown: not
    connected, handshake stale, no view open, or a plugin older than
    0.6.4 that does not report it)."""
    editable = handshake.get("editable")
    if handshake.get("stale"):
        editable = None
    block = {"editable": editable}
    if editable is False:
        block["next_action"] = handshake.get("editor_mode_next_action") or (
            "KLayout is running in viewer mode: write RPCs are refused "
            "(ERR_VIEWER_MODE). Close KLayout and start it with "
            "`klayout -e`, then call klink.reconnect.")
    elif editable is None:
        block["note"] = (
            "unknown: not connected, no view open, or the plugin predates "
            "0.6.4; a viewer-mode KLayout surfaces as ERR_VIEWER_MODE on "
            "the first write RPC either way.")
    return block


def _optional_capabilities() -> dict:
    """Self-diagnosis for optional extras in THIS MCP interpreter."""
    return {
        "gdsfactory": importlib.util.find_spec("gdsfactory") is not None,
        "klayout_db": importlib.util.find_spec("klayout") is not None,
    }

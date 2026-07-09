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
        return {
            "connected": ctx._client is not None,
            "host": ctx._host,
            "port": ctx._port,
            "interpreter": sys.executable,
            "capabilities": _optional_capabilities(),
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
            "version_handshake": self.version_handshake_status(),
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
            result = evaluate_handshake(__version__, PROTOCOL_VERSION, {})
            result["error"] = str(exc)
            return result


def _optional_capabilities() -> dict:
    """Self-diagnosis for optional extras in THIS MCP interpreter."""
    return {
        "gdsfactory": importlib.util.find_spec("gdsfactory") is not None,
        "klayout_db": importlib.util.find_spec("klayout") is not None,
    }

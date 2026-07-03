"""Connection lifecycle + event/journal wiring for KLinkMCPBridge.

Owns the live RPC link to klink: connect/ensure/reconnect/close, tool-catalogue
refresh (meta.methods -> profile filter), interaction selection_sent
subscription, registered-session event fan-in, and SEND-journal catch-up.

Operates on the bridge as `ctx` (the shared state holder): all mutable state
(`_client`, `_tools`, `_method_specs`, event/journal bookkeeping, …) lives on
the bridge so state placement is unchanged by the split.
"""

from __future__ import annotations

import time
from pathlib import Path

from .config import DEFAULT_KLINK_HOST
from .profiles import filter_methods

# KLinkClient is resolved from the bridge module at call time (not imported at
# module top) so it stays the single monkeypatch seam — tests patch
# klink.mcp.bridge.KLinkClient and both the primary and session-event clients
# pick it up. This also breaks the bridge<->connection import cycle.


class Connection:
    def __init__(self, ctx):
        self.ctx = ctx

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def connect(self) -> None:
        from .bridge import KLinkClient

        ctx = self.ctx
        ctx._last_connect_attempt = time.time()
        ctx._client = KLinkClient(
            host=ctx._host,
            port=ctx._port,
            default_call_timeout=ctx._call_timeout,
        )
        try:
            ctx._client.connect()
            cat = ctx._client.methods()["methods"]
            ctx._method_specs = {spec["name"]: spec for spec in cat}
            ctx._tools = filter_methods(cat, ctx._profiles)
            self.subscribe_interaction_events()
            self.subscribe_registered_session_events()
            ctx._connect_count += 1
            ctx._last_error = ""
        except Exception as exc:
            ctx._last_error = str(exc)
            self.close_client(clear_tools=True)
            raise

    def ensure_connected(self) -> bool:
        if self.ctx._client is not None:
            return True
        try:
            self.connect()
            return True
        except Exception:
            return False

    def reconnect(self) -> bool:
        self.close_client(clear_tools=True)
        return self.ensure_connected()

    def close(self) -> None:
        self.close_client(clear_tools=True)

    def close_client(self, *, clear_tools: bool) -> None:
        ctx = self.ctx
        self.close_session_event_clients()
        if ctx._client is not None:
            try:
                ctx._client.close()
            except Exception:
                pass
            ctx._client = None
        ctx._interaction_subscription_active = False
        if clear_tools:
            ctx._tools.clear()
            ctx._method_specs.clear()

    # ------------------------------------------------------------------
    # Event / journal wiring
    # ------------------------------------------------------------------
    def subscribe_interaction_events(self) -> None:
        ctx = self.ctx
        if ctx._client is None:
            return
        try:
            if not hasattr(ctx._client, "on") or not hasattr(ctx._client, "subscribe"):
                ctx._event_subscription_error = "client does not support event subscription"
                ctx._interaction_subscription_active = False
                return
            ctx._client.on("selection_changed", self.on_selection_changed)
            ctx._client.on("selection_sent", self.on_selection_sent)
            result = ctx._client.subscribe(["selection_sent", "selection_changed"])
            accepted = result.get("accepted", []) if isinstance(result, dict) else []
            ctx._interaction_subscription_active = "selection_sent" in accepted
            ctx._event_subscription_error = ""
        except Exception as exc:
            ctx._event_subscription_error = str(exc)
            ctx._interaction_subscription_active = False

    def ensure_interaction_subscription(self) -> None:
        ctx = self.ctx
        if ctx._client is None and not self.ensure_connected():
            return
        if ctx._client is not None and not ctx._interaction_subscription_active:
            self.subscribe_interaction_events()
        self.subscribe_registered_session_events()
        self.catch_up_send_journals()

    def catch_up_send_journals(self) -> None:
        """Replay SENDs journaled by the plugin while nobody was listening.

        The plugin writes every explicit SEND to
        <registry_root>/journals/<session>.send.jsonl (durability); the
        live selection_sent event is just the low-latency path. An mtime
        cache keeps this scan cheap enough to run before every
        interaction tool call.
        """
        ctx = self.ctx
        try:
            journals_dir = Path(ctx._sessions.root) / "journals"
            if not journals_dir.exists():
                return
            for path in journals_dir.glob("*.send.jsonl"):
                session_id = path.name[: -len(".send.jsonl")]
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if ctx._journal_mtimes.get(session_id) == mtime:
                    continue
                with ctx._context_lock:
                    added = ctx._context.catch_up_from_journal(path, session_id)
                ctx._journal_mtimes[session_id] = mtime
                if added:
                    # Surfaced via klink.status (journal_catchup_counts).
                    ctx._journal_catchup_counts[session_id] = (
                        ctx._journal_catchup_counts.get(session_id, 0) + added
                    )
        except Exception as exc:
            ctx._event_subscription_error = str(exc)

    def on_selection_changed(self, data: dict) -> None:
        # Passive selection memory is intentionally demoted for the MVP.
        # Keep this handler installed for diagnostics/future ring buffer,
        # but do not allocate user-facing ids from ordinary selection moves.
        return

    def on_selection_sent(self, data: dict) -> None:
        ctx = self.ctx
        try:
            with ctx._context_lock:
                ctx._context.record_selection_sent(data)
        except Exception as exc:
            ctx._event_subscription_error = str(exc)

    def subscribe_registered_session_events(self) -> None:
        from .bridge import KLinkClient

        ctx = self.ctx
        for record in ctx._sessions.list_sessions():
            session_id = str(record.get("session_id") or "")
            if not session_id or session_id in ctx._session_event_active:
                continue
            try:
                host = str(record.get("host") or DEFAULT_KLINK_HOST)
                port = int(record.get("rpc_port") or record.get("port"))
            except Exception as exc:
                ctx._session_event_errors[session_id] = str(exc)
                continue

            # The primary client already subscribes through subscribe_interaction_events.
            if ctx._client is not None and host == ctx._host and port == ctx._port:
                ctx._session_event_active.add(session_id)
                continue

            try:
                client = KLinkClient(
                    host=host,
                    port=port,
                    connect_timeout=2.0,
                    default_call_timeout=ctx._call_timeout,
                ).connect()

                def _handler(data: dict, rec=dict(record)) -> None:
                    event = dict(data)
                    event.setdefault("klayout_session_id", rec.get("session_id"))
                    event.setdefault("klayout_rpc_port", rec.get("rpc_port") or rec.get("port"))
                    event.setdefault("klayout_pid", rec.get("pid"))
                    event.setdefault("layout_path", rec.get("layout_path"))
                    event.setdefault("active_cell", rec.get("active_cell"))
                    self.on_selection_sent(event)

                client.on("selection_sent", _handler)
                result = client.subscribe(["selection_sent"])
                accepted = result.get("accepted", []) if isinstance(result, dict) else []
                if "selection_sent" not in accepted:
                    raise RuntimeError("selection_sent subscription was not accepted")
                ctx._session_event_clients[session_id] = client
                ctx._session_event_active.add(session_id)
                ctx._session_event_errors.pop(session_id, None)
            except Exception as exc:
                ctx._session_event_errors[session_id] = str(exc)
                try:
                    client.close()  # type: ignore[name-defined]
                except Exception:
                    pass

    def close_session_event_clients(self) -> None:
        ctx = self.ctx
        for client in list(ctx._session_event_clients.values()):
            try:
                client.close()
            except Exception:
                pass
        ctx._session_event_clients.clear()
        ctx._session_event_active.clear()

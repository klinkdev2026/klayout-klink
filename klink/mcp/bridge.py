"""
KLinkMCPBridge — coordinator for the klink RPC <-> MCP tool bridge.

The bridge owns the mutable connection/session/interaction state and acts as
the shared ``ctx``. The actual work lives in composed sub-objects —
``Connection`` (connect lifecycle + event/journal wiring), ``ToolRegistry``
(tools/list + dispatch), ``SessionOps`` (session resolve/label/registry), and
``Diagnostics`` (status + version handshake) — and the local MCP tool handlers
in ``local_tools/<domain>.py``, which receive this bridge as ``ctx``. The thin
delegators and the few domain helper methods below are the surface those
handlers call.
"""

from __future__ import annotations

import threading
from typing import List, Optional

from ..client import KLinkClient
from .config import (
    DEFAULT_CALL_TIMEOUT,
    DEFAULT_KLINK_HOST,
    DEFAULT_KLINK_PORT,
    DEFAULT_LONG_CALL_TIMEOUT,
)
from .connection import Connection
from .diagnostics import Diagnostics
from .interaction_context import InteractionContextStore
from .local_tools._helpers import _with_age
from .registry import ToolRegistry
from .session import SessionOps
from .session_registry import SessionRegistry


class KLinkMCPBridge:
    def __init__(
        self,
        profiles: Optional[List[str]] = None,
        host: str = DEFAULT_KLINK_HOST,
        port: int = DEFAULT_KLINK_PORT,
        call_timeout: float = DEFAULT_CALL_TIMEOUT,
        long_call_timeout: float = DEFAULT_LONG_CALL_TIMEOUT,
        session_id: str | None = None,
        context_root=None,
        registry_root=None,
    ):
        self._profiles = profiles or ["read", "write"]
        self._host = host
        self._port = port
        self._call_timeout = call_timeout
        self._long_call_timeout = long_call_timeout
        self._client: Optional[KLinkClient] = None
        self._tools: List[dict] = []
        self._method_specs: dict[str, dict] = {}
        self._last_error: str = ""
        self._last_connect_attempt: float | None = None
        self._connect_count = 0
        self._last_call: dict | None = None
        self._context = InteractionContextStore(session_id=session_id, root=context_root)
        self._context_lock = threading.Lock()
        self._sessions = SessionRegistry(root=registry_root)
        self._active_session_id: str | None = None
        self._session_event_clients: dict[str, KLinkClient] = {}
        self._session_event_errors: dict[str, str] = {}
        self._session_event_active: set[str] = set()
        self._event_subscription_error = ""
        self._journal_mtimes: dict[str, float] = {}
        self._journal_catchup_counts: dict[str, int] = {}
        self._interaction_subscription_active = False
        self._pending_transfers: dict[str, dict] = {}

        # Composed sub-objects. The bridge holds the mutable state above and
        # acts as the shared `ctx`; these operate on it. Local-tool handlers
        # live in local_tools/<domain>.py and receive this bridge as ctx,
        # reaching the extracted logic through the thin delegators below.
        self.connection = Connection(self)
        self.registry = ToolRegistry(self)
        self.session_ops = SessionOps(self)
        self.diagnostics = Diagnostics(self)

    # ------------------------------------------------------------------
    # Lifecycle (delegated to Connection / ToolRegistry / SessionOps /
    # Diagnostics — handlers and helpers below call these thin wrappers)
    # ------------------------------------------------------------------
    def connect(self) -> None:
        self.connection.connect()

    def ensure_connected(self) -> bool:
        return self.connection.ensure_connected()

    def reconnect(self) -> bool:
        return self.connection.reconnect()

    def close(self) -> None:
        self.connection.close()

    def _close_client(self, *, clear_tools: bool) -> None:
        self.connection.close_client(clear_tools=clear_tools)

    def status(self) -> dict:
        return self.diagnostics.status()

    def _ensure_interaction_subscription(self) -> None:
        self.connection.ensure_interaction_subscription()

    # ------------------------------------------------------------------
    # MCP tool listing
    # ------------------------------------------------------------------
    def list_tools(self) -> dict:
        return self.registry.list_tools()

    # ------------------------------------------------------------------
    # MCP tool call
    # ------------------------------------------------------------------
    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.registry.call_tool(name, arguments)

    def _connect_session_client(self, session_id: str) -> KLinkClient:
        return self.session_ops.connect_session_client(session_id)

    def _session_labels(self) -> dict:
        return self.session_ops.session_labels()

    def _label_for_session(self, session_id: str) -> dict:
        return self.session_ops.label_for_session(session_id)

    def _write_session_label(
        self,
        session_id: str,
        label: str,
        aliases: list[str],
        description: str,
    ) -> dict:
        return self.session_ops.write_session_label(session_id, label, aliases, description)

    def _enrich_sessions_with_labels(self, sessions: list[dict]) -> list[dict]:
        return self.session_ops.enrich_sessions_with_labels(sessions)

    def _resolve_session_id(self, query: str) -> str:
        return self.session_ops.resolve_session_id(query)

    def _resolve_session_query(self, query: str) -> dict:
        return self.session_ops.resolve_session_query(query)

    def _photonics_spec_root(self) -> str | None:
        import os

        root = os.environ.get("KLINK_CONTEXT_ROOT")
        if not root:
            return None
        from pathlib import Path

        return str(Path(root).parent / "specs")

    def _photonics_style(self, arguments: dict):
        from ..domains.photonics.net_intent import RouteStyle

        return RouteStyle(
            width_um=arguments.get("width_um"),
            radius_um=arguments.get("radius_um"),
            separation_um=float(arguments.get("separation_um", 3.0)),
            route_layer=arguments.get("route_layer"),
        )

    def _session_scoped_client(self, session: str | None):
        """Return (client, close_after) honoring an optional session arg."""
        if session:
            return self._connect_session_client(str(session)), True
        if not self.ensure_connected() or self._client is None:
            raise RuntimeError(
                "not connected to klink: %s" % (self._last_error or "unknown error")
            )
        return self._client, False

    def _structdevice_spec_root(self) -> str:
        return self._photonics_spec_root() or ".klink/specs"

    def _structdevice_connectivity(self, arguments: dict):
        from ..domains.structdevice.connectivity import ConnectivitySpec

        conductors = arguments.get("conductors")
        vias = arguments.get("vias")
        if not conductors and not vias:
            raise ValueError(
                "no connectivity given; klink ships no process. Pass conductors "
                "(your routing/conductor layers, e.g. ['101/0','104/0','106/0']) "
                "and vias (each [lower, cut, upper], e.g. "
                "[['101/0','102/0','104/0']]) for YOUR process. "
                "See your project's pdk.py (scaffolded by `klink init`).")
        return ConnectivitySpec(
            conductors=tuple(conductors or ()),
            vias=tuple(tuple(v) for v in (vias or ())),
        ).validated()

    def _interaction_context(self, arguments: dict) -> dict:
        self._ensure_interaction_subscription()
        include_current = bool(arguments.get("include_current_selection", True))
        current = None
        if include_current and self.ensure_connected() and self._client is not None:
            try:
                current = self._client.selection_get()
            except Exception as exc:
                current = {"error": str(exc)}
        latest = self._context.latest()
        return {
            "session": self._context.status(),
            "current_selection": current,
            "recent_selection": _with_age(latest),
            "recent_selections": [_with_age(r) for r in self._context.recent()],
        }

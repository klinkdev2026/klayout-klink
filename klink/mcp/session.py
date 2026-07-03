"""Session label / resolve / registry helpers for KLinkMCPBridge.

Operates on the bridge as `ctx` (shared state holder). The session/transfer
MCP tool handlers still live on the bridge and call these via thin delegators.
"""

from __future__ import annotations

from ..transfer import TransferError
from .config import DEFAULT_KLINK_HOST

# KLinkClient is resolved from the bridge module at call time so it stays the
# single monkeypatch seam (klink.mcp.bridge.KLinkClient) and to avoid a
# bridge<->session import cycle.


class SessionOps:
    def __init__(self, ctx):
        self.ctx = ctx

    def connect_session_client(self, session_id: str):
        from .bridge import KLinkClient

        session_id = self.resolve_session_id(session_id)
        record = self.ctx._sessions.get(session_id)
        if record is None:
            raise TransferError(f"unknown or stale session id: {session_id}")
        host = str(record.get("host") or DEFAULT_KLINK_HOST)
        port = int(record.get("rpc_port") or record.get("port"))
        client = KLinkClient(
            host=host,
            port=port,
            default_call_timeout=self.ctx._call_timeout,
        )
        client.connect()
        return client

    def session_labels(self) -> dict:
        labels = self.ctx._sessions.read_state().get("session_labels", {})
        return labels if isinstance(labels, dict) else {}

    def label_for_session(self, session_id: str) -> dict:
        labels = self.session_labels()
        entry = labels.get(session_id, {})
        return entry if isinstance(entry, dict) else {}

    def write_session_label(
        self,
        session_id: str,
        label: str,
        aliases: list[str],
        description: str,
    ) -> dict:
        state = self.ctx._sessions.read_state()
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
        try:
            state = self.ctx._sessions.write_state({"session_labels": labels})
            return {"ok": True, "session_id": session_id, **entry, "registry_state": state}
        except OSError:
            client = self.connect_session_client(session_id)
            try:
                return client.session_label_set(
                    session_id,
                    label,
                    aliases=aliases,
                    description=description or None,
                )
            finally:
                client.close()

    def enrich_sessions_with_labels(self, sessions: list[dict]) -> list[dict]:
        labels = self.session_labels()
        out = []
        for session in sessions:
            item = dict(session)
            entry = labels.get(str(item.get("session_id")), {})
            if isinstance(entry, dict):
                item["label"] = entry.get("label")
                item["aliases"] = list(entry.get("aliases") or [])
                item["description"] = entry.get("description")
            out.append(item)
        return out

    def resolve_session_id(self, query: str) -> str:
        query = str(query or "").strip()
        if not query:
            raise TransferError("session query is required")
        if self.ctx._sessions.get(query) is not None:
            return query
        resolved = self.resolve_session_query(query)
        return str(resolved["session_id"])

    def resolve_session_query(self, query: str) -> dict:
        q = str(query or "").strip()
        q_norm = q.casefold()
        if not q:
            raise TransferError("session query is required")
        sessions = self.ctx._sessions.list_sessions(include_stale=False)
        labels = self.session_labels()
        matches = []
        for record in sessions:
            session_id = str(record.get("session_id") or "")
            if session_id == q:
                return {"session_id": session_id, "match_type": "session_id", "session": self.enrich_sessions_with_labels([record])[0]}
            entry = labels.get(session_id, {})
            label = str(entry.get("label") or "")
            aliases = [str(a) for a in (entry.get("aliases") or [])]
            active_cell = str(record.get("active_cell") or "")
            top_cells = [str(c) for c in (record.get("top_cells") or [])]
            candidates = []
            if label:
                candidates.append(("label", label))
            candidates.extend(("alias", alias) for alias in aliases)
            if active_cell:
                candidates.append(("active_cell", active_cell))
            candidates.extend(("top_cell", cell) for cell in top_cells)
            for match_type, value in candidates:
                if value.casefold() == q_norm:
                    enriched = self.enrich_sessions_with_labels([record])[0]
                    matches.append({"session_id": session_id, "match_type": match_type, "session": enriched})
        if not matches:
            raise TransferError(f"unknown session query: {query}")
        unique = {m["session_id"]: m for m in matches}
        if len(unique) > 1:
            ids = ", ".join(sorted(unique))
            raise TransferError(f"ambiguous session query {query!r}: {ids}")
        return next(iter(unique.values()))

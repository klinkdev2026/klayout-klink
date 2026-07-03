"""Local KLayout session registry used by MCP-side routing tools."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any


STALE_AFTER_S = 15.0


def default_registry_root() -> Path:
    import os
    import sys

    configured = os.environ.get("KLINK_REGISTRY_ROOT")
    if configured:
        return Path(configured)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "klink" / "registry"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "klink" / "registry"
    else:
        base = os.environ.get("XDG_STATE_HOME")
        if base:
            return Path(base) / "klink" / "registry"
        return Path.home() / ".local" / "state" / "klink" / "registry"

    return Path.cwd() / ".klink" / "registry"


class SessionRegistry:
    def __init__(self, root: str | Path | None = None, *, stale_after_s: float = STALE_AFTER_S):
        self.root = Path(root) if root is not None else default_registry_root()
        self.sessions_dir = self.root / "sessions"
        self.state_path = self.root / "state.json"
        self.stale_after_s = float(stale_after_s)

    def list_sessions(self, *, include_stale: bool = False) -> list[dict[str, Any]]:
        now = time.time()
        out: list[dict[str, Any]] = []
        if not self.sessions_dir.exists():
            return []
        for path in sorted(self.sessions_dir.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(record, dict):
                continue
            last_seen = _as_float(record.get("last_seen"))
            stale = last_seen is None or (now - last_seen) > self.stale_after_s
            record["stale"] = stale
            if last_seen is not None:
                record["age_s"] = round(now - last_seen, 3)
            if include_stale or not stale:
                out.append(record)
        return out

    def get(self, session_id: str, *, include_stale: bool = False) -> dict[str, Any] | None:
        for record in self.list_sessions(include_stale=include_stale):
            if record.get("session_id") == session_id:
                return record
        return None

    def read_state(self) -> dict[str, Any]:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            return state if isinstance(state, dict) else {}
        except Exception:
            return {}

    def write_state(self, updates: dict[str, Any]) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        state = self.read_state()
        state.update(updates)
        state["updated_at"] = time.time()
        _atomic_write_json(self.state_path, state)
        return state


def _as_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)

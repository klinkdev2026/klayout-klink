"""Local session registry writer for KLayout-side klink instances."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path


def default_registry_root() -> Path:
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


class KLayoutSessionRegistry:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else default_registry_root()
        self.sessions_dir = self.root / "sessions"
        self.state_path = self.root / "state.json"

    def write_session(self, record: dict) -> dict:
        payload = dict(record)
        payload["last_seen"] = time.time()
        session_id = str(payload.get("session_id") or "")
        if not session_id:
            raise ValueError("session_id is required")
        _atomic_write_json(self.sessions_dir / f"{session_id}.json", payload)
        return payload

    def remove_session(self, session_id: str) -> None:
        try:
            (self.sessions_dir / f"{session_id}.json").unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass

    def write_state(self, updates: dict) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        state = self.read_state()
        state.update(updates)
        state["updated_at"] = time.time()
        _atomic_write_json(self.state_path, state)
        return state

    def read_state(self) -> dict:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            return state if isinstance(state, dict) else {}
        except Exception:
            return {}


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)

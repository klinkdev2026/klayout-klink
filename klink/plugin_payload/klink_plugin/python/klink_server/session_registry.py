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


_TMP_SWEEP_MAX_AGE_S = 3600.0
_REPLACE_ATTEMPTS = 5
_SWEPT_DIRS: set = set()


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _sweep_stale_tmp_once(path.parent)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        _replace_with_retry(tmp, path)
    except BaseException:
        # A failed write must never leave its tmp file behind (Windows
        # readers holding the target open make replace() fail transiently).
        try:
            tmp.unlink()
        except OSError:
            pass
        _sweep_stale_tmp(path.parent)
        raise


def _replace_with_retry(tmp: Path, path: Path, attempts: int = _REPLACE_ATTEMPTS) -> None:
    for attempt in range(1, attempts + 1):
        try:
            tmp.replace(path)
            return
        except OSError:  # includes PermissionError ([WinError 5] on Windows)
            if attempt >= attempts:
                raise
            time.sleep(0.02 * attempt)


def _sweep_stale_tmp_once(directory: Path) -> None:
    key = str(directory)
    if key in _SWEPT_DIRS:
        return
    _SWEPT_DIRS.add(key)
    _sweep_stale_tmp(directory)


def _sweep_stale_tmp(directory: Path, max_age_s: float = _TMP_SWEEP_MAX_AGE_S) -> int:
    """Remove ``*.tmp`` files in ``directory`` older than ``max_age_s``.

    Tolerant of every error: another process may sweep the same file.
    Returns the number of files removed by this call.
    """
    removed = 0
    try:
        candidates = list(Path(directory).glob("*.tmp"))
    except OSError:
        return 0
    cutoff = time.time() - float(max_age_s)
    for candidate in candidates:
        try:
            if not candidate.is_file() or candidate.stat().st_mtime >= cutoff:
                continue
            candidate.unlink()
            removed += 1
        except OSError:
            continue
    return removed

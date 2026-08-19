"""Sidecar persistence for Intents and Runs (docs/REGION_INTENT_DESIGN.md §11).

Deliberately minimal: two durable object kinds only.

* ``intents/<intent_id>.json``  — written via same-directory temp file +
  flush + ``os.replace`` (a killed process can never leave a half-written
  canonical file).
* ``runs/<run_id>.jsonl``       — append-only events, flushed per event.
* ``project.lock``              — MINIMAL exclusive writer lock: atomic
  ``O_CREAT|O_EXCL`` create. No preemption, no merge; a stale lock (dead
  pid) is only recovered by the user explicitly deleting the file — the
  error says exactly that.
"""

from __future__ import annotations

import atexit
import json
import os
import time
import uuid
from pathlib import Path


class StoreLockedError(RuntimeError):
    pass


class LayoutIntentStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.intents_dir = self.root / "intents"
        self.runs_dir = self.root / "runs"
        self.lock_path = self.root / "project.lock"
        self._lock_fd: int | None = None

    # -- lock ---------------------------------------------------------------

    def acquire_lock(self) -> None:
        if self._lock_fd is not None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.lock_path),
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = ""
            try:
                holder = self.lock_path.read_text(encoding="utf-8").strip()
            except Exception:
                pass
            raise StoreLockedError(
                "project sidecar is locked by another writer (%s at %s). "
                "If that process is dead, delete the lock file yourself and "
                "retry — klink never auto-preempts: %s"
                % (holder or "unknown", self.lock_path, self.lock_path))
        os.write(fd, ("pid=%d start=%s" % (
            os.getpid(), time.strftime("%Y-%m-%dT%H:%M:%S"))).encode("utf-8"))
        os.fsync(fd)
        self._lock_fd = fd
        atexit.register(self.release_lock)

    def release_lock(self) -> None:
        if self._lock_fd is None:
            return
        try:
            os.close(self._lock_fd)
        except Exception:
            pass
        self._lock_fd = None
        try:
            self.lock_path.unlink()
        except Exception:
            pass

    # -- intents ------------------------------------------------------------

    @staticmethod
    def new_id(prefix: str) -> str:
        return "%s_%s" % (prefix, uuid.uuid4().hex[:8])

    def write_intent(self, intent: dict) -> None:
        self.acquire_lock()
        self.intents_dir.mkdir(parents=True, exist_ok=True)
        path = self.intents_dir / ("%s.json" % intent["intent_id"])
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(intent, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def read_intent(self, intent_id: str) -> dict | None:
        path = self.intents_dir / ("%s.json" % intent_id)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_intents(self) -> list[dict]:
        if not self.intents_dir.is_dir():
            return []
        out = []
        for path in sorted(self.intents_dir.glob("int_*.json")):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                out.append({"intent_id": path.stem, "error": "unreadable"})
        return out

    # -- runs ---------------------------------------------------------------

    def append_run_event(self, run_id: str, event: dict) -> None:
        self.acquire_lock()
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        event = dict(event)
        event.setdefault("run_id", run_id)
        event.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S"))
        path = self.runs_dir / ("%s.jsonl" % run_id)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
            fh.write("\n")
            fh.flush()

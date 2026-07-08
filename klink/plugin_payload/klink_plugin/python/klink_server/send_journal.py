"""Durable local journal for explicit SEND events.

Design: the selection_sent EVENT is only a live push
to currently-subscribed listeners; durability lives here. Every explicit
SEND is appended to a per-session JSONL journal regardless of whether any
listener is connected, with a plugin-assigned monotonic ``send_seq``.
Consumers (the MCP bridge on reconnect, non-MCP agent harnesses, the
workbench) read the journal as the source of truth and use the event
stream only as a low-latency signal.

Location: ``<registry_root>/journals/<session_id>.send.jsonl`` — derived
from the same KLINK_REGISTRY_ROOT convention as the session registry, so
plugin and external consumers resolve identical paths and tests isolate
via the env var.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .log import get_logger
from .session_registry import default_registry_root

_log = get_logger("send_journal")

_MAX_BYTES = 5_000_000  # rotate to .1 beyond this; one backup kept


def journal_path(session_id: str) -> Path:
    return default_registry_root() / "journals" / f"{session_id}.send.jsonl"


class SendJournal:
    """Append-only JSONL journal with a monotonic per-session sequence."""

    def __init__(self, session_id: str):
        self.session_id = str(session_id)
        self.path = journal_path(self.session_id)
        self._next_seq = self._scan_last_seq() + 1

    def _scan_last_seq(self) -> int:
        try:
            if not self.path.exists():
                return 0
            last = 0
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        seq = int(json.loads(line).get("send_seq", 0))
                        last = max(last, seq)
                    except Exception:
                        continue
            return last
        except Exception as exc:
            _log.warning("journal scan failed for %s: %s", self.path, exc)
            return 0

    def append(self, data: dict) -> int:
        """Append one SEND record; returns its send_seq.

        Raises on write failure — callers decide how to surface it; a SEND
        that cannot be journaled must not be silently reported as durable.
        """
        seq = self._next_seq
        record = {"send_seq": seq, "journal_ts": time.time(), **data}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed()
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._next_seq = seq + 1
        return seq

    def _rotate_if_needed(self) -> None:
        try:
            if self.path.exists() and self.path.stat().st_size > _MAX_BYTES:
                backup = self.path.with_suffix(".jsonl.1")
                if backup.exists():
                    backup.unlink()
                self.path.replace(backup)
                _log.info("rotated send journal %s", self.path.name)
        except Exception as exc:
            _log.warning("journal rotation failed: %s", exc)

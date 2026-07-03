"""Session-scoped interaction context for the MCP runtime.

This module intentionally lives outside the KLayout plugin. The plugin emits
facts and events; this runtime records session context for the agent.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_RECENT_LIMIT = 5
FULL_ITEM_LIMIT = 50
MEDIUM_ITEM_LIMIT = 500
SAMPLE_ITEM_LIMIT = 20


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_session_id(value: str | None) -> str:
    raw = (value or "").strip() or "default"
    allowed = []
    for ch in raw:
        if ch.isalnum() or ch in ("-", "_", "."):
            allowed.append(ch)
        else:
            allowed.append("_")
    return "".join(allowed)[:120] or "default"


def _default_root() -> Path:
    env = os.environ.get("KLINK_CONTEXT_ROOT")
    if env:
        return Path(env)
    return Path.cwd() / ".klink" / "sessions"


def _layer_key(item: dict[str, Any]) -> str:
    layer = item.get("layer")
    datatype = item.get("datatype", 0)
    if layer is not None:
        return f"{layer}/{datatype}"
    if item.get("layer_index") is not None:
        return f"layer_index:{item['layer_index']}"
    return "unknown"


def _item_bbox_dbu(item: dict[str, Any]) -> list[int] | None:
    bbox = item.get("bbox_dbu")
    if isinstance(bbox, list) and len(bbox) == 4:
        return [int(v) for v in bbox]
    points = item.get("points_dbu")
    if isinstance(points, list) and points:
        xs = [int(pt[0]) for pt in points]
        ys = [int(pt[1]) for pt in points]
        return [min(xs), min(ys), max(xs), max(ys)]
    return None


def _union_bbox(boxes: list[list[int]]) -> list[int] | None:
    if not boxes:
        return None
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


@dataclass
class InteractionContextStore:
    session_id: str | None = None
    root: Path | None = None
    recent_limit: int = DEFAULT_RECENT_LIMIT
    full_item_limit: int = FULL_ITEM_LIMIT
    medium_item_limit: int = MEDIUM_ITEM_LIMIT
    sample_item_limit: int = SAMPLE_ITEM_LIMIT

    def __post_init__(self) -> None:
        self.session_id = _safe_session_id(self.session_id or os.environ.get("KLINK_SESSION_ID"))
        self.root = self.root or _default_root()
        self.session_dir = Path(self.root) / self.session_id
        self.log_path = self.session_dir / "interaction_context.jsonl"
        self._records: dict[str, dict[str, Any]] = {}
        self._labels: dict[str, dict[str, str]] = {}
        self._sequence = 0
        self._load()

    def status(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "session_dir": str(self.session_dir),
            "log_path": str(self.log_path),
            "selection_count": len(self._records),
            "latest_sequence": self._sequence,
            "recent_limit": self.recent_limit,
        }

    def record_selection_changed(self, event: dict[str, Any]) -> dict[str, Any]:
        return self._record_selection(event, capture_reason="selection_changed")

    def record_selection_sent(self, event: dict[str, Any]) -> dict[str, Any]:
        # Dedupe against the plugin-side send journal: a SEND may arrive
        # both as a live event and via journal catch-up; the plugin's
        # per-session send_seq is the identity.
        seq = event.get("send_seq")
        source_session = event.get("klayout_session_id")
        if seq is not None and source_session is not None:
            if int(seq) <= self.max_send_seq(str(source_session)):
                return {
                    "type": "selection_ignored",
                    "reason": "duplicate_send_seq",
                    "send_seq": int(seq),
                    "timestamp": _now_iso(),
                    "session_id": self.session_id,
                }
        return self._record_selection(event, capture_reason="selection_sent")

    def max_send_seq(self, klayout_session_id: str) -> int:
        best = 0
        for record in self._records.values():
            if record.get("klayout_session_id") == klayout_session_id:
                try:
                    best = max(best, int(record.get("send_seq") or 0))
                except (TypeError, ValueError):
                    continue
        return best

    def catch_up_from_journal(self, journal_path, klayout_session_id: str) -> int:
        """Record journal entries newer than what this store has seen.

        The plugin journals every SEND durably (send_journal.py); this
        replays entries missed while no listener was subscribed. Returns
        the number of records added.
        """
        path = Path(journal_path)
        if not path.exists():
            return 0
        added = 0
        floor = self.max_send_seq(klayout_session_id)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                seq = int(entry.get("send_seq") or 0)
            except (TypeError, ValueError):
                continue
            if seq <= floor:
                continue
            entry.setdefault("klayout_session_id", klayout_session_id)
            entry.setdefault("caught_up_from_journal", True)
            result = self._record_selection(entry, capture_reason="selection_sent")
            if result.get("type") == "selection":
                added += 1
                floor = max(floor, seq)
        return added

    def _record_selection(self, event: dict[str, Any], *, capture_reason: str) -> dict[str, Any]:
        count = int(event.get("count") or len(event.get("items") or []))
        if count <= 0:
            return {
                "type": "selection_ignored",
                "reason": "empty_selection",
                "timestamp": _now_iso(),
                "session_id": self.session_id,
            }
        self._sequence += 1
        record = self._selection_record(event, self._sequence)
        record["capture_reason"] = capture_reason
        self._records[record["id"]] = record
        self._append(record)
        return record

    def latest(self) -> dict[str, Any] | None:
        records = self._selection_records()
        return records[-1] if records else None

    def recent(self, limit: int | None = None) -> list[dict[str, Any]]:
        n = int(limit or self.recent_limit)
        return self._selection_records()[-n:]

    def get(self, selection_id: str) -> dict[str, Any] | None:
        return self._records.get(selection_id)

    def label(self, selection_id: str, label: str | None, description: str | None = None) -> dict[str, Any]:
        if selection_id not in self._records:
            raise KeyError(selection_id)
        payload = {
            "type": "selection_label",
            "id": selection_id,
            "timestamp": _now_iso(),
            "label": label or "",
            "description": description or "",
        }
        self._labels[selection_id] = {
            "label": payload["label"],
            "description": payload["description"],
        }
        self._records[selection_id]["label"] = payload["label"]
        self._records[selection_id]["description"] = payload["description"]
        self._append(payload)
        return self._records[selection_id]

    def clear_session(self) -> dict[str, Any]:
        old_count = len(self._records)
        self._records.clear()
        self._labels.clear()
        self._sequence = 0
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")
        return {"cleared": True, "old_selection_count": old_count, **self.status()}

    def _selection_records(self) -> list[dict[str, Any]]:
        return sorted(self._records.values(), key=lambda r: int(r.get("sequence") or 0))

    def _selection_record(self, event: dict[str, Any], sequence: int) -> dict[str, Any]:
        items = list(event.get("items") or [])
        count = int(event.get("count") or len(items))
        layers: dict[str, int] = {}
        for item in items:
            key = _layer_key(item)
            layers[key] = layers.get(key, 0) + 1

        bboxes = [bbox for bbox in (_item_bbox_dbu(item) for item in items) if bbox]
        bbox_dbu = _union_bbox(bboxes)
        if bbox_dbu is None and isinstance(event.get("bbox_dbu"), list):
            bbox_dbu = [int(v) for v in event["bbox_dbu"]]

        if count <= self.full_item_limit:
            detail_level = "full"
            stored_items = items
            too_large = False
            items_available = True
        elif count <= self.medium_item_limit:
            detail_level = "sampled"
            stored_items = items[: self.sample_item_limit]
            too_large = False
            items_available = True
        else:
            detail_level = "summary_only"
            stored_items = []
            too_large = True
            items_available = False

        record = {
            "type": "selection",
            "id": f"sel_{sequence:04d}",
            "session_id": self.session_id,
            "sequence": sequence,
            "timestamp": _now_iso(),
            "capture_reason": event.get("capture_reason") or "selection_changed",
            "cell": event.get("cell") or event.get("active_cell"),
            "count": count,
            "bbox_dbu": bbox_dbu,
            "layers": layers,
            "detail_level": detail_level,
            "items_available": items_available,
            "too_large": too_large,
            "items": stored_items,
        }
        for key in (
            "klayout_session_id",
            "klayout_rpc_port",
            "klayout_pid",
            "layout_path",
            "active_cell",
            "send_seq",
            "caught_up_from_journal",
        ):
            if event.get(key) is not None:
                record[key] = event.get(key)
        return record

    def _append(self, record: dict[str, Any]) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
            f.write("\n")

    def _load(self) -> None:
        if not self.log_path.exists():
            return
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("type") == "selection":
                sid = record.get("id")
                if not sid:
                    continue
                self._records[sid] = record
                self._sequence = max(self._sequence, int(record.get("sequence") or 0))
            elif record.get("type") == "selection_label":
                sid = record.get("id")
                if sid and sid in self._records:
                    self._records[sid]["label"] = record.get("label", "")
                    self._records[sid]["description"] = record.get("description", "")
                    self._labels[sid] = {
                        "label": record.get("label", ""),
                        "description": record.get("description", ""),
                    }


def age_seconds(record: dict[str, Any]) -> float | None:
    timestamp = record.get("timestamp")
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, time.time() - parsed.timestamp())

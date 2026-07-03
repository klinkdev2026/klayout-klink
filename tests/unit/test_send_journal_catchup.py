"""SEND journal catch-up and dedupe in the MCP interaction store.

The plugin journals every explicit SEND durably (send_journal.py); the
store replays missed entries on demand and dedupes live events against
the plugin-assigned send_seq.
"""

from __future__ import annotations

import json

from klink.mcp.interaction_context import InteractionContextStore


def _journal_entry(seq: int, name: str) -> dict:
    return {
        "send_seq": seq,
        "journal_ts": 1000.0 + seq,
        "capture_reason": "selection_sent",
        "cell": "LOOP",
        "count": 1,
        "items": [{"kind": "shape", "bbox_dbu": [0, 0, 10, 10], "name": name}],
        "klayout_session_id": "klayout-8766",
    }


def _write_journal(path, entries) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


def test_catch_up_replays_missed_sends_and_is_idempotent(tmp_path):
    store = InteractionContextStore(session_id="t1", root=tmp_path / "ctx")
    journal = tmp_path / "journals" / "klayout-8766.send.jsonl"
    _write_journal(journal, [_journal_entry(i, f"s{i}") for i in (1, 2, 3)])

    added = store.catch_up_from_journal(journal, "klayout-8766")
    assert added == 3
    assert store.max_send_seq("klayout-8766") == 3
    latest = store.latest()
    assert latest["send_seq"] == 3
    assert latest["caught_up_from_journal"] is True

    # Idempotent: nothing new in the journal -> nothing added.
    assert store.catch_up_from_journal(journal, "klayout-8766") == 0


def test_live_event_dedupes_against_journal_seq(tmp_path):
    store = InteractionContextStore(session_id="t2", root=tmp_path / "ctx")
    journal = tmp_path / "journals" / "klayout-8766.send.jsonl"
    _write_journal(journal, [_journal_entry(1, "a"), _journal_entry(2, "b")])
    assert store.catch_up_from_journal(journal, "klayout-8766") == 2

    # The same SEND arriving live (event raced the catch-up) is ignored.
    dup = store.record_selection_sent(_journal_entry(2, "b"))
    assert dup["type"] == "selection_ignored"
    assert dup["reason"] == "duplicate_send_seq"

    # A genuinely new live SEND is recorded and advances the floor.
    fresh = store.record_selection_sent(_journal_entry(3, "c"))
    assert fresh["type"] == "selection"
    assert store.max_send_seq("klayout-8766") == 3
    # Catch-up afterwards does not re-add the older entries.
    assert store.catch_up_from_journal(journal, "klayout-8766") == 0


def test_sessions_do_not_cross_contaminate(tmp_path):
    store = InteractionContextStore(session_id="t3", root=tmp_path / "ctx")
    journal_a = tmp_path / "journals" / "klayout-8765.send.jsonl"
    _write_journal(journal_a, [_journal_entry(5, "a")])
    entry = _journal_entry(5, "a")
    entry["klayout_session_id"] = "klayout-8765"
    _write_journal(journal_a, [entry])
    assert store.catch_up_from_journal(journal_a, "klayout-8765") == 1

    # Same seq number on a DIFFERENT KLayout session is not a duplicate.
    other = store.record_selection_sent(_journal_entry(5, "x"))
    assert other["type"] == "selection"


def test_events_without_send_seq_keep_working(tmp_path):
    # Old plugins (pre-journal) emit events without send_seq; they must
    # still record normally.
    store = InteractionContextStore(session_id="t4", root=tmp_path / "ctx")
    event = {"capture_reason": "selection_sent", "count": 1,
             "items": [{"kind": "shape", "bbox_dbu": [0, 0, 1, 1]}],
             "klayout_session_id": "klayout-8766"}
    record = store.record_selection_sent(event)
    assert record["type"] == "selection"
    assert "send_seq" not in record

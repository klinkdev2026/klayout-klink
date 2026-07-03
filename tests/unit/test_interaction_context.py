from __future__ import annotations

from klink.mcp.interaction_context import InteractionContextStore


def _event(count=1):
    return {
        "cell": "TOP",
        "count": count,
        "items": [
            {
                "kind": "shape",
                "layer": 1,
                "datatype": 0,
                "bbox_dbu": [i, 0, i + 1, 1],
            }
            for i in range(min(count, 60))
        ],
    }


def test_selection_ids_and_recent_window_persist_by_order(tmp_path):
    store = InteractionContextStore(session_id="s1", root=tmp_path)

    for i in range(6):
        store.record_selection_changed(_event(i + 1))

    assert [r["id"] for r in store.recent()] == [
        "sel_0002",
        "sel_0003",
        "sel_0004",
        "sel_0005",
        "sel_0006",
    ]

    reloaded = InteractionContextStore(session_id="s1", root=tmp_path)
    assert reloaded.latest()["id"] == "sel_0006"
    assert reloaded.record_selection_changed(_event())["id"] == "sel_0007"


def test_capture_policy_full_sampled_summary(tmp_path):
    store = InteractionContextStore(
        session_id="caps",
        root=tmp_path,
        full_item_limit=2,
        medium_item_limit=4,
        sample_item_limit=1,
    )

    full = store.record_selection_changed(_event(2))
    sampled = store.record_selection_changed(_event(4))
    huge = store.record_selection_changed(_event(5))

    assert full["detail_level"] == "full"
    assert len(full["items"]) == 2
    assert sampled["detail_level"] == "sampled"
    assert len(sampled["items"]) == 1
    assert huge["detail_level"] == "summary_only"
    assert huge["items_available"] is False
    assert huge["too_large"] is True


def test_label_and_clear_session(tmp_path):
    store = InteractionContextStore(session_id="labels", root=tmp_path)
    record = store.record_selection_changed(_event())

    labeled = store.label(record["id"], "bad corner", "hybrid transition is wrong")

    assert labeled["label"] == "bad corner"
    assert labeled["description"] == "hybrid transition is wrong"
    reloaded = InteractionContextStore(session_id="labels", root=tmp_path)
    assert reloaded.get(record["id"])["label"] == "bad corner"

    cleared = reloaded.clear_session()
    assert cleared["cleared"] is True
    assert reloaded.recent() == []


def test_empty_selection_event_does_not_allocate_id(tmp_path):
    store = InteractionContextStore(session_id="empty", root=tmp_path)

    ignored = store.record_selection_changed({"cell": "TOP", "count": 0, "items": []})
    record = store.record_selection_changed(_event(1))

    assert ignored["type"] == "selection_ignored"
    assert record["id"] == "sel_0001"
    assert store.recent()[0]["id"] == "sel_0001"


def test_selection_record_preserves_klayout_session_metadata(tmp_path):
    store = InteractionContextStore(session_id="window-meta", root=tmp_path)
    event = _event(1)
    event.update({
        "klayout_session_id": "klayout-8766",
        "klayout_rpc_port": 8766,
        "klayout_pid": 12345,
        "layout_path": "C:/tmp/demo.gds",
        "active_cell": "TOP",
    })

    record = store.record_selection_sent(event)

    assert record["klayout_session_id"] == "klayout-8766"
    assert record["klayout_rpc_port"] == 8766
    assert record["klayout_pid"] == 12345
    assert record["layout_path"] == "C:/tmp/demo.gds"
    assert record["active_cell"] == "TOP"
    reloaded = InteractionContextStore(session_id="window-meta", root=tmp_path)
    assert reloaded.latest()["klayout_session_id"] == "klayout-8766"

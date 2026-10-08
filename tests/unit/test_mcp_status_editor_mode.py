"""`klink.status` lifts viewer mode to a top-level `editor_mode` block."""

from klink.mcp.diagnostics import _editor_mode_block


def test_viewer_mode_block_carries_instruction():
    b = _editor_mode_block({"editable": False, "editor_mode_next_action": "restart with -e"})
    assert b == {"editable": False, "next_action": "restart with -e"}


def test_viewer_mode_block_has_fallback_wording():
    b = _editor_mode_block({"editable": False})
    assert b["editable"] is False
    assert "klayout -e" in b["next_action"]


def test_editor_mode_block_is_quiet():
    assert _editor_mode_block({"editable": True}) == {"editable": True}


def test_unknown_and_stale_are_none_with_note():
    for hs in ({}, {"editable": None}, {"editable": True, "stale": True}):
        b = _editor_mode_block(hs)
        assert b["editable"] is None
        assert "ERR_VIEWER_MODE" in b["note"]

"""Version handshake comparison — pure, offline."""

from klink.handshake import evaluate_handshake


def _server(protocol):
    return {
        "server": "klink",
        "version": "0.1.0",
        "protocol": protocol,
        "klayout_version": "0.30.8",
    }


def test_matching_protocol_is_compatible_no_next_action():
    r = evaluate_handshake("0.1.0", 1, _server(1))
    assert r["compatible"] is True
    assert "next_action" not in r
    assert r["server_protocol"] == 1
    assert r["client_protocol"] == 1
    assert r["klayout_version"] == "0.30.8"


def test_older_plugin_tells_user_to_update_plugin():
    r = evaluate_handshake("0.1.0", 2, _server(1))
    assert r["compatible"] is False
    assert "Manage Packages" in r["next_action"]
    assert "older" in r["next_action"]


def test_newer_plugin_tells_user_to_upgrade_pip_package():
    r = evaluate_handshake("0.1.0", 1, _server(2))
    assert r["compatible"] is False
    assert "pip install -U klink" in r["next_action"]
    assert "newer" in r["next_action"]


def test_missing_server_info_names_missing_plugin():
    for missing in ({}, None, {"version": "0.1.0"}):
        r = evaluate_handshake("0.1.0", 1, missing)
        assert r["compatible"] is False
        assert r["server_protocol"] is None
        assert "missing or too old" in r["next_action"]


def test_compatible_equal_versions_no_skew():
    r = evaluate_handshake("0.1.0", 1, _server(1))
    assert r["compatible"] is True
    assert "version_skew" not in r
    assert "next_action" not in r


def test_compatible_older_plugin_version_flags_plugin_older():
    server = _server(1)
    server["version"] = "0.0.9"
    r = evaluate_handshake("0.1.0", 1, server)
    assert r["compatible"] is True
    assert r["version_skew"] == "plugin_older"
    assert "klink plugin install" in r["next_action"]


def test_compatible_newer_plugin_version_flags_client_older():
    server = _server(1)
    server["version"] = "0.2.0"
    r = evaluate_handshake("0.1.0", 1, server)
    assert r["compatible"] is True
    assert r["version_skew"] == "client_older"
    assert "pip install -U klayout-klink" in r["next_action"]


# --- viewer mode (plugin 0.6.4+ reports `editable` in hello) -------------

def test_viewer_mode_promoted_to_next_action_when_versions_fine():
    info = _server(1)
    info["editable"] = False
    r = evaluate_handshake("0.1.0", 1, info)
    assert r["compatible"] is True
    assert r["editable"] is False
    assert "klayout -e" in r["editor_mode_next_action"]
    # no version problem claims the slot, so the restart instruction is
    # the one thing an agent reading `next_action` sees
    assert r["next_action"] == r["editor_mode_next_action"]


def test_viewer_mode_uses_plugin_wording_when_given():
    info = _server(1)
    info["editable"] = False
    info["next_action"] = "plugin says: restart with -e"
    r = evaluate_handshake("0.1.0", 1, info)
    assert r["editor_mode_next_action"] == "plugin says: restart with -e"


def test_viewer_mode_does_not_mask_a_version_problem():
    info = _server(2)
    info["editable"] = False
    r = evaluate_handshake("0.1.0", 1, info)
    assert r["compatible"] is False
    assert "newer" in r["next_action"]            # version fix stays primary
    assert "klayout -e" in r["editor_mode_next_action"]


def test_editor_mode_and_old_plugin_are_quiet():
    info = _server(1)
    info["editable"] = True
    r = evaluate_handshake("0.1.0", 1, info)
    assert r["editable"] is True
    assert "editor_mode_next_action" not in r and "next_action" not in r
    # plugin older than 0.6.4: field absent -> None, never False
    r = evaluate_handshake("0.1.0", 1, _server(1))
    assert r["editable"] is None
    assert "next_action" not in r

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

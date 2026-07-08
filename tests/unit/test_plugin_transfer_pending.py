from __future__ import annotations

import sys
from pathlib import Path
import json

import pytest

# The klink_server package (imported inside the tests) needs pya, which the
# klayout pip package provides offline; skip cleanly in a bare env.
pytest.importorskip("klayout.db", reason="klayout pip package not installed")



def test_transfer_pending_store_set_status_clear(monkeypatch):
    plugin_python = str(Path(__file__).resolve().parents[2] / "klink_plugin" / "python")
    if plugin_python not in sys.path:
        sys.path.insert(0, plugin_python)

    from klink_server import transfer_pending

    transfer_pending.clear_pending()
    package = {
        "version": 1,
        "package_id": "xfer_test",
        "copy_mode": "flat_selection",
        "source_session": "klayout-8765",
        "target_session": "klayout-8767",
        "target_cell": "TOP",
        "review": {"shape_count": 1, "layers": ["1/0"], "target_layers": ["10/0"]},
        "items": [{"kind": "box", "layer": 10, "datatype": 0, "bbox_um": [0, 0, 1, 1]}],
    }

    status = transfer_pending.set_pending(package)

    assert status["pending"] is True
    assert status["package_id"] == "xfer_test"
    assert status["shape_count"] == 1
    package["items"][0]["bbox_um"][2] = 99
    assert transfer_pending.get_pending()["items"][0]["bbox_um"][2] == 1

    cleared = transfer_pending.clear_pending()

    assert cleared == {"ok": True, "cleared": True}
    assert transfer_pending.status() == {"pending": False}


def test_plugin_session_label_set_writes_shared_registry(monkeypatch, tmp_path):
    plugin_python = str(Path(__file__).resolve().parents[2] / "klink_plugin" / "python")
    if plugin_python not in sys.path:
        sys.path.insert(0, plugin_python)
    monkeypatch.setenv("KLINK_REGISTRY_ROOT", str(tmp_path))

    from klink_server.methods.session_m import session_label_set

    result = session_label_set({
        "session_id": "klayout-8765",
        "label": "source layout",
        "aliases": ["source", "k8765"],
        "description": "test source",
    }, None)

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["session_id"] == "klayout-8765"
    assert state["session_labels"]["klayout-8765"]["label"] == "source layout"
    assert state["session_labels"]["klayout-8765"]["aliases"] == ["source", "k8765"]

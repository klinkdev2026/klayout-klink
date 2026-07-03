"""Unit tests for the klink.guide orientation payload (Principle 7b)."""

import json

from klink.mcp.guide import guide_payload, scan_spec_root, suggest_next


def _write(tmp_path, name, payload):
    (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")


class TestScan:
    def test_empty_root(self, tmp_path):
        assert scan_spec_root(str(tmp_path / "nope")) == []

    def test_inventory_per_cell(self, tmp_path):
        _write(tmp_path, "INV.elec_nets.json",
               {"cell": "INV", "nets": [{"net": "A", "terminals": ["X1.G"]}]})
        _write(tmp_path, "INV.lvs.json", {"ok": True})
        _write(tmp_path, "INV.klink.spec.json", {})
        _write(tmp_path, "NAND.elec_nets.json", {"nets": [{}, {}]})
        state = scan_spec_root(str(tmp_path))
        by = {s["cell"]: s for s in state}
        assert by["INV"]["declared_nets"] == 1
        assert by["INV"]["lvs_ok"] is True
        assert "spec_path" in by["INV"]
        assert by["NAND"]["declared_nets"] == 2
        assert "lvs_ok" not in by["NAND"]


class TestSuggest:
    def test_declared_but_unverified_suggests_connect(self):
        s = suggest_next([{"cell": "NAND", "declared_nets": 2}])
        assert "structdevice.connect_nets {cell: 'NAND'" in s
        assert "route_layer" in s and "pdk.py" in s   # process args are required

    def test_failing_lvs_suggests_reading_the_report(self):
        s = suggest_next([{"cell": "X", "declared_nets": 1,
                           "lvs_ok": False}])
        assert "FAILING" in s

    def test_clean_state_suggests_nothing(self):
        assert suggest_next([{"cell": "X", "declared_nets": 1,
                              "lvs_ok": True, "spec_path": "p"}]) is None


class TestPayload:
    def test_payload_shape(self, tmp_path):
        _write(tmp_path, "C.elec_nets.json", {"nets": [{}]})
        p = guide_payload(str(tmp_path),
                          connection={"connected": True})
        assert p["connection"]["connected"] is True
        assert any("declare_nets" in i["call"] for i in p["intentions"])
        assert any("build_from_netlist" in i["call"]
                   for i in p["intentions"])
        assert p["suggested_next_action"].startswith("cell 'C'")

    def test_disconnected_default_points_to_reconnect(self, tmp_path):
        p = guide_payload(str(tmp_path))
        assert p["connection"]["next_action"] == "klink.reconnect {}"

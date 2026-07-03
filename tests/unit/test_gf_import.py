"""Pure/offline pieces of the gf-script takeover (gf_import).

Needs gdsfactory (skips gracefully without it) but NO live KLayout: builds a
small user-style circuit — two routed connections plus one abutted
`connect()` — and locks:

* route instances are classified out and chains collapse to device-level
  nets (including the direct-snap connection);
* >2-device-port nets come back as instructive problems, not guesses;
* the KLayout cell name is readable and settings-unique;
* template harvesting maps child-local ports through live instance
  transforms with the {tag}{ordinal}_{port} identity rule;
* re-import self-heals a device cell whose live shape count disagrees with
  what the gf cell should produce (empty shell / partial write left behind
  by a past kfactory active-view hijack), and leaves a matching cell alone;
* ``mark_policy="used"`` on the template harvester keeps only in-net ports.
"""
from __future__ import annotations

import pytest

gf = pytest.importorskip("gdsfactory")

from klink.domains.photonics.gf_import import (  # noqa: E402
    _cell_polygons_um,
    harvest_gf_template_ports,
    import_gf_component,
    klayout_cell_name_for,
    split_gf_netlist,
)


@pytest.fixture(scope="module")
def circuit():
    try:
        gf.get_active_pdk()
    except Exception:
        gf.gpdk.PDK.activate()
    c = gf.Component("gfimport_unit_circuit")
    s = c.add_ref(gf.components.mmi1x2(), name="split")
    up = c.add_ref(gf.components.mmi2x2(), name="up")
    down = c.add_ref(gf.components.mmi2x2(), name="down")
    up.move((150, 60))
    down.move((150, -60))
    gf.routing.route_single(c, s.ports["o2"], up.ports["o1"], cross_section="strip")
    gf.routing.route_single(c, s.ports["o3"], down.ports["o1"], cross_section="strip")
    tail = c.add_ref(gf.components.mmi1x2(), name="tail")
    tail.connect("o1", up.ports["o4"])
    return c


def test_collapse_routes_to_device_nets(circuit):
    devices, nets, problems = split_gf_netlist(circuit.get_netlist())
    assert sorted(devices) == ["down", "split", "tail", "up"]
    as_sets = {frozenset(("%s,%s" % a, "%s,%s" % b)) for a, b in nets}
    assert frozenset(("split,o2", "up,o1")) in as_sets
    assert frozenset(("split,o3", "down,o1")) in as_sets
    # the direct connect() snap is a net too
    assert frozenset(("tail,o1", "up,o4")) in as_sets
    assert len(nets) == 3
    assert problems == []


def test_multiway_net_is_an_instructive_problem():
    netlist = {
        "instances": {
            "a": {"component": "mmi1x2"},
            "b": {"component": "mmi1x2"},
            "c": {"component": "mmi1x2"},
        },
        "placements": {},
        "nets": [
            {"p1": "a,o2", "p2": "b,o1"},
            {"p1": "b,o1", "p2": "c,o1"},
        ],
    }
    _, nets, problems = split_gf_netlist(netlist)
    assert nets == []
    assert len(problems) == 1
    assert "point-to-point" in problems[0]


def test_cell_name_is_readable_and_unique(circuit):
    ref = next(r for r in circuit.insts if r.name == "split")
    name = klayout_cell_name_for(ref.cell)
    assert name.startswith("GFDEV_mmi1x2")
    assert name != "GFDEV_mmi1x2"  # carries the settings hash


class FakeClient:
    """instance.query returns two placements of one imported device cell."""

    def layout_info(self):
        return {"dbu": 0.001}

    def call(self, method, params):
        assert method == "instance.query"
        return {"instances": [
            {"child": "GFDEV_X", "trans": {"dx_dbu": 10000, "dy_dbu": 0,
                                           "rotation_deg": 0, "mirror": False}},
            {"child": "GFDEV_X", "trans": {"dx_dbu": 50000, "dy_dbu": 20000,
                                           "rotation_deg": 90, "mirror": False}},
        ]}


def test_template_harvest_applies_live_transforms():
    templates = {"GFDEV_X": {"ports": [
        {"name": "o1", "center_um": [0.0, 0.0], "orientation": 180.0,
         "width_um": 0.5, "target_layer": "1/0"},
        {"name": "o2", "center_um": [20.0, 0.0], "orientation": 0.0,
         "width_um": 0.5, "target_layer": "1/0"},
    ]}}
    marks = harvest_gf_template_ports(
        FakeClient(), "TOP", tags={"GFDEV_X": "x"}, templates=templates,
        nets={"x0_o1": "n_in"})
    by_name = {m["name"]: m for m in marks}
    assert sorted(by_name) == ["x0_o1", "x0_o2", "x1_o1", "x1_o2"]
    assert by_name["x0_o1"]["center_um"] == [10.0, 0.0]
    assert by_name["x0_o1"]["net"] == "n_in"
    assert by_name["x0_o2"]["center_um"] == [30.0, 0.0]
    # rotated instance: local (20, 0) -> parent (50, 20) + (0, 20)
    assert by_name["x1_o2"]["center_um"] == [50.0, 40.0]
    assert by_name["x1_o2"]["orientation"] == 90.0
    assert by_name["x1_o1"]["orientation"] == 270.0


def test_mark_policy_used_keeps_only_in_net_ports():
    """A pad-style device with a used edge port + unused duplicate access
    points (e.g. e1/e3 unused edges, plus the redundant center 'pad' port)
    should only surface the in-net one when mark_policy='used'; 'all'
    (default) is unchanged."""
    templates = {"GFDEV_PAD": {"ports": [
        {"name": "e1", "center_um": [-30.0, 0.0], "orientation": 180.0,
         "width_um": 10.0, "port_type": "electrical", "target_layer": "49/0"},
        {"name": "e2", "center_um": [0.0, 30.0], "orientation": 90.0,
         "width_um": 10.0, "port_type": "electrical", "target_layer": "49/0"},
        {"name": "e3", "center_um": [30.0, 0.0], "orientation": 0.0,
         "width_um": 10.0, "port_type": "electrical", "target_layer": "49/0"},
        {"name": "e4", "center_um": [0.0, -30.0], "orientation": 270.0,
         "width_um": 10.0, "port_type": "electrical", "target_layer": "49/0"},
        {"name": "pad", "center_um": [0.0, 0.0], "orientation": 0.0,
         "width_um": 60.0, "port_type": "pad", "target_layer": "49/0"},
    ]}}
    class OneInstanceClient(FakeClient):
        def call(self, method, params):
            assert method == "instance.query"
            return {"instances": [
                {"child": "GFDEV_PAD", "trans": {"dx_dbu": 0, "dy_dbu": 0,
                                                 "rotation_deg": 0, "mirror": False}},
            ]}

    all_marks = harvest_gf_template_ports(
        OneInstanceClient(), "TOP", tags={"GFDEV_PAD": "p"}, templates=templates,
        nets={"p0_e4": "n_heater_pad"})
    assert sorted(m["name"] for m in all_marks) == [
        "p0_e1", "p0_e2", "p0_e3", "p0_e4", "p0_pad"]

    used_marks = harvest_gf_template_ports(
        OneInstanceClient(), "TOP", tags={"GFDEV_PAD": "p"}, templates=templates,
        nets={"p0_e4": "n_heater_pad"}, mark_policy="used")
    assert [m["name"] for m in used_marks] == ["p0_e4"]
    assert used_marks[0]["net"] == "n_heater_pad"

    with pytest.raises(ValueError):
        harvest_gf_template_ports(
            OneInstanceClient(), "TOP", tags={"GFDEV_PAD": "p"},
            templates=templates, mark_policy="bogus")


class FakeReimportClient:
    """Enough of the KLinkClient surface for import_gf_component(route=False):
    cell_list/create/delete, layer_ensure/list, shape_query/delete/insert_many,
    instance_insert_many + instance.query, and the port.mark/view.show_cell
    calls the no-route branch makes. Layer indexes are assigned on first use
    (ensure or a pre-seeded existing shape) and stay stable for the run.
    """

    def __init__(self, existing_cells=(), existing_shape_layers=None):
        self.existing_cells = set(existing_cells)
        self.existing_shape_layers = {
            k: list(v) for k, v in (existing_shape_layers or {}).items()}
        self._layer_seq: dict[tuple[int, int], int] = {}
        self.instances: dict[str, list[dict]] = {}
        self.shape_insert_calls: list[tuple[str, int]] = []
        self.shape_delete_calls: list[tuple[str, dict]] = []

    def _layer_index(self, layer: int, datatype: int) -> int:
        key = (int(layer), int(datatype))
        if key not in self._layer_seq:
            self._layer_seq[key] = len(self._layer_seq)
        return self._layer_seq[key]

    def layout_info(self):
        return {"dbu": 0.001}

    def cell_list(self, **kwargs):
        return {"cells": [{"name": n} for n in sorted(self.existing_cells)]}

    def cell_create(self, name=None):
        self.existing_cells.add(name)
        return {"cell": name}

    def cell_delete(self, cell, recursive=False):
        self.existing_cells.discard(cell)
        self.instances.pop(cell, None)
        return {"ok": True}

    def layer_ensure(self, layer, datatype=0, name=None):
        return {"layer_index": self._layer_index(layer, datatype)}

    def layer_list(self):
        return {"layers": [
            {"layer": l, "datatype": d, "layer_index": i}
            for (l, d), i in self._layer_seq.items()
        ]}

    def shape_query(self, cell, **kwargs):
        layers = self.existing_shape_layers.get(cell, [])
        return {"shapes": [
            {"layer_index": self._layer_index(*layer_dt)} for layer_dt in layers
        ]}

    def shape_delete(self, cell, **kwargs):
        self.shape_delete_calls.append((cell, dict(kwargs)))
        return {"deleted": len(self.existing_shape_layers.get(cell, []))}

    def shape_insert_many(self, cell, items, dry_run=False):
        self.shape_insert_calls.append((cell, len(items)))
        return {"inserted": len(items)}

    def instance_insert_many(self, parent, items, dry_run=False):
        self.instances[parent] = [dict(it) for it in items]
        return {"inserted": len(items)}

    def call(self, method, params=None):
        params = params or {}
        if method == "instance.query":
            parent = params.get("parent")
            out = []
            for it in self.instances.get(parent, []):
                out.append({"child": it["child"], "trans": {
                    "dx_dbu": int(round(it["position_um"][0] / 0.001)),
                    "dy_dbu": int(round(it["position_um"][1] / 0.001)),
                    "rotation_deg": it.get("rotation", 0.0),
                    "mirror": it.get("mirror", False),
                }})
            return {"instances": out}
        return {"ok": True}


def _expected_items_for(gf_cell) -> int:
    polygons_by_layer = _cell_polygons_um(gf, gf_cell)
    return sum(len(polys) for polys in polygons_by_layer.values())


def test_reimport_heals_mismatched_cell_and_skips_matching_one(circuit, tmp_path):
    """Bug 1: 'GFDEV_...' cells that already EXIST are only trusted when
    their live shape count matches the gf cell's real geometry. A mismatch
    (the empty-shell / partial-write failure mode from the kfactory
    active-view hijack) must be cleared and refilled; a matching count must
    be left completely untouched (idempotent, no needless churn)."""
    split_ref = next(r for r in circuit.insts if r.name == "split")
    up_ref = next(r for r in circuit.insts if r.name == "up")
    mmi1x2_name = klayout_cell_name_for(split_ref.cell)
    mmi2x2_name = klayout_cell_name_for(up_ref.cell)
    expected_mmi1x2 = _expected_items_for(split_ref.cell)
    expected_mmi2x2 = _expected_items_for(up_ref.cell)
    assert expected_mmi1x2 >= 1 and expected_mmi2x2 >= 1  # real geometry, not empty

    # mmi1x2: 3 stray shapes left over from a broken past write -> mismatch
    # (real-world equivalent: an empty shell, or a partial residual write).
    stray_count = expected_mmi1x2 + 2
    client = FakeReimportClient(
        existing_cells={mmi1x2_name, mmi2x2_name},
        existing_shape_layers={
            mmi1x2_name: [(250, 0)] * stray_count,
            # mmi2x2: shape count already matches -> must be left alone.
            mmi2x2_name: [(250, 0)] * expected_mmi2x2,
        },
    )

    result = import_gf_component(client, circuit, cell="TESTGF_HEAL",
                                 route=False, spec_root=str(tmp_path))

    assert result["created_cells"] == []
    healed_by_cell = {h["cell"]: h for h in result["healed_cells"]}
    assert set(healed_by_cell) == {mmi1x2_name}
    assert healed_by_cell[mmi1x2_name]["shapes_before"] == stray_count
    assert healed_by_cell[mmi1x2_name]["shapes_after"] == expected_mmi1x2

    # The mismatched cell was cleared (its existing junk layer) and refilled.
    healed_deletes = [c for c in client.shape_delete_calls if c[0] == mmi1x2_name]
    assert len(healed_deletes) == 1
    assert "250/0" in healed_deletes[0][1]["layers"]
    healed_inserts = [c for c in client.shape_insert_calls if c[0] == mmi1x2_name]
    assert healed_inserts == [(mmi1x2_name, expected_mmi1x2)]

    # The matching cell was never touched: no delete, no re-insert.
    assert not [c for c in client.shape_delete_calls if c[0] == mmi2x2_name]
    assert not [c for c in client.shape_insert_calls if c[0] == mmi2x2_name]

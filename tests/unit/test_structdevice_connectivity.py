"""Unit tests for derived connectivity (M3 LayoutToNetlist wrapper).

Synthetic in-memory layouts only.  Requires the 'klayout' package
(present in the project venv); skipped where it is unavailable.
"""

import pytest

kdb = pytest.importorskip("klayout.db")

from klink.domains.structdevice.connectivity import (
    ConnectivityError,
    ConnectivityExtractor,
    ConnectivitySpec,
    PlacedTerminal,
)

SPEC = ConnectivitySpec(
    conductors=("101/0", "104/0"),
    vias=(("101/0", "102/0", "104/0"),),
)


def _flat_three_net_layout():
    """The live-probe replica: expected electrical truth is 3 nets."""
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("TOP")
    m1 = ly.layer(101, 0)
    via = ly.layer(102, 0)
    m2 = ly.layer(104, 0)
    top.shapes(m1).insert(kdb.Box(0, 0, 10000, 2000))        # m1 A
    top.shapes(m1).insert(kdb.Box(0, 10000, 10000, 12000))   # m1 B
    top.shapes(via).insert(kdb.Box(4000, 500, 6000, 1500))   # on m1 A
    top.shapes(m2).insert(kdb.Box(4000, -5000, 6000, 6000))  # tall, via-tied
    top.shapes(m2).insert(kdb.Box(20000, 0, 22000, 2000))    # isolated
    return ly


def _hierarchical_layout():
    """Child-cell metal merged across hierarchy by a top-level strap."""
    ly = kdb.Layout()
    ly.dbu = 0.001
    m1 = ly.layer(101, 0)
    m2 = ly.layer(104, 0)
    ly.layer(102, 0)
    dev = ly.create_cell("DEV")
    dev.shapes(m1).insert(kdb.Box(0, 0, 2000, 2000))
    top = ly.create_cell("TOP")
    top.insert(kdb.CellInstArray(dev.cell_index(), kdb.Trans(kdb.Vector(0, 0))))
    top.insert(kdb.CellInstArray(dev.cell_index(),
                                 kdb.Trans(kdb.Vector(10000, 0))))
    top.shapes(m1).insert(kdb.Box(1000, 500, 11000, 1500))   # strap
    top.shapes(m2).insert(kdb.Box(0, 10000, 2000, 12000))    # isolated
    return ly


class TestSpecValidation:
    def test_empty_conductors(self):
        with pytest.raises(ConnectivityError, match="at least one"):
            ConnectivitySpec(conductors=()).validated()

    def test_duplicate_conductor(self):
        with pytest.raises(ConnectivityError, match="duplicate"):
            ConnectivitySpec(conductors=("101/0", "101/0")).validated()

    def test_via_referencing_undeclared_conductor(self):
        spec = ConnectivitySpec(
            conductors=("101/0",), vias=(("101/0", "102/0", "104/0"),)
        )
        with pytest.raises(ConnectivityError, match="not in conductors"):
            spec.validated()

    def test_bad_layer_string(self):
        with pytest.raises(ConnectivityError, match="layer/datatype"):
            ConnectivitySpec(conductors=("101",)).validated()


class TestExtraction:
    def test_three_net_truth(self):
        ex = ConnectivityExtractor(_flat_three_net_layout(), "TOP", SPEC)
        nets = ex.nets()
        assert len(nets) == 3
        breakdowns = sorted(
            tuple(sorted(n["shapes_by_layer"].items())) for n in nets
        )
        assert breakdowns == [
            (("101/0", 1),),                    # m1 B isolated
            (("101/0", 1), ("104/0", 1)),       # m1 A + via + m2 tall
            (("104/0", 1),),                    # m2 isolated
        ]

    def test_probe_maps_um_points_to_nets(self):
        ex = ConnectivityExtractor(_flat_three_net_layout(), "TOP", SPEC)
        a = ex.probe_um("101/0", 5.0, 1.0)
        b = ex.probe_um("101/0", 5.0, 11.0)
        tall = ex.probe_um("104/0", 5.0, -4.0)
        assert a is not None and b is not None
        assert a != b
        assert tall == a  # via-connected
        assert ex.probe_um("101/0", 99.0, 99.0) is None

    def test_cross_hierarchy_merge(self):
        ex = ConnectivityExtractor(_hierarchical_layout(), "TOP", SPEC)
        nets = ex.nets()
        assert len(nets) == 2
        inst_a = ex.probe_um("101/0", 1.0, 1.0)
        inst_b = ex.probe_um("101/0", 11.0, 1.0)
        assert inst_a == inst_b  # strap merges across hierarchy

    def test_missing_cell_instructive(self):
        with pytest.raises(ConnectivityError, match="not found"):
            ConnectivityExtractor(_flat_three_net_layout(), "NOPE", SPEC)

    def test_undrawn_spec_layers_tolerated_but_recorded(self):
        # GDS write drops empty layers; a declared-but-undrawn layer is
        # legal and must be reported honestly, not raised
        ly = kdb.Layout()
        ly.dbu = 0.001
        ly.create_cell("TOP")
        ex = ConnectivityExtractor(ly, "TOP", SPEC)
        assert ex.missing_layers == ["101/0", "102/0", "104/0"]
        assert ex.nets() == []

    def test_present_layers_not_reported_missing(self):
        ex = ConnectivityExtractor(_flat_three_net_layout(), "TOP", SPEC)
        assert ex.missing_layers == []

    def test_twice_in_one_process_identical(self):
        r1 = ConnectivityExtractor(_flat_three_net_layout(), "TOP", SPEC).nets()
        r2 = ConnectivityExtractor(_flat_three_net_layout(), "TOP", SPEC).nets()
        assert r1 == r2


class TestTerminalNetTable:
    def test_table_and_floating_problem(self):
        ex = ConnectivityExtractor(_flat_three_net_layout(), "TOP", SPEC)
        placed = [
            PlacedTerminal("X1", "G", "101/0", (5.0, 1.0)),
            PlacedTerminal("X1", "S", "104/0", (5.0, -4.0)),
            PlacedTerminal("X2", "G", "101/0", (5.0, 11.0)),
            PlacedTerminal("X9", "D", "104/0", (90.0, 90.0)),  # floating
        ]
        report = ex.terminal_net_table(placed)
        assert len(report["rows"]) == 4
        by_term = {f"{r['instance']}.{r['terminal']}": r["net_id"]
                   for r in report["rows"]}
        assert by_term["X1.G"] == by_term["X1.S"]  # via-connected
        assert by_term["X2.G"] != by_term["X1.G"]
        assert by_term["X9.D"] is None
        assert len(report["problems"]) == 1
        assert "floating" in report["problems"][0]
        assert sorted(report["nets"][by_term["X1.G"]]) == ["X1.G", "X1.S"]

    def test_unknown_layer_rejected(self):
        ex = ConnectivityExtractor(_flat_three_net_layout(), "TOP", SPEC)
        with pytest.raises(ConnectivityError, match="not part of"):
            ex.probe_um("999/0", 0.0, 0.0)

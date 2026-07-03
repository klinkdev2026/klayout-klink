"""Unit tests for LVS-lite declared/derived reconciliation."""

import pytest

from klink.domains.structdevice.lvs_lite import (
    DeclarationError,
    DeclaredNet,
    align_declared_by_position,
    declared_nets_from_dicts,
    reconcile,
)


def test_align_declared_by_position_remaps_permuted_names():
    # declared X1/X2 placed at known positions; the LAYOUT named the same
    # physical devices Y9/Y2 (permuted order). Alignment must rename by POSITION.
    declared = [{"net": "OUT", "terminals": ["X1.D", "X2.G"]}]
    device_terms = {"X1": {"D": [0.0, 6.0], "S": [0.0, -6.0], "G": [-8.0, 0.0]},
                    "X2": {"D": [100.0, 6.0], "S": [100.0, -6.0], "G": [92.0, 0.0]}}
    layout_term_pos = {"Y9": {"D": [0.0, 6.0], "S": [0.0, -6.0], "G": [-8.0, 0.0]},
                       "Y2": {"D": [100.0, 6.0], "S": [100.0, -6.0], "G": [92.0, 0.0]}}
    remapped, problems, n = align_declared_by_position(declared, device_terms, layout_term_pos)
    assert problems == [] and n == 2
    assert remapped == [{"net": "OUT", "terminals": ["Y9.D", "Y2.G"]}]


def test_align_declared_by_position_reports_unmatched_device():
    declared = [{"net": "OUT", "terminals": ["X1.D"]}]
    device_terms = {"X1": {"D": [0.0, 6.0], "S": [0.0, -6.0], "G": [-8.0, 0.0]}}
    layout_term_pos = {"Y1": {"D": [500.0, 6.0], "S": [500.0, -6.0], "G": [492.0, 0.0]}}
    remapped, problems, n = align_declared_by_position(declared, device_terms, layout_term_pos)
    assert n == 0 and len(problems) == 1 and "X1" in problems[0]
    assert "call lvs_check again" in problems[0]            # error is an instruction


def _table(rows):
    return {"rows": [
        {"instance": i, "terminal": t, "layer": "104/0",
         "point_um": [0.0, 0.0], "net_id": n}
        for i, t, n in rows
    ]}


# derived truth used across tests: X1.D + X2.S + X2.G merged (net_0),
# X1.S alone (net_1), X2.D alone (net_2), X1.G alone (net_3)
DERIVED = _table([
    ("X1", "G", "net_3"), ("X1", "S", "net_1"), ("X1", "D", "net_0"),
    ("X2", "G", "net_0"), ("X2", "S", "net_0"), ("X2", "D", "net_2"),
])


def _declare(*nets):
    return declared_nets_from_dicts(
        [{"net": n, "terminals": list(t)} for n, t in nets]
    )


class TestDeclarations:
    def test_valid_roundtrip(self):
        nets = _declare(("OUT", ["X1.D", "X2.S"]), ("IN", ["X1.G"]))
        assert nets[0] == DeclaredNet("OUT", ("X1.D", "X2.S"))

    def test_duplicate_net_id(self):
        with pytest.raises(DeclarationError, match="duplicate"):
            _declare(("A", ["X1.G"]), ("A", ["X1.S"]))

    def test_terminal_in_two_nets(self):
        with pytest.raises(DeclarationError, match="exactly one net"):
            _declare(("A", ["X1.G"]), ("B", ["X1.G"]))

    def test_bad_ref_format(self):
        with pytest.raises(DeclarationError, match="instance.terminal"):
            _declare(("A", ["X1G"]))

    def test_empty_net(self):
        with pytest.raises(DeclarationError, match="no terminals"):
            _declare(("A", []))


class TestReconcile:
    def test_clean_pass(self):
        declared = _declare(
            ("OUT", ["X1.D", "X2.S", "X2.G"]),
            ("GND", ["X1.S"]),
            ("VDD", ["X2.D"]),
            ("IN", ["X1.G"]),
        )
        report = reconcile(declared, DERIVED)
        assert report["ok"] is True
        assert report["problems"] == []
        assert report["matches"] == {
            "OUT": "net_0", "GND": "net_1", "VDD": "net_2", "IN": "net_3",
        }
        assert report["infos"] == []

    def test_open_detected(self):
        # declares the two gates together, but geometry keeps them apart
        declared = _declare(("IN", ["X1.G", "X2.G"]))
        report = reconcile(declared, DERIVED)
        assert report["ok"] is False
        assert any(p.startswith("open:") for p in report["problems"])

    def test_short_detected(self):
        # OUT and SENSE declared separate but land on the same derived net
        declared = _declare(("OUT", ["X1.D"]), ("SENSE", ["X2.S"]))
        report = reconcile(declared, DERIVED)
        assert any(p.startswith("short:") for p in report["problems"])

    def test_floating_detected(self):
        table = _table([("X1", "G", None)])
        declared = _declare(("IN", ["X1.G"]))
        report = reconcile(declared, table)
        assert any(p.startswith("floating:") for p in report["problems"])

    def test_unknown_ref_detected(self):
        declared = _declare(("IN", ["X9.G"]))
        report = reconcile(declared, DERIVED)
        assert any(p.startswith("unknown:") for p in report["problems"])

    def test_undeclared_terminal_is_info_not_problem(self):
        declared = _declare(
            ("OUT", ["X1.D", "X2.S"]),  # X2.G shares net_0 but undeclared
            ("GND", ["X1.S"]),
            ("VDD", ["X2.D"]),
            ("IN", ["X1.G"]),
        )
        report = reconcile(declared, DERIVED)
        assert report["ok"] is True
        assert any("X2.G shares derived net net_0" in i
                   for i in report["infos"])

    def test_twice_in_one_process_identical(self):
        declared = _declare(("OUT", ["X1.D", "X2.S", "X2.G"]))
        assert reconcile(declared, DERIVED) == reconcile(declared, DERIVED)

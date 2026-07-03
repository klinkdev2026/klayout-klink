"""PUBLIC test: the single-parser process stack (F0).

Routing's layer view and LVS's layer view must come from ONE parser and agree
exactly. Offline, no KLayout, no lab data -- the two stack-projection cases that
needed a lab pdk in the dev suite are sourced here from tests/public/synth_pdk.py.
"""
import json

import pytest

from klink.domains.structdevice.connectivity import ConnectivitySpec
from klink.process_stack import StackError, StackSpec

from synth_pdk import SYNTH_STACK


def _stack():
    return StackSpec.from_dict({
        "conductors": [
            {"layer": "101/0", "role": "gate", "prefer": "crossunder"},
            {"layer": "104/0", "role": "sd", "prefer": "signal"},
        ],
        "vias": [{"from": "101/0", "via_layer": "102/0", "to": "104/0",
                  "via_cell": "via12_cell"}],
        "order": ["104/0", "102/0", "101/0"],
    })


class TestParsing:
    def test_canonical_layer_forms(self):
        s = StackSpec.from_dict({
            "conductors": [{"layer": 101, "datatype": 0}, "104/0"],
            "vias": [{"from": [101, 0], "via_layer": "102/0", "to": "104/0",
                      "via_cell": "v"}]})
        assert s.conductor_layers() == ["101/0", "104/0"]
        assert s.via_triples() == [("101/0", "102/0", "104/0")]

    def test_empty_conductors_rejected(self):
        with pytest.raises(StackError, match="conductors is empty"):
            StackSpec.from_dict({"conductors": []})

    def test_duplicate_conductor_rejected(self):
        with pytest.raises(StackError, match="duplicate conductor"):
            StackSpec.from_dict({"conductors": ["101/0", "101/0"]})

    def test_via_to_undeclared_conductor_rejected(self):
        with pytest.raises(StackError, match="not a declared conductor"):
            StackSpec.from_dict({
                "conductors": ["101/0"],
                "vias": [{"from": "101/0", "via_layer": "102/0",
                          "to": "104/0", "via_cell": "v"}]})

    def test_via_without_cell_rejected(self):
        with pytest.raises(StackError, match="via_cell"):
            StackSpec.from_dict({
                "conductors": ["101/0", "104/0"],
                "vias": [{"from": "101/0", "via_layer": "102/0",
                          "to": "104/0"}]})


class TestViews:
    def test_via_cell_lookup_is_order_independent(self):
        s = _stack()
        assert s.via_cell_for("101/0", "104/0") == "via12_cell"
        assert s.via_cell_for("104/0", "101/0") == "via12_cell"
        assert s.via_cell_for("101/0", "106/0") is None

    def test_role_and_prefer(self):
        s = _stack()
        assert s.role_of("101/0") == "gate"
        assert s.prefer_of("104/0") == "signal"

    def test_deterministic_serialization(self):
        s = _stack()
        a = json.dumps(s.to_dict(), sort_keys=True)
        b = json.dumps(StackSpec.from_dict(s.to_dict()).to_dict(),
                       sort_keys=True)
        assert a == b


class TestSingleParserRoundTrip:
    def test_routing_view_and_lvs_view_are_byte_identical(self):
        s = _stack()
        routing_conductors = s.conductor_layers()
        routing_via_triples = s.via_triples()
        lvs = ConnectivitySpec.from_stack(s)
        assert list(lvs.conductors) == routing_conductors
        assert list(lvs.vias) == routing_via_triples

    def test_synth_stack_projects_to_known_connectivity(self):
        lvs = ConnectivitySpec.from_stack(SYNTH_STACK)
        assert lvs.conductors == ("101/0", "104/0", "106/0")
        assert lvs.vias == (("101/0", "102/0", "104/0"),
                            ("104/0", "105/0", "106/0"))

    def test_stack_projection_equals_direct_spec(self):
        # F0 contract: the stack-derived spec equals a directly-built spec.
        lvs = ConnectivitySpec.from_stack(SYNTH_STACK)
        direct = ConnectivitySpec(
            conductors=("101/0", "104/0", "106/0"),
            vias=(("101/0", "102/0", "104/0"), ("104/0", "105/0", "106/0")),
        ).validated()
        assert lvs.conductors == direct.conductors
        assert lvs.vias == direct.vias

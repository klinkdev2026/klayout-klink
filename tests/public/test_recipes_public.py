"""PUBLIC test: the klink recipe TOOLKIT (klink ships the toolkit; device
recipes are example/PDK-owned). Exercises the recipe-free geom_terminal_provider
(the build path's terminal source) and the geometry primitives with synthetic,
IP-free geometry.
"""
import pytest

from klink.domains.structdevice.recipes import (
    DerivedTerminal,
    RecipeError,
    center,
    geom_terminal_provider,
    norm_box,
    overlap_area,
    snap_orientation,
    touches,
)

# Synthetic harvested device-geometry table (the shape of device_geom.json):
# a back-gate-style cell with G/S/D terminals and their pad boxes.
GEOM = {
    "dev_bg": {
        "terms": {
            "G": {"center": [-18.0, 0.0], "orientation": 180.0, "layer": "101/0", "length": 2.0},
            "S": {"center": [0.0, -5.5], "orientation": 270.0, "layer": "104/0", "length": 1.0},
            "D": {"center": [0.0, 5.5], "orientation": 90.0, "layer": "104/0", "length": 1.0},
        },
        "pads": {
            "G": [-19.0, -7.5, -17.0, 7.5],   # ori 180 -> width = pad height = 15
            "S": [-5.0, -6.0, 5.0, -5.0],     # ori 270 -> width = pad width = 10
            "D": [-5.0, 5.0, 5.0, 6.0],       # ori 90  -> width = pad width = 10
        },
    },
}


def test_geom_terminal_provider_builds_terminals_from_data():
    provider = geom_terminal_provider(GEOM)
    terms = provider(None, "dev_bg")           # client is ignored (data already harvested)

    assert set(terms) == {"G", "S", "D"}
    assert all(isinstance(t, DerivedTerminal) for t in terms.values())

    g = terms["G"]
    assert g.center_um == (-18.0, 0.0)
    assert g.orientation_deg == 180.0
    assert g.width_um == 15.0                  # pad height for a horizontal launch
    assert g.layer == "101/0"
    assert g.source == "derived:device_geom"

    assert terms["S"].orientation_deg == 270.0
    assert terms["S"].width_um == 10.0         # pad width for a vertical launch
    assert terms["D"].layer == "104/0"
    # to_port_dict is the Port-IR handoff
    assert terms["G"].to_port_dict()["orientation_deg"] == 180.0


def test_geom_terminal_provider_rejects_unknown_cell():
    provider = geom_terminal_provider(GEOM)
    with pytest.raises(RecipeError, match="no harvested geometry"):
        provider(None, "not_a_cell")


def test_geometry_primitives():
    assert center([-2.0, -1.0, 2.0, 1.0]) == (0.0, 0.0)
    assert norm_box([2, 1, -2, -1], "b") == (-2.0, -1.0, 2.0, 1.0)   # normalizes corners
    # snap to nearest axis
    assert snap_orientation((1.0, 0.1), "v") == 0.0
    assert snap_orientation((-0.1, 1.0), "v") == 90.0
    # touching vs overlapping
    assert touches((0.0, 0.0, 1.0, 1.0), (1.0, 0.0, 2.0, 1.0))
    assert overlap_area((0.0, 0.0, 2.0, 2.0), (1.0, 1.0, 3.0, 3.0)) == 1.0
    assert overlap_area((0.0, 0.0, 1.0, 1.0), (2.0, 2.0, 3.0, 3.0)) == 0.0

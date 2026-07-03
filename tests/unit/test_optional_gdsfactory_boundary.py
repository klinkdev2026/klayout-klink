"""Optional-dependency boundary for the gdsfactory bridge.

The klink core is zero-dependency: every gdsfactory bridge module must be
importable without gdsfactory installed, and calling into the bridge without
it must raise a capability error that names the install command, never a
bare AttributeError/TypeError on a None symbol.
"""

from __future__ import annotations

import importlib.util

import pytest

GDSFACTORY_PRESENT = importlib.util.find_spec("gdsfactory") is not None


def test_routing_package_exports_bridge_symbols_without_gdsfactory():
    import klink.routing as routing

    for name in (
        "place_gdsfactory_components",
        "route_gdsfactory_ports",
        "select_gdsfactory_port_groups",
        "route_bundle_with_gdsfactory",
        "component_polygons_to_shape_items",
        "gdsfactory_component_marker_to_shapes_and_ports",
    ):
        symbol = getattr(routing, name)
        assert callable(symbol), f"{name} must be a callable, not {symbol!r}"


@pytest.mark.skipif(GDSFACTORY_PRESENT, reason="gdsfactory installed; missing-dep path not testable")
def test_load_gdsfactory_raises_capability_error_with_install_hint():
    from klink.routing.backends.gdsfactory.gdsfactory_backend import _load_gdsfactory

    with pytest.raises(RuntimeError) as excinfo:
        _load_gdsfactory()
    message = str(excinfo.value)
    assert "gdsfactory" in message
    assert "pip install gdsfactory" in message


@pytest.mark.skipif(GDSFACTORY_PRESENT, reason="gdsfactory installed; missing-dep path not testable")
def test_port_to_gf_returns_none_without_gdsfactory():
    # Documented soft contract: port_to_gf returns None when gdsfactory is
    # missing (callers like _gf_port turn that into their own errors).
    from klink.routing.geom.port import port_to_gf

    assert port_to_gf({"name": "P1", "center_um": [0, 0]}) is None

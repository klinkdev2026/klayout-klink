"""Draw a gdsfactory route_bundle result into KLayout through klink.

Run with the project gdsfactory venv while KLayout/klink is running:

    .\\venv\\Scripts\\python.exe examples_klink\\23_gdsfactory_route_to_klayout.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from pprint import pprint

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from klink import KLinkClient
from klink.routing.backends.gdsfactory.gdsfactory_backend import route_bundle_with_gdsfactory
from klink.routing.geom.writeback import commit_routes


CELL = "GF_ROUTE_BUNDLE_SMOKE"


def _box(client: KLinkClient, bbox_um, *, layer=20, datatype=0) -> None:
    client.shape_insert_box(CELL, layer=layer, datatype=datatype, bbox_um=bbox_um)


def _port_marker(client: KLinkClient, center, orientation, *, name: str) -> dict:
    x, y = center
    size = 3.0
    _box(client, [x - size / 2, y - size / 2, x + size / 2, y + size / 2], layer=21)
    client.shape_insert_text(CELL, name, layer=21, datatype=0, position_um=[x - 2, y + 4], size_um=2.5)
    return {
        "name": name,
        "center_um": [float(x), float(y)],
        "orientation": float(orientation),
        "width_um": 0.5,
        "target_layer": "10/0",
        "port_type": "electrical",
    }


def main() -> int:
    ports_left = [
        _port_marker_dict("L0", [0.0, 0.0], 0),
        _port_marker_dict("L1", [0.0, 10.0], 0),
    ]
    ports_right = [
        _port_marker_dict("R0", [80.0, 20.0], 180),
        _port_marker_dict("R1", [80.0, 30.0], 180),
    ]
    report = route_bundle_with_gdsfactory(
        ports_left,
        ports_right,
        layer="10/0",
        route_width_um=0.5,
        separation_um=5.0,
        sort_ports=True,
    )

    with KLinkClient().connect() as client:
        try:
            client.cell_delete(CELL, recursive=True)
        except Exception:
            pass
        client.cell_create(CELL)
        client.layer_ensure(10, 0, name="GF_ROUTE")
        client.layer_ensure(20, 0, name="GF_PORT_BOX")
        client.layer_ensure(21, 0, name="GF_PORT_MARKER")

        for port in ports_left + ports_right:
            _port_marker(client, port["center_um"], port["orientation"], name=port["name"])

        writeback = commit_routes(client, CELL, report["routes"], route_layer="10/0", clear=True)
        client.show_cell(CELL, zoom_fit=True)

    pprint({key: value for key, value in report.items() if key != "gf_component"})
    pprint({"writeback": writeback, "cell": CELL})
    return 0


def _port_marker_dict(name: str, center, orientation: float) -> dict:
    return {
        "name": name,
        "center_um": [float(center[0]), float(center[1])],
        "orientation": float(orientation),
        "width_um": 0.5,
        "target_layer": "10/0",
        "port_type": "electrical",
    }


if __name__ == "__main__":
    raise SystemExit(main())

"""Route width-mismatched KLayout Port markers through gdsfactory auto-taper.

Run while KLayout/klink is running:

    .\\venv\\Scripts\\python.exe examples_klink\\29_gdsfactory_width_mismatch.py

This creates two Port PCells with the same net but different widths, then asks
gdsfactory.route_bundle(auto_taper=True) to insert the taper and route geometry.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from klink import KLinkClient
from klink.errors import KLinkServerError
from klink.routing.backends.gdsfactory.gdsfactory_ports import route_gdsfactory_ports


CELL = "GF_WIDTH_MISMATCH_AUTO_TAPER"
PORT_LAYER = "999/99"
ROUTE_LAYER = "10/0"
GF_ROUTE_LAYER = "1/0"
DEVICE_LAYER = (2, 0)


def reset_cell(client: KLinkClient, cell: str) -> None:
    try:
        client.cell_delete(cell, recursive=True)
    except KLinkServerError:
        pass
    client.cell_create(cell)


def mark_port(client: KLinkClient, name: str, center_um, orientation: float, width_um: float) -> None:
    client.call(
        "port.mark",
        {
            "cell": CELL,
            "layer": PORT_LAYER,
            "name": name,
            "center_um": list(center_um),
            "orientation": orientation,
            "width_um": width_um,
            "port_type": "optical",
            "net": "wide_demo",
            "target_layer": ROUTE_LAYER,
            "show_label": True,
        },
    )


def main() -> int:
    with KLinkClient().connect() as client:
        reset_cell(client, CELL)
        client.layer_ensure(*DEVICE_LAYER, name="GF_DEMO_DEVICE")
        client.layer_ensure(999, 99, name="KLINK_PORTS")
        client.layer_ensure(997, 99, name="KLINK_LABELS")
        client.call("port.set_layer", {"layer": PORT_LAYER})

        client.shape_insert_box(CELL, layer=2, datatype=0, bbox_um=[0, -0.25, 12, 0.25])
        client.shape_insert_box(CELL, layer=2, datatype=0, bbox_um=[80, 39, 94, 41])
        client.shape_insert_text(
            CELL,
            "0.5um port -> 2.0um port via gdsfactory auto_taper",
            layer=997,
            datatype=99,
            position_um=[0, 55],
            size_um=3,
        )

        mark_port(client, "NARROW", [12, 0], 0, 0.5)
        mark_port(client, "WIDE", [80, 40], 180, 2.0)

        report = route_gdsfactory_ports(
            client,
            CELL,
            port_layer=PORT_LAYER,
            route_layer=ROUTE_LAYER,
            gf_route_layer=GF_ROUTE_LAYER,
            output_mode="batch_polygons",
            auto_taper=True,
            net="wide_demo",
        )

    route = report["routes"][0]
    print("cell:", CELL)
    print("routes:", len(report["routes"]))
    print("source -> target:", route["source"], "->", route["target"])
    print("width_um:", route["width_um"])
    print("points_um:", route["points_um"])
    print("writeback inserted:", report["writeback"].get("inserted", 0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Place a gdsfactory splitter marker and route its two-port nets."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from klink import KLinkClient
from klink.errors import KLinkServerError
from klink.routing.backends.gdsfactory.gdsfactory_components import place_gdsfactory_components
from klink.routing.backends.gdsfactory.gdsfactory_ports import route_gdsfactory_ports


CELL = "GF_COMPONENT_01_MMI_SPLITTER"
PORT_LAYER = "999/99"
WG_LAYER = "10/0"


def reset_cell(client: KLinkClient) -> None:
    try:
        client.cell_delete(CELL, recursive=True)
    except KLinkServerError:
        pass
    client.cell_create(CELL)


def mark_external_port(client: KLinkClient, name: str, xy, orientation: float, net: str) -> None:
    client.call(
        "port.mark",
        {
            "cell": CELL,
            "layer": PORT_LAYER,
            "name": name,
            "center_um": list(xy),
            "orientation": orientation,
            "width_um": 0.5,
            "port_type": "optical",
            "net": net,
            "target_layer": WG_LAYER,
            "show_label": True,
        },
    )


def main() -> int:
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8765
    with KLinkClient(port=port).connect() as client:
        reset_cell(client)
        client.layer_ensure(997, 99, name="LABEL")
        client.shape_insert_text(
            CELL,
            "gdsfactory mmi1x2 marker: route three point-to-point optical nets",
            layer=997,
            datatype=99,
            position_um=[-25, 50],
            size_um=3,
        )

        mark_external_port(client, "SRC", [0, 0], 0, "net_in")
        mark_external_port(client, "DROP0", [110, 18], 180, "net_out0")
        mark_external_port(client, "DROP1", [110, -18], 180, "net_out1")

        marker = {
            "id": "SPL1",
            "component": "mmi1x2",
            "center_um": [55, 0],
            "rotation": 0,
            "params": {},
            "port_nets": {
                "o1": "net_in",
                "o2": "net_out0",
                "o3": "net_out1",
            },
        }
        placed = place_gdsfactory_components(
            client,
            CELL,
            [marker],
            target_layer=WG_LAYER,
            port_layer=PORT_LAYER,
            clear=True,
        )
        routed = route_gdsfactory_ports(
            client,
            CELL,
            port_layer=PORT_LAYER,
            route_layer=WG_LAYER,
            output_mode="batch_polygons",
            clear=False,
            allow_crossing=False,
            separation_um=3.0,
        )
        client.show_cell(CELL, zoom_fit=True)

    print("placed components=%d ports=%d shapes=%d" % (
        len(placed["components"]),
        placed["port_count"],
        placed["shape_count"],
    ))
    print("routed routes=%d crossings=%d" % (len(routed["routes"]), len(routed["crossings"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

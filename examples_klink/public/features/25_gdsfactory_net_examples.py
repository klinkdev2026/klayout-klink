"""Create net-driven gdsfactory routing test cells."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from klink import KLinkClient
from klink.errors import KLinkServerError


PORT_LAYER = "999/99"
M1 = (1, 0)


def reset_cell(client: KLinkClient, cell: str) -> None:
    try:
        client.cell_delete(cell, recursive=True)
    except KLinkServerError:
        pass
    client.cell_create(cell)


def port(client: KLinkClient, cell: str, name: str, xy, orientation: float, net: str) -> None:
    client.call(
        "port.mark",
        {
            "cell": cell,
            "layer": PORT_LAYER,
            "name": name,
            "center_um": list(xy),
            "orientation": orientation,
            "width_um": 0.5,
            "port_type": "optical",
            "net": net,
            "target_layer": "10/0",
            "show_label": True,
        },
    )


def box(client: KLinkClient, cell: str, bbox) -> None:
    client.shape_insert_box(cell, layer=M1[0], datatype=M1[1], bbox_um=list(bbox))


def label(client: KLinkClient, cell: str, text: str, xy) -> None:
    client.shape_insert_text(cell, text, layer=997, datatype=99, position_um=list(xy), size_um=3)


def build_two_port(client: KLinkClient) -> None:
    cell = "GF_NET_01_TWO_PORT"
    reset_cell(client, cell)
    label(client, cell, "two arbitrary port names, same net", [0, 25])
    box(client, cell, [0, -2, 15, 2])
    box(client, cell, [100, 28, 115, 32])
    port(client, cell, "LEFT_RANDOM", [15, 0], 0, "sig_a")
    port(client, cell, "RIGHT_RANDOM", [100, 30], 180, "sig_a")


def build_bundle(client: KLinkClient) -> None:
    cell = "GF_NET_02_BUNDLE"
    reset_cell(client, cell)
    label(client, cell, "three two-port nets; names intentionally do not match", [0, 48])
    left = [("Lfoo", 0, "sig0"), ("Lbar", 12, "sig1"), ("Lbaz", 24, "sig2")]
    right = [("Rxxx", 24, "sig2"), ("Ryyy", 12, "sig1"), ("Rzzz", 0, "sig0")]
    for name, y, net in left:
        box(client, cell, [0, y - 2, 15, y + 2])
        port(client, cell, name, [15, y], 0, net)
    for name, y, net in right:
        box(client, cell, [100, y - 2, 115, y + 2])
        port(client, cell, name, [100, y], 180, net)


def build_multidrop(client: KLinkClient) -> None:
    cell = "GF_NET_03_MULTIDROP"
    reset_cell(client, cell)
    label(client, cell, "invalid photonic net: one optical net has three ports; insert splitter", [0, 48])
    box(client, cell, [0, 10, 15, 14])
    box(client, cell, [100, -2, 115, 2])
    box(client, cell, [100, 28, 115, 32])
    port(client, cell, "SRC", [15, 12], 0, "bus")
    port(client, cell, "DROP0", [100, 0], 180, "bus")
    port(client, cell, "DROP1", [100, 30], 180, "bus")


def main() -> int:
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8765
    with KLinkClient(port=port).connect() as client:
        client.layer_ensure(*M1, name="M1")
        client.layer_ensure(997, 99, name="LABEL")
        client.layer_ensure(999, 99, name="KLINK_PORTS")
        client.call("port.set_layer", {"layer": PORT_LAYER})
        build_two_port(client)
        build_bundle(client)
        build_multidrop(client)
        client.show_cell("GF_NET_01_TWO_PORT", zoom_fit=True)
    print("created: GF_NET_01_TWO_PORT, GF_NET_02_BUNDLE, GF_NET_03_MULTIDROP")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

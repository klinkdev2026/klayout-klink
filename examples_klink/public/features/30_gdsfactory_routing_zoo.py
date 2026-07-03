"""Run gdsfactory routing styles from KLayout Port markers.

This is intentionally a klink-style adapter demo:

    KLayout cell + Port PCells
        -> route_gdsfactory_ports(...)
        -> gf.Port conversion + selected gdsfactory router
        -> polygons remapped and written back through RPC

Run while KLayout/klink is running:

    .\\venv\\Scripts\\python.exe examples_klink\\30_gdsfactory_routing_zoo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gdsfactory as gf

from klink import KLinkClient
from klink.errors import KLinkServerError
from klink.routing.backends.gdsfactory.gdsfactory_ports import route_gdsfactory_ports


PORT_LAYER = "999/99"
ROUTE_LAYER = "10/0"
GF_STRIP_LAYER = "1/0"
DEVICE_LAYER = (2, 0)
LABEL_LAYER = (997, 99)


def reset_cell(client: KLinkClient, cell: str) -> None:
    try:
        client.cell_delete(cell, recursive=True)
    except KLinkServerError:
        pass
    client.cell_create(cell)


def setup_cell(client: KLinkClient, cell: str, title: str) -> None:
    reset_cell(client, cell)
    client.layer_ensure(*DEVICE_LAYER, name="GF_DEMO_DEVICE")
    client.layer_ensure(*LABEL_LAYER, name="KLINK_LABELS")
    client.layer_ensure(999, 99, name="KLINK_PORTS")
    client.layer_ensure(10, 0, name="GF_ROUTE_REMAPPED")
    client.call("port.set_layer", {"layer": PORT_LAYER})
    client.shape_insert_text(
        cell,
        title,
        layer=LABEL_LAYER[0],
        datatype=LABEL_LAYER[1],
        position_um=[0, 82],
        size_um=4,
    )


def mark_port(
    client: KLinkClient,
    cell: str,
    name: str,
    xy,
    orientation: float,
    *,
    width_um: float = 0.5,
    net: str = "sig",
    port_type: str = "optical",
) -> None:
    client.call(
        "port.mark",
        {
            "cell": cell,
            "layer": PORT_LAYER,
            "name": name,
            "center_um": list(xy),
            "orientation": orientation,
            "width_um": width_um,
            "port_type": port_type,
            "net": net,
            "target_layer": ROUTE_LAYER,
            "show_label": True,
        },
    )


def draw_stub(client: KLinkClient, cell: str, xy, orientation: float, width: float, length: float = 10.0) -> None:
    x, y = float(xy[0]), float(xy[1])
    half = float(width) / 2.0
    if orientation % 360 == 0:
        bbox = [x - length, y - half, x, y + half]
    elif orientation % 360 == 180:
        bbox = [x, y - half, x + length, y + half]
    elif orientation % 360 == 90:
        bbox = [x - half, y - length, x + half, y]
    else:
        bbox = [x - half, y, x + half, y + length]
    client.shape_insert_box(cell, layer=DEVICE_LAYER[0], datatype=DEVICE_LAYER[1], bbox_um=bbox)


def route_case(client: KLinkClient, cell: str, title: str, ports: list[dict], **route_kwargs) -> dict:
    setup_cell(client, cell, title)
    gf_route_layer = route_kwargs.pop("gf_route_layer", GF_STRIP_LAYER)
    for port in ports:
        draw_stub(client, cell, port["xy"], port["orientation"], port.get("width_um", 0.5))
        mark_port(client, cell, **port)
    return route_gdsfactory_ports(
        client,
        cell,
        port_layer=PORT_LAYER,
        route_layer=ROUTE_LAYER,
        gf_route_layer=gf_route_layer,
        output_mode="batch_polygons",
        clear=True,
        **route_kwargs,
    )


def main() -> int:
    gf.gpdk.PDK.activate()
    results = []
    with KLinkClient().connect() as client:
        for stale_cell in ("GF_ZOO_02_SINGLE_WAYPOINTS", "GF_ZOO_02_SINGLE_STEPS"):
            try:
                client.cell_delete(stale_cell, recursive=True)
            except KLinkServerError:
                pass
        results.append(
            (
                "GF_ZOO_01_BUNDLE",
                route_case(
                    client,
                    "GF_ZOO_01_BUNDLE",
                    "Port markers -> gdsfactory route_bundle",
                    [
                        {"name": "L0", "xy": [10, 0], "orientation": 0, "net": "n0"},
                        {"name": "L1", "xy": [10, 12], "orientation": 0, "net": "n1"},
                        {"name": "L2", "xy": [10, 24], "orientation": 0, "net": "n2"},
                        {"name": "R0", "xy": [100, 24], "orientation": 180, "net": "n2"},
                        {"name": "R1", "xy": [100, 12], "orientation": 180, "net": "n1"},
                        {"name": "R2", "xy": [100, 0], "orientation": 180, "net": "n0"},
                    ],
                    router="bundle",
                    cross_section="strip",
                    all_two_port_nets=True,
                    sort_ports=True,
                ),
            )
        )
        results.append(
            (
                "GF_ZOO_02_BUNDLE_STEPS",
                route_case(
                    client,
                    "GF_ZOO_02_BUNDLE_STEPS",
                    "Port markers -> gdsfactory route_bundle with dx step",
                    [
                        {"name": "A", "xy": [10, 0], "orientation": 0, "net": "s"},
                        {"name": "B", "xy": [110, 40], "orientation": 180, "net": "s"},
                    ],
                    router="bundle",
                    cross_section="strip",
                    net="s",
                    steps=[{"dx": 40}],
                ),
            )
        )
        results.append(
            (
                "GF_ZOO_03_AUTO_TAPER",
                route_case(
                    client,
                    "GF_ZOO_03_AUTO_TAPER",
                    "Port markers -> route_bundle auto_taper width mismatch",
                    [
                        {"name": "NARROW", "xy": [12, 0], "orientation": 0, "width_um": 0.5, "net": "w"},
                        {"name": "WIDE", "xy": [80, 40], "orientation": 180, "width_um": 2.0, "net": "w"},
                    ],
                    router="bundle",
                    cross_section="strip",
                    auto_taper=True,
                    net="w",
                ),
            )
        )
        results.append(
            (
                "GF_ZOO_04_CUSTOM_TAPER",
                route_case(
                    client,
                    "GF_ZOO_04_CUSTOM_TAPER",
                    "Port markers -> route_bundle custom auto_taper",
                    [
                        {"name": "W0", "xy": [12, 0], "orientation": 0, "width_um": 4.0, "net": "ct"},
                        {"name": "W1", "xy": [100, 45], "orientation": 180, "width_um": 4.0, "net": "ct"},
                    ],
                    router="bundle",
                    cross_section="strip",
                    auto_taper=True,
                    auto_taper_taper=gf.components.taper(width1=4, width2=0.5, length=30),
                    net="ct",
                ),
            )
        )
        results.append(
            (
                "GF_ZOO_05_LOW_LOSS_TAPER",
                route_case(
                    client,
                    "GF_ZOO_05_LOW_LOSS_TAPER",
                    "Port markers -> route_bundle wider low-loss route taper",
                    [
                        {"name": "A", "xy": [10, 0], "orientation": 0, "net": "ll"},
                        {"name": "B", "xy": [110, 50], "orientation": 180, "net": "ll"},
                    ],
                    router="bundle",
                    cross_section="strip",
                    taper=gf.components.taper(width1=0.5, width2=2),
                    min_straight_taper_um=20,
                    net="ll",
                ),
            )
        )
        results.append(
            (
                "GF_ZOO_06_SBEND",
                route_case(
                    client,
                    "GF_ZOO_06_SBEND",
                    "Port markers -> gdsfactory route_bundle_sbend",
                    [
                        {"name": "L0", "xy": [10, 0], "orientation": 0, "net": "b0"},
                        {"name": "L1", "xy": [10, 8], "orientation": 0, "net": "b1"},
                        {"name": "L2", "xy": [10, 16], "orientation": 0, "net": "b2"},
                        {"name": "R0", "xy": [80, 2], "orientation": 180, "net": "b0"},
                        {"name": "R1", "xy": [80, 10], "orientation": 180, "net": "b1"},
                        {"name": "R2", "xy": [80, 18], "orientation": 180, "net": "b2"},
                    ],
                    router="sbend",
                    cross_section="strip",
                    all_two_port_nets=True,
                ),
            )
        )
        results.append(
            (
                "GF_ZOO_07_ALL_ANGLE",
                route_case(
                    client,
                    "GF_ZOO_07_ALL_ANGLE",
                    "Port markers -> gdsfactory route_bundle_all_angle",
                    [
                        {"name": "A0", "xy": [10, 0], "orientation": 0, "net": "aa0"},
                        {"name": "A1", "xy": [10, 14], "orientation": 0, "net": "aa1"},
                        {"name": "B0", "xy": [140, 22], "orientation": 150, "net": "aa0"},
                        {"name": "B1", "xy": [140, 36], "orientation": 150, "net": "aa1"},
                    ],
                    router="all_angle",
                    cross_section="strip",
                    all_two_port_nets=True,
                ),
            )
        )
        results.append(
            (
                "GF_ZOO_08_ELECTRICAL",
                route_case(
                    client,
                    "GF_ZOO_08_ELECTRICAL",
                    "Port markers -> gdsfactory electrical route_bundle",
                    [
                        {"name": "E0", "xy": [10, 0], "orientation": 0, "width_um": 10, "net": "e", "port_type": "electrical"},
                        {"name": "E1", "xy": [180, 0], "orientation": 180, "width_um": 10, "net": "e", "port_type": "electrical"},
                    ],
                    router="electrical",
                    gf_route_layer="24/0",
                    cross_section="metal_routing",
                    net="e",
                ),
            )
        )
        results.append(
            (
                "GF_ZOO_09_DUBINS",
                route_case(
                    client,
                    "GF_ZOO_09_DUBINS",
                    "Port markers -> gdsfactory route_dubins",
                    [
                        {"name": "D0", "xy": [100, 0], "orientation": 0, "width_um": 3.2, "net": "d"},
                        {"name": "D1", "xy": [300, 50], "orientation": 225, "width_um": 3.2, "net": "d"},
                    ],
                    router="dubins",
                    cross_section=gf.cross_section.strip(width=3.2, radius=100),
                    net="d",
                ),
            )
        )
        client.show_cell("GF_ZOO_01_BUNDLE", zoom_fit=True)

    for cell, report in results:
        writeback = report.get("writeback") or {}
        print(
            "%s: backend=%s routes=%d inserted=%s"
            % (cell, report.get("backend"), len(report.get("routes", [])), writeback.get("inserted"))
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

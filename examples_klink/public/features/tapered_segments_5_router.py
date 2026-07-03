"""Run the generic tapered cell router on fixture cells."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from klink import KLinkClient
from klink.routing.backends.geometric.tapered_segments import route_tapered_hybrid_cell


CELLS = [
    "TAPERED_SEG_FIX_01_STRAIGHT",
    "TAPERED_SEG_FIX_02_WAYPOINT",
    "TAPERED_SEG_FIX_03_EDGE_SLIDE",
    "TAPERED_SEG_FIX_04_OBSTACLE",
    "TAPERED_SEG_FIX_05_FANOUT",
]


def route_cell(client, cell: str) -> dict:
    result = route_tapered_hybrid_cell(client, cell, spacing_um=4.0, clear=True)
    summary = {
        "cell": cell,
        "ok": result["ok"],
        "ports": result["port_count"],
        "anchors": result["anchor_count"],
        "pairs": result["pair_count"],
        "groups": [
            {
                "route_layer": group["route_layer"],
                "ok": group["ok"],
                "routes": group["route_count"],
                "lane_reports": group["lane_reports"],
                "errors": group["errors"],
                "write": None if group["write"] is None else {
                    "paths": group["write"]["paths"],
                    "patches": group["write"]["patches"],
                    "polygons": group["write"]["polygons"],
                },
            }
            for group in result["groups"]
        ],
    }
    return summary


def main() -> None:
    with KLinkClient().connect() as client:
        results = [route_cell(client, cell) for cell in CELLS]
        client.show_cell(CELLS[-1], zoom_fit=True)
        for result in results:
            print(result)
        if any(not result["ok"] for result in results):
            raise SystemExit(1)


if __name__ == "__main__":
    main()

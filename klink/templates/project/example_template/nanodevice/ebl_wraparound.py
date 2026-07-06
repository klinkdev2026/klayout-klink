# klink PUBLIC example — runnable as-is, self-contained: imports only `klink`
# (no PDK, no NDA, no extra GDS). A parametric generator that carries its own
# layers (it does NOT read pdk.py — that pattern is for process-separated flows).
#
#   Run:    python <this file> --port <your-klayout-rpc-port>
#   In a `klink init` project these live in example_template/ — copy one into
#   custom_devices/ and adapt. See recipes/README.md.
#
"""Live wrapper for the reusable nanodevice EBL wraparound demo."""

from __future__ import annotations

import argparse
import json
import os
import sys


from klink import KLinkClient
from klink.domains.nanodevice.devices.wraparound import (
    ANCHOR_LAYER,
    CELL,
    PORT_LAYER,
    build_wraparound_demo,
)

# Process layers for this self-contained EBL demo -- example-owned; klink ships
# none. Edit these for YOUR process. (Port/Anchor stay on klink's 999/* markers.)
WRAP_LAYERS = {
    "flake": (30, 0), "m1": (10, 0), "m2": (11, 0), "pad": (20, 0),
    "via": (40, 0), "label": (6, 0), "patch": "113/0",
}


def _ensure_layers(client: KLinkClient, items: list[dict]) -> None:
    seen = set()
    for item in items:
        key = (int(item["layer"]), int(item.get("datatype", 0)))
        if key not in seen:
            seen.add(key)
            client.layer_ensure(key[0], key[1], name=f"NANODEVICE_{key[0]}_{key[1]}")


def _delete_cell(client: KLinkClient, cell: str) -> None:
    try:
        client.cell_delete(cell, recursive=True)
    except Exception:
        pass


def write_live(bundle: dict, *, keep: bool) -> dict:
    with KLinkClient(port=8765).connect() as client:
        _delete_cell(client, CELL)
        client.cell_create(CELL)
        client.call("port.set_layer", {"layer": PORT_LAYER})
        client.call("anchor.set_layer", {"layer": ANCHOR_LAYER})
        _ensure_layers(client, bundle["shape_items"])
        insert = client.shape_insert_many(CELL, bundle["shape_items"])
        for port in bundle["port_marks"]:
            payload = dict(port)
            payload["cell"] = CELL
            client.call("port.mark", payload)
        for anchor in bundle["anchor_marks"]:
            payload = dict(anchor)
            payload["cell"] = CELL
            client.call("anchor.mark", payload)
        ports = client.call("port.list", {"cell": CELL, "layer": PORT_LAYER}).get("count")
        anchors = client.call("anchor.list", {"cell": CELL, "layer": ANCHOR_LAYER}).get("count")
        client.show_cell(CELL, zoom_fit=True)
        if not keep:
            _delete_cell(client, CELL)
        return {
            "cell": CELL,
            "inserted": int(insert.get("inserted", 0)),
            "ports": ports,
            "anchors": anchors,
            "kept": keep,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="write to KLayout port 8765")
    parser.add_argument("--keep", action="store_true", help="keep the generated live cell")
    args = parser.parse_args(argv)

    bundle = build_wraparound_demo(WRAP_LAYERS)
    report = {
        "ok": True,
        "cell": CELL,
        "mode": "live" if args.live else "offline",
        "demo": bundle["report"],
        "writefield": bundle["writefield"]["report"],
        "patch": bundle["patch_report"],
        "wf_validation": {
            "crossing_count": bundle["wf_validation"]["crossing_count"],
            "violations": len(bundle["wf_validation"]["violations"]),
        },
        "overlap_validation": {
            "overlaps": len(bundle["overlap_validation"]["overlaps"]),
        },
    }
    if args.live:
        report["live"] = write_live(bundle, keep=args.keep)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

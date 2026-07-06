# klink PUBLIC example — runnable as-is, self-contained: imports only `klink`
# (no PDK, no NDA, no extra GDS). A parametric generator that carries its own
# layers (it does NOT read pdk.py — that pattern is for process-separated flows).
#
#   Run:    python <this file> --port <your-klayout-rpc-port>
#   In a `klink init` project these live in example_template/ — copy one into
#   custom_devices/ and adapt. See recipes/README.md.
#
"""Nanodevice Hall bar example.

Default mode is offline and prints the generated semantic bundle plus routed
result.  Use ``--live`` to write a disposable KLayout cell; live mode deletes
the test cell by default unless ``--keep`` is passed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


from klink.domains.nanodevice.devices.hallbar import HallBarSpec
from klink.domains.nanodevice.pipeline import build_hallbar_bundle, route_hallbar_offline


CELL = "NANODEVICE_HALLBAR_TEST"


def _mark_ports(client, cell: str, ports: list[dict]) -> None:
    for port in ports:
        payload = dict(port)
        payload["cell"] = cell
        client.call("port.mark", payload)


def _mark_anchors(client, cell: str, anchors: list[dict]) -> None:
    for anchor in anchors:
        payload = dict(anchor)
        payload["cell"] = cell
        client.call("anchor.mark", payload)


def _insert_obstacles(client, cell: str, obstacles: list[list[float]]) -> None:
    if not obstacles:
        return
    client.layer_ensure(900, 0, name="KLINK_WF_KEEPOUT")
    client.shape_insert_boxes(cell, layer=900, datatype=0, boxes_um=obstacles)


def _ensure_item_layers(client, items: list[dict]) -> None:
    seen: set[tuple[int, int]] = set()
    for item in items:
        key = (int(item["layer"]), int(item.get("datatype", 0)))
        if key in seen:
            continue
        seen.add(key)
        client.layer_ensure(key[0], key[1], name=f"NANODEVICE_{key[0]}_{key[1]}")


def _delete_cell(client, cell: str) -> None:
    try:
        client.cell_delete(cell, recursive=True)
    except Exception:
        pass


def _phase(timings: dict, name: str, fn):
    start = time.perf_counter()
    result = fn()
    timings[name] = round(time.perf_counter() - start, 4)
    return result


def _default_writefield() -> dict:
    return {
        "chip_bbox_um": [-95.0, -45.0, 95.0, 45.0],
        "writefield_size_um": [70.0, 120.0],
        "origin_um": [10.0, 0.0],
        "stitch_margin_um": 1.2,
    }


def run_live(bundle: dict, route_result: dict, *, port: int, keep: bool) -> dict:
    from klink import KLinkClient
    from klink.routing.backends.geometric.tapered_segments import commit_tapered_hybrid_many

    timings: dict[str, float] = {}
    with KLinkClient(port=port).connect() as client:
        _delete_cell(client, CELL)
        client.cell_create(CELL)
        _phase(timings, "ensure_layers", lambda: _ensure_item_layers(client, bundle["shape_items"]))
        inserted = _phase(timings, "shape_insert_many", lambda: client.shape_insert_many(CELL, bundle["shape_items"]))
        obstacles = bundle.get("obstacle_boxes_um") or []
        _phase(timings, "obstacle_insert", lambda: _insert_obstacles(client, CELL, obstacles))
        _phase(timings, "port_mark", lambda: _mark_ports(client, CELL, bundle["port_marks"]))
        _phase(timings, "anchor_mark", lambda: _mark_anchors(client, CELL, bundle["anchor_marks"]))
        write = None
        if route_result["ok"]:
            write = _phase(
                timings,
                "route_commit",
                lambda: commit_tapered_hybrid_many(client, CELL, route_result, route_layer="12/0", clear=True),
            )
        _phase(timings, "show_cell", lambda: client.show_cell(CELL, zoom_fit=True))
        wall_crossings = len(route_result.get("obstacle_hits") or [])
        report = {
            "ok": bool(route_result["ok"]) and wall_crossings == 0 and len(route_result.get("sibling_overlaps") or []) == 0,
            "cell": CELL,
            "port": port,
            "inserted": inserted,
            "ports": len(bundle["port_marks"]),
            "anchors": len(bundle["anchor_marks"]),
            "writefield_obstacles": len(obstacles),
            "writefield_wall_crossings": wall_crossings,
            "sibling_overlaps": len(route_result.get("sibling_overlaps") or []),
            "routes": route_result["route_count"],
            "write": write,
            "timings_s": timings,
            "kept": bool(keep),
        }
        if not keep:
            _delete_cell(client, CELL)
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="write a disposable KLayout cell")
    parser.add_argument("--port", type=int, default=8765, help="KLayout klink JSON-RPC port")
    parser.add_argument("--keep", action="store_true", help="keep the live test cell for inspection")
    args = parser.parse_args(argv)

    timings: dict[str, float] = {}
    bundle = _phase(
        timings,
        "build_hallbar_bundle",
        lambda: build_hallbar_bundle(
            HallBarSpec(name="NDHB",
                        device_layer="1/0", metal_layer="10/0",
                        label_layer="6/0", route_layer="12/0"),
            writefield=_default_writefield()),
    )
    route_result = _phase(timings, "route_plan", lambda: route_hallbar_offline(bundle))
    report = {
        "ok": bool(route_result["ok"]),
        "mode": "live" if args.live else "offline",
        "timings_s": timings,
        "layout": bundle["report"],
        "writefield": bundle.get("writefield", {}).get("report", {}),
        "routing": {
            "backend": route_result["backend"],
            "route_count": route_result["route_count"],
            "errors": route_result["errors"],
            "sibling_overlaps": len(route_result["sibling_overlaps"]),
            "obstacle_hits": len(route_result["obstacle_hits"]),
            "writefield_wall_crossings": len(route_result["obstacle_hits"]),
        },
    }
    if args.live:
        report["live"] = run_live(bundle, route_result, port=args.port, keep=args.keep)
        report["ok"] = bool(report["live"]["ok"])
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

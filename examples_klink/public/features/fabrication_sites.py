#!/usr/bin/env python
"""Scripted live gate for fabrication site arrays."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from klink import KLinkClient
from klink.domains.fabrication import place_site_array


CELL = "FABRICATION_SITES_TEST"
DEVICE_CELL = "FABRICATION_DUMMY_DEVICE"
MARK_CELLS = {
    "leftdown": "FAB_MARK_LEFTDOWN",
    "leftup": "FAB_MARK_LEFTUP",
    "rightdown": "FAB_MARK_RIGHTDOWN",
    "rightup": "FAB_MARK_RIGHTUP",
}


def _delete_cell(client, cell: str) -> None:
    try:
        client.cell_delete(cell, recursive=True)
    except Exception:
        pass


def _prepare_dummy_device(client) -> None:
    _delete_cell(client, DEVICE_CELL)
    client.cell_create(DEVICE_CELL)
    client.layer_ensure(1, 0, name="FABRICATION_DUMMY_DEVICE")
    client.layer_ensure(6, 0, name="FABRICATION_LABELS")
    client.shape_insert_many(
        DEVICE_CELL,
        [
            {"kind": "box", "layer": 1, "datatype": 0, "bbox_um": [-50, -25, 50, 25]},
            {"kind": "text", "layer": 6, "datatype": 0, "text": "DUT", "position_um": [0, 0], "size_um": 20},
        ],
    )


def _prepare_dummy_mark_cells(client) -> None:
    for idx, (corner, cell) in enumerate(MARK_CELLS.items()):
        _delete_cell(client, cell)
        client.cell_create(cell)
        layer = 6 + idx
        client.layer_ensure(layer, 0, name=f"FABRICATION_MARK_{corner.upper()}")
        client.shape_insert_many(
            cell,
            [
                {"kind": "box", "layer": layer, "datatype": 0, "bbox_um": [-80, -8, 80, 8]},
                {"kind": "box", "layer": layer, "datatype": 0, "bbox_um": [-8, -40 - 10 * idx, 8, 40 + 10 * idx]},
                {"kind": "text", "layer": layer, "datatype": 0, "text": corner, "position_um": [-60, 30], "size_um": 12},
            ],
        )


def _cleanup(client) -> None:
    _delete_cell(client, CELL)
    _delete_cell(client, DEVICE_CELL)
    for cell in MARK_CELLS.values():
        _delete_cell(client, cell)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765, help="KLayout klink JSON-RPC port")
    parser.add_argument("--keep", action="store_true", help="leave the disposable cells for inspection")
    parser.add_argument("--state-dir", default="test_outputs/fabrication_sites", help="facts output directory")
    args = parser.parse_args(argv)

    site_layout = {
        "kind": "circular",
        "center_um": [0.0, 0.0],
        "diameter_um": 10000.0,
        "pitch_x_um": 1000.0,
        "pitch_y_um": 1000.0,
        "edge_exclusion_um": 500.0,
        "footprint_um": [100.0, 100.0],
    }
    kwargs = {
        "cell": CELL,
        "site_layout": site_layout,
        "marks": {"mode": "cells", "corners": dict(MARK_CELLS)},
        "instance": {"child": DEVICE_CELL},
        "numbering": {"scheme": "prefix", "prefix": "D", "width": 3},
        "state_dir": args.state_dir,
    }

    with KLinkClient(port=args.port).connect() as client:
        _cleanup(client)
        _prepare_dummy_device(client)
        _prepare_dummy_mark_cells(client)
        dry = place_site_array(client, dry_run=True, **kwargs)
        live = place_site_array(client, dry_run=False, **kwargs)
        ok = bool(dry.get("ok")) and bool(live.get("ok")) and dry.get("site_count") == live.get("site_count")
        report = {
            "ok": ok,
            "port": args.port,
            "cell": CELL,
            "device_cell": DEVICE_CELL,
            "mark_cells": MARK_CELLS,
            "dry_site_count": dry.get("site_count"),
            "live_site_count": live.get("site_count"),
            "state_paths": live.get("state_paths"),
            "writeback": live.get("writeback"),
            "problems": [] if ok else [dry.get("problems"), live.get("problems")],
            "kept": bool(args.keep),
        }
        if not args.keep:
            _cleanup(client)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

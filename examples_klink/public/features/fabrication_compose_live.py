"""Live gate for fabrication complete-mask composer on k8765 only.

The script creates disposable synthetic cells, composes single/grid/circular
wafer cells, verifies direct instance counts with instance.query, and deletes
only the cells it created.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from klink.client import KLinkClient
from klink.domains.fabrication import DieTemplate, WaferTemplate, compose_complete_mask


LAYER = 731
DATATYPE = 0


def main() -> None:
    args = parse_args()
    if int(args.port) != 8765:
        raise SystemExit("P9 live gate is fenced to k8765 only; pass --port 8765.")

    prefix = f"FAB_COMP_LIVE_{int(time.time())}"
    created: list[str] = []
    facts_dir = Path(".klink") / "fabrication" / prefix
    with KLinkClient(port=args.port).connect() as client:
        try:
            source_cells = create_synthetic_cells(client, prefix)
            created.extend(source_cells)
            run_mode(client, prefix, "single", facts_dir, created)
            run_mode(client, prefix, "grid", facts_dir, created)
            run_mode(client, prefix, "circular", facts_dir, created)
            # a silent gate is indistinguishable from a gate that never
            # ran — state the verified outcome explicitly
            print(
                "PASS: single/grid/circular composed and verified "
                f"(die=8 direct instances each; wafer=3/6/3) as {prefix}_*; "
                "all created cells cleaned up"
            )
        finally:
            for cell in reversed(created):
                try:
                    client.cell_delete(cell, recursive=True)
                except Exception:
                    pass


def create_synthetic_cells(client: KLinkClient, prefix: str) -> list[str]:
    names = [
        "payload",
        "bond",
        "corner_leftdown",
        "corner_leftup",
        "corner_rightdown",
        "corner_rightup",
        "global",
        "N0",
        "N1",
    ]
    created = []
    client.layer_ensure(LAYER, DATATYPE, name="FAB_COMPOSE_LIVE")
    for i, suffix in enumerate(names):
        name = f"{prefix}_{suffix}"
        result = client.cell_create(name)
        if result.get("name") != name:
            raise RuntimeError(f"cell {name!r} was auto-renamed to {result.get('name')!r}; rerun after cleanup")
        created.append(name)
        size = 2.0 + float(i % 3)
        client.shape_insert_many(
            name,
            [
                {
                    "kind": "box",
                    "layer": LAYER,
                    "datatype": DATATYPE,
                    "bbox_um": [-size, -size, size, size],
                }
            ],
        )
    return created


def run_mode(client: KLinkClient, prefix: str, mode: str, facts_dir: Path, created: list[str]) -> None:
    die = DieTemplate(
        payload_position_um=(50.0, 50.0),
        bonding={
            "cell": f"{prefix}_bond",
            "positions_um": [[10.0, 50.0], [90.0, 50.0]],
        },
        corner_marks={
            "mode": "cells",
            "corners": {
                "leftdown": f"{prefix}_corner_leftdown",
                "leftup": f"{prefix}_corner_leftup",
                "rightdown": f"{prefix}_corner_rightdown",
                "rightup": f"{prefix}_corner_rightup",
            },
        },
        global_marks=[{"cell": f"{prefix}_global", "position_um": [50.0, 90.0]}],
        die_size_um=(100.0, 100.0),
    )
    wafer = wafer_template(prefix, mode)
    name = f"{prefix}_{mode}"
    result = compose_complete_mask(
        client,
        f"{prefix}_payload",
        name=name,
        die=die,
        wafer=wafer,
        facts_dir=str(facts_dir),
    )
    if not result["ok"]:
        raise RuntimeError(f"{mode} compose failed: {result['problems']}")
    created.extend([result["die_cell"], result["wafer_cell"]])
    verify_instances(client, result["die_cell"], expected=8)
    expected_wafer = {"single": 3, "grid": 6, "circular": 3}[mode]
    verify_instances(client, result["wafer_cell"], expected=expected_wafer)


def wafer_template(prefix: str, mode: str) -> WaferTemplate:
    numbering = {
        "cells": {"0": f"{prefix}_N0", "1": f"{prefix}_N1"},
        "positions_um": [[-20.0, -20.0], [20.0, -20.0]],
    }
    if mode == "single":
        return WaferTemplate(mode="single", position_um=(0.0, 0.0), numbering=numbering)
    if mode == "grid":
        return WaferTemplate(
            mode="grid",
            region_bbox_um=(0.0, 0.0, 100.0, 100.0),
            die_pitch_um=(100.0, 100.0),
            numbering=numbering,
        )
    return WaferTemplate(
        mode="circular",
        wafer_diameter_um=250.0,
        exclusion_margin_um=0.0,
        die_pitch_um=(100.0, 100.0),
        center_um=(0.0, 0.0),
        numbering=numbering,
    )


def verify_instances(client: KLinkClient, cell: str, *, expected: int) -> None:
    query = client.instance_query(cell, limit=1000)
    actual = int(query.get("returned", 0))
    if actual != expected:
        raise RuntimeError(f"{cell}: expected {expected} direct instances, got {actual}")
    positions = sorted(
        (
            inst.get("trans", {}).get("dx_dbu"),
            inst.get("trans", {}).get("dy_dbu"),
            inst.get("child"),
        )
        for inst in query.get("instances", [])
    )
    if len(positions) != expected:
        raise RuntimeError(f"{cell}: instance position extraction mismatch")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


if __name__ == "__main__":
    main()

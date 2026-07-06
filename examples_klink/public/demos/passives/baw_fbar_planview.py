# klink PUBLIC example — runnable as-is, self-contained: imports only `klink`
# (no PDK, no NDA, no extra GDS). A parametric generator that carries its own
# layers (it does NOT read pdk.py — that pattern is for process-separated flows).
#
#   Run:    python <this file>                       # offline, writes a GDS
#           python <this file> --live --port <port>  # push to a KLayout session
#   In a `klink init` project these live in example_template/ — copy one into
#   custom_devices/ and adapt. See recipes/README.md.
#
"""BAW / FBAR plan-view resonator geometry template.

GEOMETRY TEMPLATE, NOT a validated acoustic design: we make NO
frequency/material claims. This is a PLAN VIEW ONLY (no cross-section, no
membrane/piezo film stack geometry) -- tune the numbers for your process and
verify resonance with your own acoustic simulation. klink ships zero process
data; every number here is example-owned, copy this file and edit it.

Structure: `top_electrode` is an IRREGULAR pentagon with NO two edges
parallel (the spurious-mode apodization convention some FBAR designs use),
generated from a fixed, deterministic, seedless irregular vertex recipe and
scaled to hit `active_area_um2` within 1%. `bottom_electrode` is a rectangle
that fully covers the pentagon (its bounding box plus a small margin) and
extends further out on the side OPPOSITE the top connection to reach its own
contact pad -- so bottom/top overlap is effectively the whole pentagon.
`top_connect` is a strip from the pentagon's most eastward edge to a probe
pad; `bottom_connect` is simply the bottom electrode's own westward
extension, ending in its own pad. An optional `membrane_release` box
(documentation layer only) marks the released-membrane area around the
active region. A `StackSpec` instance documents the intended vertical stack
(top electrode / piezo / bottom electrode) as DATA -- descriptive only, not
drawn geometry (StackSpec has no notion of a non-conductor interlayer; the
piezo sits between the two declared conductors in the real stack).

Params (all microns/um^2 unless noted):
  active_area_um2       target pentagon (active membrane) area
  connect_width_um      width of the top/bottom connect strips
  pad_size_um           side of the square probe pads
  bottom_extension_um   how far the bottom electrode reaches past the
                         pentagon on the side opposite the top connection
  overlap_margin_um     margin the bottom electrode adds around the
                         pentagon's bbox on the other three sides
  membrane_margin_um    margin of the optional membrane_release box around
                         the pentagon's bbox

Ports: TOP (top pad, outward = +x / 0 deg), BOT (bottom pad, outward = -x /
180 deg).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

from klink.process_stack import StackSpec

# Example-owned layers -- edit these for your process. The port marker layer
# (999/99) is klink's reserved convention; leave it alone.
LAYER_METAL_TOP = (15, 0)
LAYER_METAL_BOT = (16, 0)
LAYER_MEMBRANE = (17, 0)
LAYER_PORT = (999, 99)

CELL_NAME = "PUB_BAW_FBAR_PLANVIEW"

DEFAULT_PARAMS: dict = dict(
    active_area_um2=2000.0,
    connect_width_um=8.0,
    pad_size_um=30.0,
    bottom_extension_um=60.0,
    overlap_margin_um=6.0,
    membrane_margin_um=15.0,
)

# Fixed, deterministic, seedless irregular pentagon recipe (no RNG): five
# angles (irregular gaps, not a regular 72 deg pentagon) and five radii
# (varying), chosen so no two of the resulting five edges are parallel. Any
# UNIFORM scale of this shape preserves every edge's direction, so scaling
# to hit active_area_um2 never introduces a parallel pair.
_BASE_ANGLES_DEG = (90.0, 160.0, 205.0, 255.0, 320.0)
_BASE_RADII = (1.0, 0.85, 1.15, 0.7, 1.05)


def _base_pentagon_vertices() -> list[tuple[float, float]]:
    return [
        (r * math.cos(math.radians(a)), r * math.sin(math.radians(a)))
        for a, r in zip(_BASE_ANGLES_DEG, _BASE_RADII)
    ]


def _polygon_area(vertices: list[tuple[float, float]]) -> float:
    """Shoelace formula, absolute value (vertex winding is not guaranteed)."""
    n = len(vertices)
    s = 0.0
    for i in range(n):
        x0, y0 = vertices[i]
        x1, y1 = vertices[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return abs(s) / 2.0


def _edge_direction_classes_deg(vertices: list[tuple[float, float]]) -> list[float]:
    """Each edge's direction angle mod 180 deg (a line and its reverse are
    the same direction class) -- used to check "no two edges parallel"."""
    n = len(vertices)
    classes = []
    for i in range(n):
        x0, y0 = vertices[i]
        x1, y1 = vertices[(i + 1) % n]
        angle = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0
        classes.append(angle)
    return classes


def build_baw_fbar(params: dict) -> dict:
    """Pure geometry builder (no klayout/klink import): returns the shape
    item list, the port list, and the derived numbers the invariants in
    docs/PASSIVE_TEMPLATES_SPEC.md §3.4 are checked against."""
    active_area_um2 = float(params["active_area_um2"])
    connect_width_um = float(params["connect_width_um"])
    pad_size_um = float(params["pad_size_um"])
    bottom_extension_um = float(params["bottom_extension_um"])
    overlap_margin_um = float(params["overlap_margin_um"])
    membrane_margin_um = float(params["membrane_margin_um"])
    if active_area_um2 <= 0:
        raise ValueError("active_area_um2 must be > 0")

    base = _base_pentagon_vertices()
    base_area = _polygon_area(base)
    scale = math.sqrt(active_area_um2 / base_area)
    pentagon = [(x * scale, y * scale) for x, y in base]
    pentagon_area = _polygon_area(pentagon)

    xs = [p[0] for p in pentagon]
    ys = [p[1] for p in pentagon]
    bbox = (min(xs), min(ys), max(xs), max(ys))

    # Attach point for top_connect: the edge whose midpoint has the largest
    # x (the pentagon's most eastward edge) -- deterministic given the fixed
    # base shape's vertex order and a positive uniform scale.
    n = len(pentagon)
    mids = [((pentagon[i][0] + pentagon[(i + 1) % n][0]) / 2.0,
              (pentagon[i][1] + pentagon[(i + 1) % n][1]) / 2.0) for i in range(n)]
    attach_i = max(range(n), key=lambda i: mids[i][0])
    attach_x, attach_y = mids[attach_i]

    items: list[dict] = []
    tlayer, tdt = LAYER_METAL_TOP
    blayer, bdt = LAYER_METAL_BOT

    # top_electrode: the pentagon itself.
    items.append({
        "kind": "polygon", "layer": tlayer, "datatype": tdt,
        "points_um": [list(p) for p in pentagon],
    })

    # top_connect: a horizontal strip from the attach edge's midpoint,
    # running EAST to a probe pad. Touches (and stays on the same layer as)
    # the pentagon, so it forms one continuous top-electrode net.
    top_pad_x0 = bbox[2] + bottom_extension_um * 0.5 + pad_size_um  # comfortably east of the pentagon
    top_connect_box = [attach_x, attach_y - connect_width_um / 2.0,
                        top_pad_x0, attach_y + connect_width_um / 2.0]
    items.append({"kind": "box", "layer": tlayer, "datatype": tdt, "bbox_um": top_connect_box})
    top_pad_box = [top_pad_x0, attach_y - pad_size_um / 2.0,
                   top_pad_x0 + pad_size_um, attach_y + pad_size_um / 2.0]
    items.append({"kind": "box", "layer": tlayer, "datatype": tdt, "bbox_um": top_pad_box})

    # bottom_electrode: a rectangle that fully covers the pentagon's bbox
    # (plus a small margin on the north/south/east sides) and reaches
    # further WEST (opposite the top connection's east side) to its own pad
    # -- "extends beyond the pentagon on the opposite side from the top
    # connection". Because it is a superset of the pentagon's bbox, the
    # top/bottom overlap area is the FULL pentagon area (>0.9x trivially).
    bottom_pad_x0 = bbox[0] - bottom_extension_um - pad_size_um
    bottom_box = [bottom_pad_x0, bbox[1] - overlap_margin_um,
                  bbox[2] + overlap_margin_um, bbox[3] + overlap_margin_um]
    items.append({"kind": "box", "layer": blayer, "datatype": bdt, "bbox_um": bottom_box})
    bottom_pad_box = [bottom_pad_x0, -pad_size_um / 2.0,
                       bottom_pad_x0 + pad_size_um, pad_size_um / 2.0]
    items.append({"kind": "box", "layer": blayer, "datatype": bdt, "bbox_um": bottom_pad_box})

    membrane_box = [bbox[0] - membrane_margin_um, bbox[1] - membrane_margin_um,
                     bbox[2] + membrane_margin_um, bbox[3] + membrane_margin_um]
    items.append({
        "kind": "box", "layer": LAYER_MEMBRANE[0], "datatype": LAYER_MEMBRANE[1],
        "bbox_um": membrane_box,
    })

    ports = [
        {
            "name": "TOP", "center_um": [top_pad_x0 + pad_size_um, attach_y],
            "orientation": 0.0, "width_um": pad_size_um,
            "port_type": "electrical", "net": "TOP",
            "target_layer": "%d/%d" % LAYER_METAL_TOP,
        },
        {
            "name": "BOT", "center_um": [bottom_pad_x0, 0.0],
            "orientation": 180.0, "width_um": pad_size_um,
            "port_type": "electrical", "net": "BOT",
            "target_layer": "%d/%d" % LAYER_METAL_BOT,
        },
    ]

    # StackSpec: DESCRIPTIVE ONLY (illustrates the mechanism). It documents
    # top->bottom conductor adjacency; StackSpec has no interlayer-dielectric
    # concept, so the piezo film between these two conductors in the real
    # stack is noted here in prose, not as a StackSpec entry.
    stack = StackSpec.from_dict({
        "conductors": [
            {"layer": "%d/%d" % LAYER_METAL_TOP, "role": "top_electrode"},
            {"layer": "%d/%d" % LAYER_METAL_BOT, "role": "bottom_electrode"},
        ],
        # top -> bottom vertical order; the piezo film sits BETWEEN these
        # two conductors in the real stack (not modeled by StackSpec).
        "order": ["%d/%d" % LAYER_METAL_TOP, "%d/%d" % LAYER_METAL_BOT],
    })

    edge_classes = _edge_direction_classes_deg(pentagon)
    summary = {
        "pentagon_area_um2": pentagon_area,
        "target_area_um2": active_area_um2,
        "area_error_frac": abs(pentagon_area - active_area_um2) / active_area_um2,
        "pentagon_bbox_um": list(bbox),
        "attach_edge_index": attach_i,
        "edge_direction_classes_deg": edge_classes,
        "top_pad_box_um": top_pad_box,
        "bottom_pad_box_um": bottom_pad_box,
        "bottom_box_um": bottom_box,
        "membrane_box_um": membrane_box,
        "stack": stack.to_dict(),
    }
    return {"cell": CELL_NAME, "items": items, "ports": ports, "summary": summary, "pentagon_um": pentagon}


def _port_triangle_points_um(center_um: list[float], orientation_deg: float,
                              width_um: float) -> list[tuple[float, float]]:
    """Hand-drawn-marker-compatible triangle (mirrors the base-edge-at-origin
    convention in klink_plugin/python/klink_server/port_pcell.py): the base
    edge sits ON the contact point, the tip points outward in the port's
    orientation. A plain 3-point polygon on the port layer is exactly what
    `klink.port.workflow.is_handdrawn_port_marker` / `recognize_handdrawn_ports`
    expect, so this offline marker round-trips through the live recognizer."""
    hw = width_um / 2.0
    d = hw / math.sqrt(3.0)
    cx, cy = center_um
    rad = math.radians(orientation_deg)
    cos_r, sin_r = math.cos(rad), math.sin(rad)

    def rot(x: float, y: float) -> tuple[float, float]:
        return (cx + x * cos_r - y * sin_r, cy + x * sin_r + y * cos_r)

    return [rot(d, 0.0), rot(0.0, -hw), rot(0.0, hw)]


def _no_two_edges_parallel(edge_classes_deg: list[float], tol_deg: float = 1e-6) -> bool:
    n = len(edge_classes_deg)
    for i in range(n):
        for j in range(i + 1, n):
            if abs(edge_classes_deg[i] - edge_classes_deg[j]) < tol_deg:
                return False
    return True


def _self_check(bundle: dict, ly, top, layer_idx: dict) -> dict:
    """Region-based invariant check (spec §3.4)."""
    import klayout.db as kdb

    s = bundle["summary"]
    top_region = kdb.Region(top.begin_shapes_rec(layer_idx[LAYER_METAL_TOP]))
    top_region.merge()
    bot_region = kdb.Region(top.begin_shapes_rec(layer_idx[LAYER_METAL_BOT]))
    bot_region.merge()

    # Isolate the pentagon polygon alone (not the connect strip/pad) for the
    # overlap-fraction check: rebuild it as its own Region.
    dbu = ly.dbu
    pentagon_region = kdb.Region(kdb.Polygon([
        kdb.Point(int(round(x / dbu)), int(round(y / dbu))) for x, y in bundle["pentagon_um"]
    ]))
    overlap = pentagon_region & bot_region
    overlap_area_um2 = overlap.area() * dbu * dbu
    pentagon_area_um2 = pentagon_region.area() * dbu * dbu

    parallel_ok = _no_two_edges_parallel(s["edge_direction_classes_deg"])

    return {
        "top_region_count": top_region.count(),
        "bottom_region_count": bot_region.count(),
        "no_two_edges_parallel": parallel_ok,
        "pentagon_area_um2": round(pentagon_area_um2, 6),
        "area_error_frac": s["area_error_frac"],
        "area_within_1pct": s["area_error_frac"] <= 0.01,
        "top_bottom_overlap_um2": round(overlap_area_um2, 6),
        "overlap_frac_of_pentagon": overlap_area_um2 / pentagon_area_um2 if pentagon_area_um2 else 0.0,
        "overlap_ok": (overlap_area_um2 / pentagon_area_um2) > 0.9 if pentagon_area_um2 else False,
    }


def write_offline(params: dict, out_path: str) -> dict:
    import klayout.db as kdb

    bundle = build_baw_fbar(params)
    dbu = 0.001
    ly = kdb.Layout()
    ly.dbu = dbu
    top = ly.create_cell(bundle["cell"])
    layer_idx: dict[tuple[int, int], int] = {}

    def li(layer: int, datatype: int) -> int:
        key = (layer, datatype)
        if key not in layer_idx:
            layer_idx[key] = ly.layer(layer, datatype)
        return layer_idx[key]

    for item in bundle["items"]:
        idx = li(item["layer"], item["datatype"])
        if item["kind"] == "box":
            x0, y0, x1, y1 = item["bbox_um"]
            top.shapes(idx).insert(kdb.Box(
                int(round(x0 / dbu)), int(round(y0 / dbu)),
                int(round(x1 / dbu)), int(round(y1 / dbu)),
            ))
        elif item["kind"] == "polygon":
            pts = [kdb.Point(int(round(x / dbu)), int(round(y / dbu))) for x, y in item["points_um"]]
            top.shapes(idx).insert(kdb.Polygon(pts))

    port_idx = li(*LAYER_PORT)
    for port in bundle["ports"]:
        pts = _port_triangle_points_um(port["center_um"], port["orientation"], port["width_um"])
        top.shapes(port_idx).insert(kdb.Polygon([
            kdb.Point(int(round(x / dbu)), int(round(y / dbu))) for x, y in pts
        ]))

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    ly.write(out_path)

    check = _self_check(bundle, ly, top, layer_idx)
    report = dict(check)
    report.update({
        "ok": bool(check["no_two_edges_parallel"] and check["area_within_1pct"] and check["overlap_ok"]),
        "gds_path": out_path,
        "cell": bundle["cell"],
        "ports": len(bundle["ports"]),
        "summary": {k: v for k, v in bundle["summary"].items() if k != "stack"},
        "stack": bundle["summary"]["stack"],
    })
    return report


def push_live(params: dict, *, port: int, keep: bool) -> dict:
    from klink import KLinkClient

    bundle = build_baw_fbar(params)
    with KLinkClient(port=port).connect() as client:
        cells = {c["name"] for c in client.cell_list(limit=1000).get("cells", [])}
        if bundle["cell"] in cells:
            client.cell_delete(bundle["cell"], recursive=True)
        client.cell_create(bundle["cell"])
        client.layer_ensure(*LAYER_METAL_TOP, name="BAW_METAL_TOP")
        client.layer_ensure(*LAYER_METAL_BOT, name="BAW_METAL_BOT")
        client.layer_ensure(*LAYER_MEMBRANE, name="BAW_MEMBRANE_RELEASE")
        client.layer_ensure(*LAYER_PORT, name="KLINK_PORTS")

        inserted = client.shape_insert_many(bundle["cell"], bundle["items"])
        items = [
            {
                "name": p["name"],
                "center_um": p["center_um"],
                "orientation": p["orientation"],
                "width_um": p["width_um"],
                "port_type": p["port_type"],
                "net": p["net"],
                "target_layer": p["target_layer"],
            }
            for p in bundle["ports"]
        ]
        client.call("port.mark_many", {
            "cell": bundle["cell"],
            "layer": "%d/%d" % LAYER_PORT,
            "items": items,
        })
        client.show_cell(bundle["cell"], zoom_fit=True)

        report = {
            "ok": True,
            "cell": bundle["cell"],
            "port": port,
            "inserted": int(inserted.get("inserted", 0)) if isinstance(inserted, dict) else None,
            "ports": len(bundle["ports"]),
            "summary": {k: v for k, v in bundle["summary"].items() if k != "stack"},
            "kept": bool(keep),
        }
        if not keep:
            try:
                client.cell_delete(bundle["cell"], recursive=True)
            except Exception:
                pass
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="push to a KLayout klink session instead of writing a GDS")
    parser.add_argument("--port", type=int, default=8765, help="klink RPC port (default 8765)")
    parser.add_argument("--keep", action="store_true", help="keep the live disposable cell instead of deleting it")
    args = parser.parse_args(argv)

    report = {
        "mode": "live" if args.live else "offline",
        "command": "%s%s" % (sys.argv[0], " --live" if args.live else ""),
        "disclaimer": "geometry template, NOT a validated acoustic design; NO "
                       "frequency/material claims; plan view only; tune the "
                       "numbers for your process and verify with your own models",
        "params": DEFAULT_PARAMS,
    }
    if args.live:
        report["live"] = push_live(DEFAULT_PARAMS, port=args.port, keep=args.keep)
        report["ok"] = bool(report["live"]["ok"])
    else:
        out_path = os.path.abspath(os.path.join("test_outputs", "baw_fbar_planview_demo.gds"))
        report["offline"] = write_offline(DEFAULT_PARAMS, out_path)
        report["ok"] = bool(report["offline"]["ok"])
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

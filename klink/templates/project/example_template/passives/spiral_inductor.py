# klink PUBLIC example — runnable as-is, self-contained: imports only `klink`
# (no PDK, no NDA, no extra GDS). A parametric generator that carries its own
# layers (it does NOT read pdk.py — that pattern is for process-separated flows).
#
#   Run:    python <this file>                       # offline, writes a GDS
#           python <this file> --live --port <port>  # push to a KLayout session
#   In a `klink init` project these live in example_template/ — copy one into
#   custom_devices/ and adapt. See recipes/README.md.
#
"""Square spiral-inductor geometry template.

GEOMETRY TEMPLATE, NOT a validated electrical design: tune the numbers for
your process and verify inductance/Q with your own models. klink ships zero
process data — every number here is example-owned; copy this file and edit
it for your own stack.

Structure: a rectangular (square) spiral wound outward on `metal_top`,
starting at an inner opening of side `inner_size`. Each side of the spiral is
`track_width` wide; consecutive turns are separated by `spacing` (pitch =
track_width + spacing). Because the coil's own inner end is trapped inside
the winding, it cannot be routed directly: a `via` drops straight down from a
small pad at the inner end to a `metal_under` UNDERPASS strip that runs
beneath the turns out past the coil's own outline, where it surfaces as the
`IN` port. `OUT` is simply the coil's outer end, still on `metal_top`.

Params (all microns unless noted):
  turns             number of spiral turns, >= 1
  track_width       spiral track width
  spacing           gap between adjacent turns (pitch = track_width+spacing)
  inner_size        side of the inner opening square (first segment length)
  underpass_width   width of the metal_under crossunder strip

Ports: OUT (metal_top, the coil's outer end), IN (metal_under, the
underpass's outer end).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

# Example-owned layers -- edit these for your process. The port marker layer
# (999/99) is klink's reserved convention; leave it alone.
LAYER_METAL_TOP = (11, 0)
LAYER_METAL_UNDER = (12, 0)
LAYER_VIA = (13, 0)
LAYER_PORT = (999, 99)

CELL_NAME = "PUB_SPIRAL_INDUCTOR"

DEFAULT_PARAMS: dict = dict(
    turns=3,
    track_width=2.0,
    spacing=1.5,
    inner_size=10.0,
    underpass_width=3.0,
)


def _spiral_points(turns: int, inner_size: float, pitch: float) -> list[tuple[float, float]]:
    """Classic growing square spiral: 4 segments per turn (E, N, W, S), each
    pair of segments `pitch` longer than the pair two turns earlier so the
    coil winds outward without self-overlap. Starting point is (0, 0) — the
    inner end.

    Deterministic fact used by the underpass-crossing invariant below: the
    S-direction segment of turn k (0-indexed) always runs vertically through
    x = -(k+1)*pitch and straddles y=0 -- so a horizontal strip through y=0
    reaching out to x <= -turns*pitch crosses exactly `turns` such segments,
    one per turn.
    """
    directions = [(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)]  # E, N, W, S
    points = [(0.0, 0.0)]
    x, y = 0.0, 0.0
    seg_len = inner_size
    d_idx = 0
    for _ in range(turns * 4):
        dx, dy = directions[d_idx % 4]
        x += dx * seg_len
        y += dy * seg_len
        points.append((x, y))
        d_idx += 1
        if d_idx % 2 == 0:
            seg_len += pitch
    return points


def build_spiral(params: dict) -> dict:
    """Pure geometry builder (no klayout/klink import): returns the shape
    item list, the port list, and the derived numbers the invariants in
    docs/PASSIVE_TEMPLATES_SPEC.md §3.2 are checked against."""
    turns = int(params["turns"])
    track_width = float(params["track_width"])
    spacing = float(params["spacing"])
    inner_size = float(params["inner_size"])
    underpass_width = float(params["underpass_width"])
    if turns < 1:
        raise ValueError("turns must be >= 1")

    pitch = track_width + spacing
    points = _spiral_points(turns, inner_size, pitch)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    bbox = (min(xs), min(ys), max(xs), max(ys))

    # via / inner-end pad sizing: comfortably inside both the pad and the
    # underpass, by construction (see comments below).
    via_size = 0.5 * min(track_width, underpass_width)
    enclosure = 0.3 * via_size
    pad_size = max(track_width, via_size + 2.0 * enclosure)

    # The underpass is a single horizontal strip at y=0 (the inner end's y),
    # reaching from just past the inner-end pad on the +x side out to
    # (bbox min-x - one track_width of clearance) on the -x side -- i.e.
    # outside the coil's own outline, per the spec.
    underpass_x1 = pad_size / 2.0
    underpass_x0 = bbox[0] - track_width
    underpass_box = [underpass_x0, -underpass_width / 2.0, underpass_x1, underpass_width / 2.0]

    items: list[dict] = []
    tlayer, tdt = LAYER_METAL_TOP
    ulayer, udt = LAYER_METAL_UNDER
    vlayer, vdt = LAYER_VIA

    # metal_top: the coil, as a single continuous path (one shape -> one
    # merged region by construction, no self-short check needed), plus a
    # small square pad at the inner end so the via has somewhere to land
    # that is guaranteed to touch (and therefore merge with) the coil.
    items.append({
        "kind": "path", "layer": tlayer, "datatype": tdt,
        "points_um": [list(p) for p in points], "width_um": track_width,
    })
    items.append({
        "kind": "box", "layer": tlayer, "datatype": tdt,
        "bbox_um": [-pad_size / 2.0, -pad_size / 2.0, pad_size / 2.0, pad_size / 2.0],
    })

    # metal_under: the underpass strip, a single box (trivially 1 merged
    # region).
    items.append({
        "kind": "box", "layer": ulayer, "datatype": udt,
        "bbox_um": underpass_box,
    })

    # via: lands at the inner end, inside both the pad (metal_top) and the
    # underpass (metal_under).
    via_box = [-via_size / 2.0, -via_size / 2.0, via_size / 2.0, via_size / 2.0]
    items.append({
        "kind": "box", "layer": vlayer, "datatype": vdt,
        "bbox_um": via_box,
    })

    # The square spiral's own segments are already axis-aligned, so this is
    # exactly 0/90/180/270 in practice -- snap to the nearest 90 deg multiple
    # anyway so OUT hands routing a clean Manhattan-axis interface even if
    # floating-point noise ever nudges the raw atan2 result off-axis.
    raw_out_orientation = math.degrees(math.atan2(
        points[-1][1] - points[-2][1], points[-1][0] - points[-2][0],
    )) % 360.0
    out_orientation = round(raw_out_orientation / 90.0) * 90.0 % 360.0
    ports = [
        {
            "name": "OUT", "center_um": [points[-1][0], points[-1][1]],
            "orientation": out_orientation, "width_um": track_width,
            "port_type": "electrical", "net": "OUT",
            "target_layer": "%d/%d" % LAYER_METAL_TOP,
        },
        {
            "name": "IN", "center_um": [underpass_x0, 0.0],
            "orientation": 180.0, "width_um": underpass_width,
            "port_type": "electrical", "net": "IN",
            "target_layer": "%d/%d" % LAYER_METAL_UNDER,
        },
    ]

    # Underpass-crossing count: how many spiral segments' bounding boxes
    # overlap the underpass strip's bounding box (see _spiral_points
    # docstring for why this is >= turns by construction).
    crossings = 0
    for i in range(len(points) - 1):
        (sx0, sy0), (sx1, sy1) = points[i], points[i + 1]
        seg_x0, seg_x1 = sorted((sx0, sx1))
        seg_y0, seg_y1 = sorted((sy0, sy1))
        if (seg_x1 >= underpass_box[0] and seg_x0 <= underpass_box[2]
                and seg_y1 >= underpass_box[1] and seg_y0 <= underpass_box[3]):
            crossings += 1

    summary = {
        "turns": turns,
        "pitch_um": pitch,
        "bbox_um": list(bbox),
        "underpass_box_um": underpass_box,
        "via_box_um": via_box,
        "pad_box_um": [-pad_size / 2.0, -pad_size / 2.0, pad_size / 2.0, pad_size / 2.0],
        "underpass_crossings": crossings,
    }
    return {"cell": CELL_NAME, "items": items, "ports": ports, "summary": summary}


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


def _region_from_boxes(boxes_um: list[list[float]], dbu: float):
    import klayout.db as kdb
    region = kdb.Region()
    for (x0, y0, x1, y1) in boxes_um:
        region.insert(kdb.Box(
            int(round(x0 / dbu)), int(round(y0 / dbu)),
            int(round(x1 / dbu)), int(round(y1 / dbu)),
        ))
    return region


def _self_check(bundle: dict, ly, top, layer_idx: dict) -> dict:
    """Region-based invariant check (spec §3.2)."""
    import klayout.db as kdb

    dbu = ly.dbu
    top_region = kdb.Region(top.begin_shapes_rec(layer_idx[LAYER_METAL_TOP]))
    top_region.merge()
    under_region = kdb.Region(top.begin_shapes_rec(layer_idx[LAYER_METAL_UNDER]))
    under_region.merge()

    via_box_dbu = kdb.Box(*[int(round(v / dbu)) for v in bundle["summary"]["via_box_um"]])
    pad_box_dbu = kdb.Box(*[int(round(v / dbu)) for v in bundle["summary"]["pad_box_um"]])
    underpass_box_dbu = kdb.Box(*[int(round(v / dbu)) for v in bundle["summary"]["underpass_box_um"]])
    via_in_pad = pad_box_dbu.contains(via_box_dbu.p1) and pad_box_dbu.contains(via_box_dbu.p2)
    via_in_underpass = underpass_box_dbu.contains(via_box_dbu.p1) and underpass_box_dbu.contains(via_box_dbu.p2)

    return {
        "metal_top_region_count": top_region.count(),
        "metal_under_region_count": under_region.count(),
        "no_self_short": top_region.count() == 1 and under_region.count() == 1,
        "underpass_crossings": bundle["summary"]["underpass_crossings"],
        "underpass_ok": bundle["summary"]["underpass_crossings"] >= bundle["summary"]["turns"],
        "via_in_pad": bool(via_in_pad),
        "via_in_underpass": bool(via_in_underpass),
    }


def write_offline(params: dict, out_path: str) -> dict:
    import klayout.db as kdb

    bundle = build_spiral(params)
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
        elif item["kind"] == "path":
            pts = [kdb.Point(int(round(x / dbu)), int(round(y / dbu))) for x, y in item["points_um"]]
            width_dbu = int(round(item["width_um"] / dbu))
            top.shapes(idx).insert(kdb.Path(pts, width_dbu))

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
        "ok": bool(check["no_self_short"] and check["underpass_ok"]
                   and check["via_in_pad"] and check["via_in_underpass"]),
        "gds_path": out_path,
        "cell": bundle["cell"],
        "ports": len(bundle["ports"]),
        "summary": bundle["summary"],
    })
    return report


def push_live(params: dict, *, port: int, keep: bool) -> dict:
    from klink import KLinkClient, KLinkTransportError

    bundle = build_spiral(params)
    try:
        session = KLinkClient(port=port).connect()
    except KLinkTransportError as e:
        raise RuntimeError(
            f"could not connect to klink on port {port}: {e}\n"
            "Confirm KLayout is running with the klink plugin loaded, or "
            "pass --port <your session's klink port>."
        ) from e
    with session as client:
        cells = {c["name"] for c in client.cell_list(limit=1000).get("cells", [])}
        if bundle["cell"] in cells:
            client.cell_delete(bundle["cell"], recursive=True)
        client.cell_create(bundle["cell"])
        client.layer_ensure(*LAYER_METAL_TOP, name="SPIRAL_METAL_TOP")
        client.layer_ensure(*LAYER_METAL_UNDER, name="SPIRAL_METAL_UNDER")
        client.layer_ensure(*LAYER_VIA, name="SPIRAL_VIA")
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
            "summary": bundle["summary"],
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
        "disclaimer": "geometry template, NOT a validated electrical design; "
                       "tune the numbers for your process and verify with your own models",
        "params": DEFAULT_PARAMS,
    }
    if args.live:
        report["live"] = push_live(DEFAULT_PARAMS, port=args.port, keep=args.keep)
        report["ok"] = bool(report["live"]["ok"])
    else:
        out_path = os.path.abspath(os.path.join("test_outputs", "spiral_inductor_demo.gds"))
        report["offline"] = write_offline(DEFAULT_PARAMS, out_path)
        report["ok"] = bool(report["offline"]["ok"])
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

# klink PUBLIC example — runnable as-is, self-contained: imports only `klink`
# (no PDK, no NDA, no extra GDS). A parametric generator that carries its own
# layers (it does NOT read pdk.py — that pattern is for process-separated flows).
#
#   Run:    python <this file>                       # offline, writes a GDS
#           python <this file> --live --port <port>  # push to a KLayout session
#   In a `klink init` project these live in example_template/ — copy one into
#   custom_devices/ and adapt. See recipes/README.md.
#
"""SAW (surface acoustic wave) IDT filter geometry template.

GEOMETRY TEMPLATE, NOT a validated acoustic design: we make NO
frequency/material claims. Real SAW response depends on the piezo substrate
cut/orientation, which this plan-view template does not model at all. Tune
the numbers for your process and verify with your own acoustic simulation.
klink ships zero process data — every number here is example-owned; copy
this file and edit it for your own design.

Structure: two identical interdigital transducers (TX, RX) facing each other
along the acoustic axis (x), each electrically identical to the IDC template
(idc_capacitor.py) turned into an acoustic comb: two bus bars parallel to the
acoustic axis, alternating fingers of width = pitch/4 (metallization ratio
0.5, so the finger-to-finger gap is also pitch/4) overlapping over a length
of `aperture`. Optional shorted-grating reflectors sit `reflector_gap`
outside each IDT, apodization is NOT modeled (uniform overlap only -- a
future knob).

Params (all microns unless noted):
  pitch             electrode period; electrode width = pitch/4
  pairs             finger PAIRS per IDT (total fingers = 2*pairs)
  aperture          finger overlap length (acoustic aperture)
  bus_width         bus bar thickness
  idt_gap           edge-to-edge distance between the two IDTs along x
  reflector_fingers reflector finger count per grating (0 disables)
  reflector_gap     edge-to-edge distance from an IDT to its reflector

Ports: TX_P/TX_N (transmitter IDT buses), RX_P/RX_N (receiver IDT buses),
all outward-facing on the bus outer edge.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

# Example-owned layer -- edit this for your process. The port marker layer
# (999/99) is klink's reserved convention; leave it alone.
LAYER_METAL = (14, 0)
LAYER_PORT = (999, 99)

CELL_NAME = "PUB_SAW_IDT_FILTER"

DEFAULT_PARAMS: dict = dict(
    pitch=4.0,
    pairs=12,
    aperture=40.0,
    bus_width=6.0,
    idt_gap=30.0,
    reflector_fingers=8,
    reflector_gap=4.0,
)


def _comb_width(finger_count: int, finger_width: float, gap: float) -> float:
    return finger_count * finger_width + (finger_count - 1) * gap


def _comb_items(x0: float, finger_count: int, finger_width: float, gap: float,
                 channel_h: float, bus_width: float, layer: tuple[int, int],
                 *, alternate: bool) -> tuple[list[dict], float]:
    """One interdigitated comb (shared shape with idc_capacitor.py's IDC):
    two opposing buses spanning the acoustic axis, fingers perpendicular.
    If `alternate` is False every finger spans the full channel and touches
    BOTH buses (the shorted-grating reflector case); if True fingers
    alternate polarity and stop `gap` short of the opposite bus (the IDT
    case). Returns (items, comb_width_um)."""
    layer_i, dt = layer
    width = _comb_width(finger_count, finger_width, gap)
    pitch = finger_width + gap
    items: list[dict] = []
    items.append({
        "kind": "box", "layer": layer_i, "datatype": dt,
        "bbox_um": [x0, -bus_width, x0 + width, 0.0],
    })
    items.append({
        "kind": "box", "layer": layer_i, "datatype": dt,
        "bbox_um": [x0, channel_h, x0 + width, channel_h + bus_width],
    })
    for i in range(finger_count):
        fx0 = x0 + i * pitch
        fx1 = fx0 + finger_width
        if not alternate:
            y0, y1 = 0.0, channel_h  # shorted: touches both bars
        elif i % 2 == 0:
            y0, y1 = 0.0, channel_h - gap
        else:
            y0, y1 = gap, channel_h
        items.append({
            "kind": "box", "layer": layer_i, "datatype": dt,
            "bbox_um": [fx0, y0, fx1, y1],
        })
    return items, width


def build_saw_idt(params: dict) -> dict:
    """Pure geometry builder (no klayout/klink import): returns the shape
    item list, the port list, and the derived numbers the invariants in
    docs/PASSIVE_TEMPLATES_SPEC.md §3.3 are checked against."""
    pitch = float(params["pitch"])
    pairs = int(params["pairs"])
    aperture = float(params["aperture"])
    bus_width = float(params["bus_width"])
    idt_gap = float(params["idt_gap"])
    reflector_fingers = int(params["reflector_fingers"])
    reflector_gap = float(params["reflector_gap"])
    if pairs < 1:
        raise ValueError("pairs must be >= 1")

    finger_width = pitch / 4.0  # metallization ratio 0.5 -> width = gap = pitch/4
    gap = pitch / 4.0
    finger_count = 2 * pairs
    # channel_h is the IDT's vertical span (see idc_capacitor.py's IDC
    # comb): both alternating finger types are `aperture` long and each
    # stops `gap` short of the opposite bus.
    channel_h = aperture + gap

    items: list[dict] = []

    tx_items, tx_width = _comb_items(0.0, finger_count, finger_width, gap,
                                      channel_h, bus_width, LAYER_METAL, alternate=True)
    items += tx_items
    tx_bbox = [0.0, tx_width]  # x-range of the TX comb

    rx_x0 = tx_width + idt_gap
    rx_items, rx_width = _comb_items(rx_x0, finger_count, finger_width, gap,
                                      channel_h, bus_width, LAYER_METAL, alternate=True)
    items += rx_items
    rx_bbox = [rx_x0, rx_x0 + rx_width]

    reflector_bboxes: list[list[float]] = []
    if reflector_fingers > 0:
        refl_width = _comb_width(reflector_fingers, finger_width, gap)
        tx_refl_x0 = tx_bbox[0] - reflector_gap - refl_width
        tx_refl_items, _ = _comb_items(tx_refl_x0, reflector_fingers, finger_width, gap,
                                        channel_h, bus_width, LAYER_METAL, alternate=False)
        items += tx_refl_items
        reflector_bboxes.append([tx_refl_x0, tx_refl_x0 + refl_width])

        rx_refl_x0 = rx_bbox[1] + reflector_gap
        rx_refl_items, _ = _comb_items(rx_refl_x0, reflector_fingers, finger_width, gap,
                                        channel_h, bus_width, LAYER_METAL, alternate=False)
        items += rx_refl_items
        reflector_bboxes.append([rx_refl_x0, rx_refl_x0 + refl_width])

    def bus_ports(prefix: str, x0: float, width: float) -> list[dict]:
        cx = x0 + width / 2.0
        return [
            {
                "name": "%s_N" % prefix, "center_um": [cx, -bus_width],
                "orientation": 270.0, "width_um": min(bus_width, width),
                "port_type": "electrical", "net": "%s_N" % prefix,
                "target_layer": "%d/%d" % LAYER_METAL,
            },
            {
                "name": "%s_P" % prefix, "center_um": [cx, channel_h + bus_width],
                "orientation": 90.0, "width_um": min(bus_width, width),
                "port_type": "electrical", "net": "%s_P" % prefix,
                "target_layer": "%d/%d" % LAYER_METAL,
            },
        ]

    ports = bus_ports("TX", tx_bbox[0], tx_width) + bus_ports("RX", rx_bbox[0], rx_width)

    overall_x0 = reflector_bboxes[0][0] if reflector_bboxes else tx_bbox[0]
    overall_x1 = reflector_bboxes[-1][1] if reflector_bboxes else rx_bbox[1]
    summary = {
        "electrode_width_um": finger_width,
        "electrode_gap_um": gap,
        "aperture_um": aperture,
        "channel_height_um": channel_h,
        "tx_bbox_x_um": tx_bbox,
        "rx_bbox_x_um": rx_bbox,
        "reflector_bboxes_x_um": reflector_bboxes,
        "overall_bbox_um": [overall_x0, -bus_width, overall_x1, channel_h + bus_width],
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


def _region_in_xrange(top, layer_idx: int, x0_um: float, x1_um: float, dbu: float):
    import klayout.db as kdb
    clip = kdb.Region(kdb.Box(
        int(round(x0_um / dbu)), -(1 << 30), int(round(x1_um / dbu)), (1 << 30),
    ))
    region = kdb.Region(top.begin_shapes_rec(layer_idx))
    region.merge()
    return region & clip


def _self_check(bundle: dict, ly, top, layer_idx: int) -> dict:
    """Region-based invariant check (spec §3.3): per-IDT merged-region
    count, electrode width, and (if enabled) per-grating merged-region
    count -- each measured by clipping the whole-layer region to that
    element's own x-range."""
    dbu = ly.dbu
    s = bundle["summary"]

    tx_region = _region_in_xrange(top, layer_idx, s["tx_bbox_x_um"][0], s["tx_bbox_x_um"][1], dbu)
    rx_region = _region_in_xrange(top, layer_idx, s["rx_bbox_x_um"][0], s["rx_bbox_x_um"][1], dbu)

    reflector_counts = []
    for (rx0, rx1) in s["reflector_bboxes_x_um"]:
        refl_region = _region_in_xrange(top, layer_idx, rx0, rx1, dbu)
        reflector_counts.append(refl_region.count())

    return {
        "tx_region_count": tx_region.count(),
        "rx_region_count": rx_region.count(),
        "tx_ok": tx_region.count() == 2,
        "rx_ok": rx_region.count() == 2,
        "reflector_region_counts": reflector_counts,
        "reflectors_ok": all(c == 1 for c in reflector_counts) if reflector_counts else True,
        "electrode_width_um": s["electrode_width_um"],
    }


def write_offline(params: dict, out_path: str) -> dict:
    import klayout.db as kdb

    bundle = build_saw_idt(params)
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
        x0, y0, x1, y1 = item["bbox_um"]
        top.shapes(idx).insert(kdb.Box(
            int(round(x0 / dbu)), int(round(y0 / dbu)),
            int(round(x1 / dbu)), int(round(y1 / dbu)),
        ))

    port_idx = li(*LAYER_PORT)
    for port in bundle["ports"]:
        pts = _port_triangle_points_um(port["center_um"], port["orientation"], port["width_um"])
        top.shapes(port_idx).insert(kdb.Polygon([
            kdb.Point(int(round(x / dbu)), int(round(y / dbu))) for x, y in pts
        ]))

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    ly.write(out_path)

    check = _self_check(bundle, ly, top, layer_idx[LAYER_METAL])
    report = dict(check)
    report.update({
        "ok": bool(check["tx_ok"] and check["rx_ok"] and check["reflectors_ok"]),
        "gds_path": out_path,
        "cell": bundle["cell"],
        "ports": len(bundle["ports"]),
        "summary": bundle["summary"],
    })
    return report


def push_live(params: dict, *, port: int, keep: bool) -> dict:
    from klink import KLinkClient, KLinkTransportError

    bundle = build_saw_idt(params)
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
        client.layer_ensure(*LAYER_METAL, name="SAW_METAL")
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
        "disclaimer": "geometry template, NOT a validated acoustic design; NO "
                       "frequency/material claims; tune the numbers for your "
                       "process and verify with your own models",
        "params": DEFAULT_PARAMS,
    }
    if args.live:
        report["live"] = push_live(DEFAULT_PARAMS, port=args.port, keep=args.keep)
        report["ok"] = bool(report["live"]["ok"])
    else:
        out_path = os.path.abspath(os.path.join("test_outputs", "saw_idt_filter_demo.gds"))
        report["offline"] = write_offline(DEFAULT_PARAMS, out_path)
        report["ok"] = bool(report["offline"]["ok"])
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

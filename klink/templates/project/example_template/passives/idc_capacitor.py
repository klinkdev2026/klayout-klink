# klink PUBLIC example — runnable as-is, self-contained: imports only `klink`
# (no PDK, no NDA, no extra GDS). A parametric generator that carries its own
# layers (it does NOT read pdk.py — that pattern is for process-separated flows).
#
#   Run:    python <this file>                       # offline, writes a GDS
#           python <this file> --live --port <port>  # push to a KLayout session
#   In a `klink init` project these live in example_template/ — copy one into
#   custom_devices/ and adapt. See recipes/README.md.
#
"""Interdigitated-capacitor (IDC) geometry template.

GEOMETRY TEMPLATE, NOT a validated electrical design: tune the numbers for
your process and verify capacitance/loss with your own models. klink ships
zero process data — every number here is example-owned; copy this file and
edit it for your own stack.

Structure: two opposing horizontal bus bars (bottom bus, top bus) with
vertical fingers alternating ownership left-to-right (even index -> bottom
bus, odd index -> top bus). Every finger stops `gap` short of the OPPOSITE
bus, and adjacent fingers (regardless of which bus they belong to) are always
exactly `gap` apart in x — so `gap` is simultaneously the finger-to-finger
pitch gap and the finger-to-opposite-bus clearance. Single metal layer.

Params (all microns unless noted):
  finger_count  total finger count, >= 2 (alternates bottom/top)
  finger_length length of each finger from its own bus toward the gap
  finger_width  finger width (also sets the finger pitch: width + gap)
  gap           finger-to-finger AND finger-to-opposite-bus clearance
  bus_width     bus bar thickness

Ports: P1 (bottom bus, outward = -y / 270 deg), P2 (top bus, outward = +y /
90 deg), both centered on the bus's outer edge -- ready for the routing
backends out of the box.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

# Example-owned layers -- edit these for your process. The port marker layer
# (999/99) is klink's reserved convention; leave it alone.
LAYER_METAL = (10, 0)
LAYER_PORT = (999, 99)

CELL_NAME = "PUB_IDC_CAPACITOR"

DEFAULT_PARAMS: dict = dict(
    finger_count=10,
    finger_length=20.0,
    finger_width=2.0,
    gap=1.5,
    bus_width=4.0,
)


def build_idc(params: dict) -> dict:
    """Pure geometry builder (no klayout/klink import): returns the shape
    item list, the port list, and the derived numbers the invariants in
    docs/PASSIVE_TEMPLATES_SPEC.md §3.1 are checked against."""
    finger_count = int(params["finger_count"])
    finger_length = float(params["finger_length"])
    finger_width = float(params["finger_width"])
    gap = float(params["gap"])
    bus_width = float(params["bus_width"])
    if finger_count < 2:
        raise ValueError("finger_count must be >= 2")

    pitch = finger_width + gap
    total_width = finger_count * finger_width + (finger_count - 1) * gap
    # Channel height (bus inner edge to bus inner edge): every finger is
    # `finger_length` long and stops `gap` short of the bus it does not
    # touch, so the channel is finger_length + gap regardless of which bus
    # a given finger is attached to (see module docstring).
    channel_h = finger_length + gap

    items: list[dict] = []
    layer, datatype = LAYER_METAL

    # Bottom bus: inner edge at y=0, extends down by bus_width.
    items.append({
        "kind": "box", "layer": layer, "datatype": datatype,
        "bbox_um": [0.0, -bus_width, total_width, 0.0],
    })
    # Top bus: inner edge at y=channel_h, extends up by bus_width.
    items.append({
        "kind": "box", "layer": layer, "datatype": datatype,
        "bbox_um": [0.0, channel_h, total_width, channel_h + bus_width],
    })
    for i in range(finger_count):
        x0 = i * pitch
        x1 = x0 + finger_width
        if i % 2 == 0:
            # bottom-attached: from the bottom bus inner edge up to
            # finger_length, stopping `gap` short of the top bus.
            y0, y1 = 0.0, finger_length
        else:
            # top-attached: from the top bus inner edge down to
            # finger_length, stopping `gap` short of the bottom bus.
            y0, y1 = channel_h - finger_length, channel_h
        items.append({
            "kind": "box", "layer": layer, "datatype": datatype,
            "bbox_um": [x0, y0, x1, y1],
        })

    ports = [
        {
            "name": "P1", "center_um": [total_width / 2.0, -bus_width],
            "orientation": 270.0, "width_um": min(bus_width, total_width),
            "port_type": "electrical", "net": "P1",
            "target_layer": "%d/%d" % LAYER_METAL,
        },
        {
            "name": "P2", "center_um": [total_width / 2.0, channel_h + bus_width],
            "orientation": 90.0, "width_um": min(bus_width, total_width),
            "port_type": "electrical", "net": "P2",
            "target_layer": "%d/%d" % LAYER_METAL,
        },
    ]

    summary = {
        "finger_count": finger_count,
        "finger_pitch_um": pitch,
        "total_width_um": total_width,
        "channel_height_um": channel_h,
        "bbox_um": [0.0, -bus_width, total_width, channel_h + bus_width],
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


def _self_check(bundle: dict) -> dict:
    """Region-based invariant check (spec §3.1), reused by both the offline
    writer's printed summary and (independently re-derived) the unit tests."""
    import klayout.db as kdb

    dbu = 0.001
    region = kdb.Region()
    for item in bundle["items"]:
        x0, y0, x1, y1 = item["bbox_um"]
        region.insert(kdb.Box(
            int(round(x0 / dbu)), int(round(y0 / dbu)),
            int(round(x1 / dbu)), int(round(y1 / dbu)),
        ))
    merged = region.dup()
    merged.merge()
    merged_count = merged.count()
    total_area_um2 = region.area() * dbu * dbu
    return {
        "merged_region_count": merged_count,
        "no_short": merged_count == 2,
        "total_metal_area_um2": round(total_area_um2, 6),
    }


def write_offline(params: dict, out_path: str) -> dict:
    import klayout.db as kdb

    bundle = build_idc(params)
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

    report = _self_check(bundle)
    report.update({
        "ok": bool(report["no_short"]),
        "gds_path": out_path,
        "cell": bundle["cell"],
        "ports": len(bundle["ports"]),
        "summary": bundle["summary"],
    })
    return report


def push_live(params: dict, *, port: int, keep: bool) -> dict:
    from klink import KLinkClient, KLinkTransportError

    bundle = build_idc(params)
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
        client.layer_ensure(*LAYER_METAL, name="IDC_METAL")
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
        out_path = os.path.abspath(os.path.join("test_outputs", "idc_capacitor_demo.gds"))
        report["offline"] = write_offline(DEFAULT_PARAMS, out_path)
        report["ok"] = bool(report["offline"]["ok"])
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

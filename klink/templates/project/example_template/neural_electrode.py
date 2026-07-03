# klink PUBLIC example — runnable as-is, self-contained: imports only `klink`
# (no PDK, no NDA, no extra GDS). A parametric generator that carries its own
# layers (it does NOT read pdk.py — that pattern is for process-separated flows).
#
#   Run:    python <this file> --port <your-klayout-rpc-port>
#   In a `klink init` project these live in example_template/ — copy one into
#   custom_devices/ and adapt. See recipes/README.md.
#
"""Harness-backed neural electrode PCell generator.

Generation order:
1. define pad and via geometry,
2. define port resources on electrical connection edges,
3. define corridor anchors,
4. call the existing tapered hybrid router.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass


from klink import KLinkClient
from klink.routing.backends.geometric.tapered_segments import (
    commit_tapered_hybrid_many,
    pair_ports_by_net_tokens,
    route_tapered_hybrid_many,
    unsupported_multi_port_net_errors,
)


@dataclass(frozen=True)
class HarnessPCellSpec:
    elec_rows: int = 4
    cell_name: str = "harnesspcell"
    pad_port_width_um: float = 30.0
    elec_port_width_um: float = 5.0
    route_spacing_um: float = 8.0
    route_via_x_um: float = -3050.0
    corridor_x_um: float = -1650.0

    probe_pad_x_um: tuple[float, float, float] = (-6650.0, -5450.0, -4250.0)
    probe_pad_top_dx_um: float = -0.15
    probe_pad_size_um: float = 150.0
    probe_via_size_um: float = 120.0
    probe_route_via_size_um: float = 48.0
    probe_mid_via_size_um: float = 48.0
    probe_pitch_y_um: float = 200.0
    symmetry_y_um: float = 50.0

    elec_left_x_um: tuple[float, float, float] = (-80.0, -40.0, 0.0)
    elec_right_x_um: tuple[float, float, float] = (2400.0, 2440.0, 2480.0)
    elec_pad_size_um: tuple[float, float] = (20.0, 21.0)
    elec_via_size_um: tuple[float, float] = (18.0, 4.0)
    elec_pad_y0_um: float = -45.0
    elec_pad_pitch_y_um: float = 30.0
    elec_via_stagger_um: tuple[float, float, float] = (-8.0, 0.0, 8.0)

    port_layer: str = "999/99"
    aux_port_layer: str = "999/98"
    anchor_layer: str = "999/1"

    frame_left_x_um: float = -7500.0
    frame_left_inner_x_um: float = -7400.0
    frame_thickness_um: float = 100.0
    frame_top_margin_um: float = 235.0
    frame_bottom_margin_um: float = 335.0
    frame_neck_outer_y_um: float = 265.0
    frame_arm_outer_y_um: float = 165.0
    frame_arm_inner_y_um: float = 65.0
    frame_corridor_clearance_um: float = 100.0

    @property
    def pads_per_half(self) -> int:
        return 3 * self.elec_rows

    @property
    def net_count(self) -> int:
        return 2 * self.pads_per_half

    def probe_pad_y_um(self) -> list[float]:
        bot = [
            self.symmetry_y_um - 115.0 - i * self.probe_pitch_y_um
            for i in range(self.pads_per_half)
        ]
        top = [
            self.symmetry_y_um + 115.0 + i * self.probe_pitch_y_um
            for i in range(self.pads_per_half)
        ]
        return list(reversed(bot)) + top

    def probe_x_for_y(self, base_x: float, y_um: float) -> float:
        return base_x + (self.probe_pad_top_dx_um if y_um > self.symmetry_y_um else 0.0)

    def elec_pad_y_um(self) -> list[float]:
        return [
            self.elec_pad_y0_um + row * self.elec_pad_pitch_y_um
            for row in range(self.elec_rows)
        ]

    def elec_via_y_um(self, row: int, col: int) -> float:
        return self.elec_pad_y_um()[row] + self.elec_via_stagger_um[col]

    def frame_neck_right_x_um(self) -> float:
        return self.frame_neck_left_x_um() + 200.0

    def frame_neck_left_x_um(self) -> float:
        return self.corridor_x_um + self.corridor_width_um() / 2.0 + self.frame_corridor_clearance_um

    def frame_top_bottom_right_x_um(self) -> float:
        return self.frame_neck_left_x_um() + self.frame_thickness_um

    def corridor_width_um(self) -> float:
        max_width = max(self.pad_port_width_um, self.elec_port_width_um)
        lane_pitch = max_width + self.route_spacing_um + max_width / 2.0
        return max(700.0, self.pads_per_half * lane_pitch + max_width)

    def frame_tip_base_x_um(self) -> float:
        return max(self.elec_right_x_um) + self.elec_pad_size_um[0]

    def frame_tip_apex_x_um(self) -> float:
        return self.frame_tip_base_x_um() + 200.0

    def frame_tip_x_um(self) -> float:
        return self.frame_tip_base_x_um() + 300.0

    def electrode_pad_y_bounds_um(self) -> tuple[float, float]:
        pad_half_h = self.elec_pad_size_um[1] / 2.0
        ys = self.elec_pad_y_um()
        return min(y - pad_half_h for y in ys), max(y + pad_half_h for y in ys)

    def frame_center_fill_y_range_um(self) -> tuple[float, float]:
        bottom_pad, top_pad = self.electrode_pad_y_bounds_um()
        bottom = bottom_pad - 9.5
        top = top_pad + 9.5
        return min(-self.frame_arm_inner_y_um, bottom), max(self.frame_arm_inner_y_um, top)

    def frame_arm_y_ranges_um(self) -> tuple[tuple[float, float], tuple[float, float]]:
        fill_bottom, fill_top = self.frame_center_fill_y_range_um()
        thickness = self.frame_arm_outer_y_um - self.frame_arm_inner_y_um
        return (fill_bottom - thickness, fill_bottom), (fill_top, fill_top + thickness)

    def frame_neck_center_y_um(self, *, top: bool) -> float:
        (bottom_outer, bottom_inner), (top_inner, top_outer) = self.frame_arm_y_ranges_um()
        return top_outer + 100.0 if top else bottom_outer - 100.0


def _box(cx: float, cy: float, w: float, h: float) -> list[float]:
    return [cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0]


def _dbu_edge(x_um: float, y0_um: float, y1_um: float, dbu: float) -> str:
    x = round(x_um / dbu)
    return "%d,%d,%d,%d" % (x, round(y0_um / dbu), x, round(y1_um / dbu))


def _rounded_neck_points(spec: HarnessPCellSpec, *, top: bool, steps: int = 72) -> list[list[float]]:
    cx = spec.frame_neck_right_x_um()
    cy = spec.frame_neck_center_y_um(top=top)
    outer_r = 200.0
    inner_r = 100.0
    points: list[list[float]] = []
    if top:
        for i in range(steps + 1):
            theta = math.pi + (math.pi / 2.0) * i / steps
            points.append([cx + outer_r * math.cos(theta), cy + outer_r * math.sin(theta)])
        for i in range(steps, -1, -1):
            theta = math.pi + (math.pi / 2.0) * i / steps
            points.append([cx + inner_r * math.cos(theta), cy + inner_r * math.sin(theta)])
    else:
        for i in range(steps + 1):
            theta = math.pi - (math.pi / 2.0) * i / steps
            points.append([cx + outer_r * math.cos(theta), cy + outer_r * math.sin(theta)])
        for i in range(steps, -1, -1):
            theta = math.pi - (math.pi / 2.0) * i / steps
            points.append([cx + inner_r * math.cos(theta), cy + inner_r * math.sin(theta)])
    return points


def _frame_items(spec: HarnessPCellSpec) -> list[dict]:
    items: list[dict] = []
    y_values = spec.probe_pad_y_um()
    outer_top = max(y_values) + spec.frame_top_margin_um
    outer_bottom = min(y_values) - spec.frame_bottom_margin_um
    t = spec.frame_thickness_um
    neck_left = spec.frame_neck_left_x_um()
    neck_right = spec.frame_neck_right_x_um()
    top_bottom_right = spec.frame_top_bottom_right_x_um()
    tip_base = spec.frame_tip_base_x_um()
    tip_apex = spec.frame_tip_apex_x_um()
    tip_x = spec.frame_tip_x_um()
    fill_bottom_y, fill_top_y = spec.frame_center_fill_y_range_um()
    (bottom_arm_outer_y, bottom_arm_inner_y), (top_arm_inner_y, top_arm_outer_y) = spec.frame_arm_y_ranges_um()
    bottom_neck_center_y = spec.frame_neck_center_y_um(top=False)
    top_neck_center_y = spec.frame_neck_center_y_um(top=True)
    tip_center_y = (fill_bottom_y + fill_top_y) / 2.0
    common: list[dict] = [
        {"type": "box", "bbox_um": [spec.frame_left_x_um, outer_bottom, top_bottom_right, outer_bottom + t]},
        {"type": "box", "bbox_um": [spec.frame_left_x_um, outer_bottom + t, spec.frame_left_inner_x_um, outer_top - t]},
        {"type": "box", "bbox_um": [spec.frame_left_x_um, outer_top - t, top_bottom_right, outer_top]},
        {"type": "box", "bbox_um": [neck_left, outer_bottom + t, top_bottom_right, bottom_neck_center_y]},
        {"type": "polygon", "points_um": _rounded_neck_points(spec, top=False)},
        {"type": "polygon", "points_um": _rounded_neck_points(spec, top=True)},
        {"type": "box", "bbox_um": [neck_left, top_neck_center_y, top_bottom_right, outer_top - t]},
        {"type": "box", "bbox_um": [neck_right, bottom_arm_outer_y, tip_base, bottom_arm_inner_y]},
        {"type": "box", "bbox_um": [neck_right, top_arm_inner_y, tip_base, top_arm_outer_y]},
        {
            "type": "polygon",
            "points_um": [
                [tip_base, bottom_arm_outer_y],
                [tip_base, bottom_arm_inner_y],
                [tip_apex, tip_center_y],
                [tip_base, top_arm_inner_y],
                [tip_base, top_arm_outer_y],
                [tip_x, top_arm_outer_y],
                [tip_x, bottom_arm_outer_y],
            ],
        },
    ]
    layer7_extra: list[dict] = [
        {"type": "box", "bbox_um": [neck_right, fill_bottom_y, tip_base, fill_top_y]},
        {
            "type": "polygon",
            "points_um": [
                [tip_base, fill_bottom_y],
                [tip_base, fill_top_y],
                [tip_apex, tip_center_y],
            ],
        },
    ]
    for layer in (6, 7):
        for item in common:
            items.append({**item, "layer": layer, "datatype": 0})
        if layer == 7:
            for item in layer7_extra:
                items.append({**item, "layer": layer, "datatype": 0})
    return items


def _layer_items(spec: HarnessPCellSpec) -> list[dict]:
    items: list[dict] = _frame_items(spec)
    for idx, y in enumerate(spec.probe_pad_y_um()):
        net_num = idx + 1
        for base_x in spec.probe_pad_x_um:
            x = spec.probe_x_for_y(base_x, y)
            for layer in (3, 5):
                items.append({"type": "box", "layer": layer, "datatype": 0, "bbox_um": _box(x, y, spec.probe_pad_size_um, spec.probe_pad_size_um)})

        pad_x = spec.probe_x_for_y(spec.probe_pad_x_um[0], y)
        items.append({"type": "box", "layer": 3, "datatype": 0, "bbox_um": [pad_x - 5.0, y - 15.0, spec.route_via_x_um - 15.0, y + 15.0]})
        items.append({"type": "box", "layer": 3, "datatype": 0, "bbox_um": _box(spec.route_via_x_um, y, 100.0, 100.0)})

        x0 = spec.probe_x_for_y(spec.probe_pad_x_um[0], y)
        items.append({"type": "box", "layer": 4, "datatype": 0, "bbox_um": _box(x0, y, spec.probe_via_size_um, spec.probe_via_size_um)})
        if net_num <= spec.pads_per_half:
            items.append({"type": "box", "layer": 1, "datatype": 0, "bbox_um": _box(spec.route_via_x_um, y, 50.0, 50.0)})
            items.append({"type": "box", "layer": 2, "datatype": 0, "bbox_um": _box(spec.route_via_x_um, y, spec.probe_route_via_size_um, spec.probe_route_via_size_um)})
        for base_x in spec.probe_pad_x_um[1:]:
            x = spec.probe_x_for_y(base_x, y)
            items.append({"type": "box", "layer": 4, "datatype": 0, "bbox_um": _box(x, y, spec.probe_mid_via_size_um, spec.probe_mid_via_size_um)})

    ew, eh = spec.elec_pad_size_um
    vw, vh = spec.elec_via_size_um
    for group_xs in (spec.elec_left_x_um, spec.elec_right_x_um):
        for row, pad_y in enumerate(spec.elec_pad_y_um()):
            for col, x in enumerate(group_xs):
                via_y = spec.elec_via_y_um(row, col)
                items.append({"type": "box", "layer": 5, "datatype": 0, "bbox_um": _box(x, pad_y, ew, eh)})
                items.append({"type": "box", "layer": 4, "datatype": 0, "bbox_um": _box(x, via_y, vw, vh)})
                items.append({"type": "box", "layer": 3, "datatype": 0, "bbox_um": _box(x, via_y, vw, vh)})
                if group_xs == spec.elec_right_x_um:
                    items.append({"type": "box", "layer": 2, "datatype": 0, "bbox_um": _box(x, via_y, vw, vh)})
    return items


def _mark_pad_ports(client: KLinkClient, spec: HarnessPCellSpec, dbu: float) -> None:
    for idx, y in enumerate(spec.probe_pad_y_um()):
        net_num = idx + 1
        x = spec.probe_x_for_y(spec.probe_pad_x_um[0], y) + spec.probe_via_size_um / 2.0
        half = spec.probe_via_size_um / 2.0
        if net_num == spec.net_count:
            name = "P0"
        else:
            name = f"P{35 + net_num}"
        client.call(
            "port.mark",
            {
                "cell": spec.cell_name,
                "layer": spec.port_layer,
                "name": name,
                "label": f"n{net_num}",
                "center_um": [x, y],
                "orientation": 0,
                "width_um": spec.pad_port_width_um,
                "port_type": "electrical",
                "net": f"n{net_num}",
                "target_layer": "1/0",
                "access_mode": "edge",
                "slide_allowed": True,
                "slide_edge": _dbu_edge(x, y - half, y + half, dbu),
                "show_label": True,
            },
        )


def _mark_route_source_ports(client: KLinkClient, spec: HarnessPCellSpec, dbu: float) -> None:
    half = spec.probe_route_via_size_um / 2.0
    for idx, y in enumerate(spec.probe_pad_y_um()):
        net_num = idx + 1
        route_layer = "1/0" if net_num <= spec.pads_per_half else "3/0"
        client.call(
            "port.mark",
            {
                "cell": spec.cell_name,
                "layer": spec.aux_port_layer,
                "name": f"R{net_num}",
                "label": f"route_n{net_num}",
                "center_um": [spec.route_via_x_um, y],
                "orientation": 0,
                "width_um": spec.pad_port_width_um,
                "port_type": "electrical",
                "net": f"n{net_num}",
                "target_layer": route_layer,
                "access_mode": "edge",
                "slide_allowed": True,
                "slide_edge": _dbu_edge(spec.route_via_x_um, y - half, y + half, dbu),
                "show_label": True,
            },
        )


def _mark_electrode_ports(client: KLinkClient, spec: HarnessPCellSpec) -> None:
    ew, _ = spec.elec_via_size_um
    cell0_electrode_names = [
        "P25", "P28", "P32",
        "P26", "P29", "P33",
        "P27", "P30", "P34",
        "P24", "P31", "P35",
    ]
    for row in range(spec.elec_rows):
        for col in range(3):
            bottom_net = row * 3 + col + 1
            top_net = bottom_net + spec.pads_per_half
            idx = row * 3 + col
            lx = spec.elec_left_x_um[col] + ew / 2.0
            ly = spec.elec_via_y_um(row, col)
            client.call(
                "port.mark",
                {
                    "cell": spec.cell_name,
                    "layer": spec.port_layer,
                    "name": cell0_electrode_names[idx] if idx < len(cell0_electrode_names) else f"PE{idx}",
                    "label": f"n{bottom_net}/{top_net}",
                    "center_um": [lx, ly],
                    "orientation": 180,
                    "width_um": spec.elec_port_width_um,
                    "port_type": "electrical",
                    "net": f"n{bottom_net},n{top_net}",
                    "target_layer": "3/0",
                    "access_mode": "point",
                    "slide_allowed": False,
                    "slide_edge": "",
                    "show_label": True,
                },
            )

            for name, net, x, y, layer in (
                (f"EB{bottom_net}", bottom_net, spec.elec_right_x_um[col] + ew / 2.0, spec.elec_via_y_um(row, col), "1/0"),
                (f"ET{top_net}", top_net, lx, ly, "3/0"),
            ):
                client.call(
                    "port.mark",
                    {
                        "cell": spec.cell_name,
                        "layer": spec.aux_port_layer,
                        "name": name,
                        "label": f"route_n{net}",
                        "center_um": [x, y],
                        "orientation": 180,
                        "width_um": spec.elec_port_width_um,
                        "port_type": "electrical",
                        "net": f"n{net}",
                        "target_layer": layer,
                        "access_mode": "point",
                        "slide_allowed": False,
                        "slide_edge": "",
                        "show_label": False,
                    },
                )


def _mark_corridor_anchors(client: KLinkClient, spec: HarnessPCellSpec) -> None:
    bottom_nets = ",".join(f"n{i}" for i in range(1, spec.pads_per_half + 1))
    top_nets = ",".join(f"n{i}" for i in range(spec.pads_per_half + 1, spec.net_count + 1))
    # Single source of truth: frame clearance derives from the same value.
    width = spec.corridor_width_um()
    for anchor_id, nets, priority in (
        ("A0_TOP_M3_CORRIDOR", top_nets, 0),
        ("A1_BOTTOM_M1_CORRIDOR", bottom_nets, 1),
    ):
        client.call(
            "anchor.mark",
            {
                "cell": spec.cell_name,
                "layer": spec.anchor_layer,
                "id": anchor_id,
                "kind": "corridor",
                "center_um": [spec.corridor_x_um, 0.0],
                "width_um": width,
                "path_points": "0,-1;0,1",
                "net": nets,
                "priority": priority,
                "required": True,
                "show_label": True,
            },
        )


def _route_harness_cell(client: KLinkClient, spec: HarnessPCellSpec) -> dict:
    all_ports = client.call("port.list", {"cell": spec.cell_name, "layer": spec.aux_port_layer, "sort": "name"}).get("ports", [])
    ports = [
        p for p in all_ports
        if str(p.get("name") or "").startswith(("R", "EB", "ET"))
    ]
    anchors = client.call("anchor.list", {"cell": spec.cell_name, "layer": spec.anchor_layer, "sort": "id"}).get("anchors", [])
    planning_errors = unsupported_multi_port_net_errors(ports)
    pairs = pair_ports_by_net_tokens(ports)
    by_layer: dict[str, list[dict]] = {}
    for pair in pairs:
        by_layer.setdefault(str(pair.get("route_layer") or "10/0"), []).append(pair)

    groups = []
    ok = not planning_errors
    for route_layer in sorted(by_layer):
        planned = route_tapered_hybrid_many(
            by_layer[route_layer],
            anchors=anchors,
            spacing_um=spec.route_spacing_um,
            strategy="uniform",
            angle_mode="manhattan",
            obstacle_bboxes=[],
            validate_sibling_overlap=True,
        )
        write = None
        if planned["ok"]:
            write = commit_tapered_hybrid_many(client, spec.cell_name, planned, route_layer=route_layer, clear=True)
        else:
            ok = False
        groups.append({
            "route_layer": route_layer,
            "ok": planned["ok"],
            "route_count": planned["route_count"],
            "sibling_overlap_count": len(planned.get("sibling_overlaps", [])),
            "obstacle_hit_count": len(planned.get("obstacle_hits", [])),
            "lane_reports": planned["lane_reports"],
            "errors": planned["errors"],
            "write": write,
        })
    return {
        "ok": ok,
        "cell": spec.cell_name,
        "port_count": len(ports),
        "anchor_count": len(anchors),
        "pair_count": len(pairs),
        "planning_errors": planning_errors,
        "groups": groups,
    }


def generate_harnesspcell(client: KLinkClient, spec: HarnessPCellSpec, *, route: bool = True) -> dict:
    cells = {c["name"] for c in client.cell_list(limit=1000).get("cells", [])}
    if spec.cell_name in cells:
        client.cell_delete(spec.cell_name, recursive=True)
    client.cell_create(spec.cell_name)

    client.call("port.set_layer", {"layer": spec.port_layer})
    client.call("anchor.set_layer", {"layer": spec.anchor_layer})
    for layer, name in ((1, "M1"), (2, "VIA13"), (3, "M3"), (4, "VIA35"), (5, "Pads"), (6, "Frame"), (7, "Protect")):
        client.layer_ensure(layer, 0, name=name)
    client.layer_ensure(999, 99, name="KLINK_PORTS")
    client.layer_ensure(999, 98, name="KLINK_ROUTE_PORTS")
    client.layer_ensure(999, 1, name="KLINK_ANCHORS")

    inserted = client.shape_insert_many(spec.cell_name, _layer_items(spec), dry_run=False)
    dbu = float(client.layout_info().get("dbu", 0.001))
    _mark_pad_ports(client, spec, dbu)
    _mark_route_source_ports(client, spec, dbu)
    _mark_electrode_ports(client, spec)
    _mark_corridor_anchors(client, spec)

    route_result = None
    if route:
        route_result = _route_harness_cell(client, spec)
    client.show_cell(spec.cell_name, zoom_fit=True)
    return {
        "cell": spec.cell_name,
        "elec_rows": spec.elec_rows,
        "expected_ports": spec.net_count + spec.pads_per_half,
        "expected_aux_ports": spec.net_count * 2,
        "inserted": inserted,
        "route": route_result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the neural electrode harnesspcell in KLayout.")
    parser.add_argument("--port", type=int, default=8765, help="your klink RPC port (8765 default)")
    parser.add_argument("--cell", default="harnesspcell")
    parser.add_argument("--elec-rows", type=int, default=4)
    parser.add_argument("--pad-port-width", type=float, default=30.0)
    parser.add_argument("--elec-port-width", type=float, default=5.0)
    parser.add_argument("--route-spacing", type=float, default=8.0)
    parser.add_argument("--route-via-x", type=float, default=-3050.0)
    parser.add_argument("--corridor-x", type=float, default=-1650.0)
    parser.add_argument("--no-route", action="store_true")
    args = parser.parse_args()

    spec = HarnessPCellSpec(
        cell_name=args.cell,
        elec_rows=args.elec_rows,
        pad_port_width_um=args.pad_port_width,
        elec_port_width_um=args.elec_port_width,
        route_spacing_um=args.route_spacing,
        route_via_x_um=args.route_via_x,
        corridor_x_um=args.corridor_x,
    )
    with KLinkClient(port=args.port).connect() as client:
        result = generate_harnesspcell(client, spec, route=not args.no_route)
    print(result)
    return 0 if not result.get("route") or result["route"].get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

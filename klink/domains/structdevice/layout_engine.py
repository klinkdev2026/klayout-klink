"""Algorithmic layout engine (domain-resident): netlist -> sized -> DERIVED
floorplan -> single-pass multilayer route -> draw -> device LVS.

Everything is profile-driven (klink.routing.grid.process_profile) -- no hardcoded
process constant -- and demand-driven (klink.routing.grid.floorplan) -- no magic
pitch. Routing is the negotiated dispatcher (Rust kernel else Python). This is
the engine `build_from_netlist` (netlist_build.py) calls; the examples are thin
callers of it.

Device geometry (channel/body/pad/terminal) is HARVESTED per the canonical
recipe into a device_geom.json (general over any device cell, never preset).
"""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from klink.routing.grid.capacity_grid import NetInput, build_capacity_grid
from klink.routing.grid.process_profile import ProcessProfile


# --------------------------------------------------------------------------- #
# harvested device geometry (recipe-derived; keyed by device cell name).
# The geom FILE path is deploy/example data (see your pdk.py
# DEVICE_GEOM_FILE) -- klink ships NO hardcoded path; the caller supplies it.
# --------------------------------------------------------------------------- #
def load_device_geom(path: str) -> Dict[str, dict]:
    p = Path(path)
    raw = json.loads(p.read_text()) if p.exists() else {}
    return raw


def _geom_tables(raw: Mapping[str, Any]):
    device_geom = {c: {"body": tuple(g["body"]), "channel": tuple(g["channel"])}
                   for c, g in raw.items()}
    device_pads = {c: {t: (g["terms"][t]["layer"], tuple(g["pads"][t])) for t in g["pads"]}
                   for c, g in raw.items()}
    terms = {c: g["terms"] for c, g in raw.items()}
    return device_geom, device_pads, terms


# --------------------------------------------------------------------------- #
# geometry helpers (general, recipe-based)
# --------------------------------------------------------------------------- #
def device_keepouts(instances, device_geom):
    """Channel boxes (wire+via keep-out) and body boxes (via-only) for the
    placed devices, in top coordinates."""
    channels, bodies = [], []
    for inst in instances:
        g = device_geom[inst["device_cell"]]
        dx, dy = inst["transform"]["dx_um"], inst["transform"]["dy_um"]
        cx1, cy1, cx2, cy2 = g["channel"]
        bx1, by1, bx2, by2 = g["body"]
        channels.append((dx + cx1, dy + cy1, dx + cx2, dy + cy2))
        bodies.append((dx + bx1, dy + by1, dx + bx2, dy + by2))
    return channels, bodies


def access_point(ref, layer, inst_map, terms):
    """Pad outer-edge attach point from the harvested recipe terminal:
    center + length/2 along the launch orientation. No preset dimensions."""
    inst, t = ref.split(".")
    cell, dx, dy = inst_map[inst]
    td = terms[cell][t]
    cx, cy = td["center"]
    ori = math.radians(td["orientation"])
    half = td["length"] / 2.0
    return (dx + cx + math.cos(ori) * half, dy + cy + math.sin(ori) * half)


def foreign_pad_boxes(nets, inst_map, device_pads):
    """Every device pad as a global box tagged with its owning net + layer
    (a wide pad crossed by a foreign wire is a short)."""
    ref2net = {ref: net["net"] for net in nets for ref in net["terminals"]}
    pads = []
    for ref, owner in ref2net.items():
        inst, t = ref.split(".")
        cell, dx, dy = inst_map[inst]
        layer, (x1, y1, x2, y2) = device_pads[cell][t]
        pads.append((owner, layer, (dx + x1, dy + y1, dx + x2, dy + y2)))
    return pads


def merge_polylines(seg_edges):
    """Merge unit cell-edges into maximal smooth polylines (centerlines),
    split at junctions/dead-ends, drop collinear interior points."""
    adj = defaultdict(set)
    for a, b in seg_edges:
        adj[a].add(b); adj[b].add(a)
    used = set()

    def ekey(a, b):
        return (a, b) if a <= b else (b, a)

    def compress(pts):
        if len(pts) <= 2:
            return pts
        out = [pts[0]]
        for p, c, n in zip(pts, pts[1:], pts[2:]):
            if not ((p[0] == c[0] == n[0]) or (p[1] == c[1] == n[1])):
                out.append(c)
        out.append(pts[-1])
        return out

    polylines = []
    starts = [n for n in adj if len(adj[n]) != 2] or ([next(iter(adj))] if adj else [])
    for s in starts:
        for nb in list(adj[s]):
            if ekey(s, nb) in used:
                continue
            path = [s, nb]; used.add(ekey(s, nb)); prev, cur = s, nb
            while len(adj[cur]) == 2:
                nxts = [x for x in adj[cur] if x != prev and ekey(cur, x) not in used]
                if not nxts:
                    break
                nx = nxts[0]; used.add(ekey(cur, nx)); path.append(nx); prev, cur = cur, nx
            polylines.append(compress(path))
    return polylines


# --------------------------------------------------------------------------- #
# placement (load-on-top columns; demand-derived row pitch is set by caller)
# --------------------------------------------------------------------------- #
def place_grid(netlist, rows, cols, *, profile: ProcessProfile, row_pitch: float,
               forbid_y_bands=()):
    """Pack gates into rows x cols; each gate a column of stacked devices
    (group['instances'] is [load, drv...] in role order -> load on top).

    ``forbid_y_bands`` = [(y_lo, y_hi), ...] horizontal bands no device row may
    intersect (e.g. a fixed probe-card pad row crossing the block): a row whose
    stack span would overlap a band is pushed BELOW it and packing continues
    from there -- this is how a block splits "half inside the pad ring, half
    outside" without any new placement engine. Default () = original packing
    byte-for-byte."""
    cell_of = {i["instance_id"]: i["device_cell"] for i in netlist["instances"]}
    stack_h = row_pitch  # conservative row extent: pitch covers stack + channel
    bands = sorted(tuple(map(float, b)) for b in forbid_y_bands)
    placement: Dict[str, tuple] = {}
    row_y: Dict[int, float] = {}
    for g, grp in enumerate(netlist["groups"]):
        r, c = divmod(g, cols)
        if r not in row_y:
            if not bands:                 # closed form: byte-identical default
                gy = profile.y_top_um - r * row_pitch
            else:
                gy = (row_y[r - 1] - row_pitch) if r else profile.y_top_um
                # a row occupies [gy - stack_h, gy]; push it below any band it cuts
                moved = True
                while moved:
                    moved = False
                    for (lo, hi) in bands:
                        if gy > lo and gy - stack_h < hi:
                            gy = lo       # row top lands exactly under the band
                            moved = True
            row_y[r] = gy
        gx = c * profile.col_pitch_um
        gy = row_y[r]
        for slot, xi in enumerate(grp["instances"]):
            placement[xi] = (cell_of[xi], gx, gy - slot * profile.y_step_um)
    return placement


def _instances(placement):
    return [{"instance_id": xi, "device_cell": c,
             "transform": {"dx_um": dx, "dy_um": dy, "rotation_deg": 0.0, "mirror": False}}
            for xi, (c, dx, dy) in placement.items()]


def _term_table(placement, terms):
    table = {}
    for xi, (c, dx, dy) in placement.items():
        for t, td in terms[c].items():
            cx, cy = td["center"]
            table[f"{xi}.{t}"] = ((dx + cx, dy + cy), td["layer"])
    return table


# --------------------------------------------------------------------------- #
# build grid + route + plan
# --------------------------------------------------------------------------- #
def build_grid(netlist, placement, *, profile, layers, vias, device_geom,
               device_pads, terms, extra_channels=(), extra_pads_by_layer=None,
               route_only=None, bbox_include_um=()):
    """`extra_channels` are ALL-LAYER wire+via keep-out boxes (device channels).
    `extra_pads_by_layer` is {layer: [(owner, box)]} of PER-LAYER, owner-aware
    keep-outs (e.g. the power-grid metal): they block FOREIGN nets on THAT layer
    only, so signals still get pin access + via escape on the other layers.
    `route_only` restricts which nets get NetInputs (e.g. signals only).
    `bbox_include_um` are extra boxes the grid must cover (e.g. a pre-placed
    probe-card pad ring far outside the device area); extra_pads_by_layer alone
    NEVER widens the bbox (the PDN relies on that)."""
    nets = [{"net": n["net_id"], "terminals": n["terminals"]} for n in netlist["nets"]]
    instances = _instances(placement)
    channels, bodies = device_keepouts(instances, device_geom)
    channels = list(channels) + [tuple(b) for b in extra_channels]
    inst_map = {i["instance_id"]: (i["device_cell"], i["transform"]["dx_um"],
                                   i["transform"]["dy_um"]) for i in instances}
    grow = profile.wire_width_um / 2.0 + profile.wire_clear_um
    def _grow(b):
        return (b[0] - grow, b[1] - grow, b[2] + grow, b[3] + grow)
    pad_by_layer = defaultdict(list)
    real_pad_by_layer = defaultdict(list)   # ungrown real metal -> ownership priority
    for owner, layer, box in foreign_pad_boxes(nets, inst_map, device_pads):
        pad_by_layer[layer].append((owner, _grow(box)))
        real_pad_by_layer[layer].append((owner, box))
    for layer, items in (extra_pads_by_layer or {}).items():
        for owner, box in items:
            pad_by_layer[layer].append((owner, _grow(tuple(box))))
            real_pad_by_layer[layer].append((owner, tuple(box)))

    term = _term_table(placement, terms)
    net_inputs, xs, ys = [], [], []
    for net in nets:
        acc = []
        for ref in net["terminals"]:
            (_x, _y), layer = term[ref]
            ax, ay = access_point(ref, layer, inst_map, terms)
            acc.append((ax, ay, layer)); xs.append(ax); ys.append(ay)
        if route_only is None or net["net"] in route_only:
            net_inputs.append(NetInput(net["net"], acc))
    for (x1, y1, x2, y2) in channels + bodies:
        xs += [x1, x2]; ys += [y1, y2]
    for (x1, y1, x2, y2) in bbox_include_um:
        xs += [x1, x2]; ys += [y1, y2]
    m = profile.margin_um
    bbox = (min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m)
    g = build_capacity_grid(layers=layers, bbox_um=bbox, pitch_um=profile.grid_pitch_um,
                            channel_boxes_um=channels, pad_boxes_by_layer=pad_by_layer,
                            device_body_boxes_um=bodies, via_rules=vias,
                            via_footprint_um=profile.via_pad_um,
                            real_pad_boxes_by_layer=real_pad_by_layer)
    return g, net_inputs, instances


def to_plan(g, result, instances, *, profile, cut_layer):
    inv = {i: l for i, l in enumerate(g.layers)}
    paths = {l: [] for l in g.layers}
    vias = defaultdict(set)
    for net, edges in result.edges.items():
        by_layer = defaultdict(list)
        for a, b in edges:
            if a[2] == b[2]:
                by_layer[a[2]].append(((a[0], a[1]), (b[0], b[1])))
            else:
                cut = cut_layer[tuple(sorted((inv[a[2]], inv[b[2]])))]
                vias[cut].add((round(g.cx(a[0]) / 1000.0, 3), round(g.cy(a[1]) / 1000.0, 3)))
        for lyr_i, segs in by_layer.items():
            for poly in merge_polylines(segs):
                paths[inv[lyr_i]].append([[round(g.cx(c[0]) / 1000.0, 3),
                                           round(g.cy(c[1]) / 1000.0, 3)] for c in poly])
    return {
        "devices": [{"cell": i["device_cell"], "dx_um": i["transform"]["dx_um"],
                     "dy_um": i["transform"]["dy_um"]} for i in instances],
        "paths": {l: p for l, p in paths.items() if p},
        "vias": {str(c): sorted(v) for c, v in vias.items() if v},
        "width_um": profile.wire_width_um, "via_pad_um": profile.via_pad_um,
        "via_cut_um": round(profile.via_pad_um - 2 * profile.litho_tol_um, 3),
    }


# --------------------------------------------------------------------------- #
# live draw (fitted-PCell device instances + smooth wires + via stacks).
# The device DRAW spec is example/process data: `devices` maps each device-cell
# KEY to {params, pcell, library, style, fit_table}. klink ships none of it --
# no PCell name, no fit-table path, no W/L parsing from cell names.
# --------------------------------------------------------------------------- #
def _pcell_item(devices: Mapping[str, Any], cell_key: str,
                dx_um: float, dy_um: float) -> Dict[str, Any]:
    spec = devices[cell_key]
    # The fitted PCell binds its table at REGISTRATION (ensure_pcell), so a drawn
    # instance carries only its geometry parameters -- no fit_table path leaks
    # into the layout.
    return {
        "pcell": spec["pcell"], "library": spec["library"],
        "params": {**spec["params"], "style": spec["style"]},
        "position_um": [dx_um, dy_um],
    }


def _device_layers(profile) -> List[str]:
    """Device-internal layers (gate/sd/channel) the fitted PCell draws on, taken
    from the PROFILE -- never hardcoded. Routing/via layers come from the plan."""
    out: List[str] = []
    for attr in ("gate_layer", "sd_layer", "channel_layer"):
        v = getattr(profile, attr, None)
        if v:
            out.append(v)
    return out


def ensure_pcell(client, devices: Mapping[str, Any]) -> None:
    """Register every distinct fitted PCell referenced by `devices` (idempotent
    within a session)."""
    seen = set()
    for spec in devices.values():
        key = (spec["pcell"], spec["fit_table"])
        if key in seen:
            continue
        seen.add(key)
        try:
            client.call("pcell.register_fitted",
                        {"name": spec["pcell"], "fit_table": spec["fit_table"]})
        except Exception as exc:
            if "already registered" not in str(exc):
                raise


def draw(client, cell: str, plan: Dict[str, Any], *, cut_layer, devices,
         device_layers=()) -> None:
    used = {tuple(int(x) for x in k.split("/")) for k in plan["paths"]}
    used |= {(int(c), 0) for c in plan["vias"]}
    used |= {tuple(int(x) for x in l.split("/")) for l in device_layers}
    existing = {c["name"] for c in client.cell_list()["cells"]}
    if cell in existing:
        client.instance_delete(cell, all=True)
        for (l, d) in used:
            li = client.layer_ensure(l, d)["layer_index"]
            client.call("shape.delete", {"cell": cell, "layer_index": li, "all": True})
    else:
        client.cell_create(cell)
    for (l, d) in used:
        client.layer_ensure(l, d)

    client.instance_insert_pcell_many(cell, [
        _pcell_item(devices, dv["cell"], dv["dx_um"], dv["dy_um"])
        for dv in plan["devices"]])

    w = plan["width_um"]
    shapes = []
    for key, polys in plan["paths"].items():
        l, d = (int(x) for x in key.split("/"))
        for poly in polys:
            shapes.append({"kind": "path", "layer": l, "datatype": d, "points_um": poly, "width_um": w})
    pad, cut = plan["via_pad_um"], plan["via_cut_um"]
    for cl_str, pts in plan["vias"].items():
        cl = int(cl_str)
        bridged = [a for pair, c in cut_layer.items() if c == cl for a in pair]
        land = sorted({int(p.split("/")[0]) for p in bridged})
        for (vx, vy) in pts:
            for L in land:
                shapes.append({"kind": "box", "layer": L, "datatype": 0,
                               "bbox_um": [vx - pad / 2, vy - pad / 2, vx + pad / 2, vy + pad / 2]})
            shapes.append({"kind": "box", "layer": cl, "datatype": 0,
                           "bbox_um": [vx - cut / 2, vy - cut / 2, vx + cut / 2, vy + cut / 2]})
    if shapes:                       # devices-only (no routing yet) draws nothing here
        client.shape_insert_many(cell, shapes)


# --------------------------------------------------------------------------- #
# one entry: route + draw a placed netlist (used by build_from_netlist)
# --------------------------------------------------------------------------- #
def route_and_draw(client, cell, netlist, placement, *, profile, layers, vias,
                   cut_layer, geom_path, devices, max_iters=200):
    """Build grid -> negotiated route (single pass, all nets) -> draw. Returns
    (ok, route_result, plan). No LVS here (the orchestrator gates with LVS).
    `devices` maps each device-cell key to its draw spec (params + PCell)."""
    # Lazy import: only this old negotiated path needs it, so importing
    # layout_engine (e.g. for the FlexDR demo) must not pull the negotiated
    # chain. See docs/DEMO_DEPENDENCY_MAP.md.
    from klink.routing.backends.negotiated.negotiated import route_negotiated

    device_geom, device_pads, terms = _geom_tables(load_device_geom(geom_path))
    g, net_inputs, instances = build_grid(
        netlist, placement, profile=profile, layers=layers, vias=vias,
        device_geom=device_geom, device_pads=device_pads, terms=terms)
    r = route_negotiated(g, net_inputs, width_um=profile.wire_width_um,
                         wire_clear_um=profile.wire_clear_um, via_clear_um=profile.via_clear_um,
                         max_iters=max_iters)
    if not r.ok:
        return False, r, None
    plan = to_plan(g, r, instances, profile=profile, cut_layer=cut_layer)
    ensure_pcell(client, devices)
    draw(client, cell, plan, cut_layer=cut_layer, devices=devices,
         device_layers=_device_layers(profile))
    return True, r, plan


def _parse_io_pads(io_pads, layers, netlist, power):
    """Structural validation of the io_pads contract (pure; no client, no PDN).
    Returns {"pad_layer", "block_layers", "text_size_um", "entries": [{"id",
    "net", "box"}]} with boxes normalized. Every failure is an instruction."""
    pad_layer = io_pads["pad_layer"]
    if pad_layer not in layers:
        raise ValueError(
            f"io_pads pad_layer {pad_layer!r} is not one of this run's routing "
            f"layers {list(layers)}: the router can only land wires on routing "
            "layers. Put the pad's LANDING metal on a routing layer (draw the "
            "physical bond-pad stack on top of it in your example if your "
            "process uses a dedicated pad layer)")
    block_layers = set(io_pads.get("block_layers") or layers) | {pad_layer}
    unknown_bl = block_layers - set(layers)
    if unknown_bl:
        raise ValueError(
            f"io_pads block_layers {sorted(unknown_bl)} are not routing layers "
            f"of this run ({list(layers)}); keep-outs only apply to layers the "
            "router uses")
    net_names = {n["net_id"] for n in netlist["nets"]}
    entries, seen = [], []
    for i, p in enumerate(io_pads["pads"]):
        pid = p.get("id", i)
        pnet = p.get("net")
        if pnet is not None and pnet not in net_names:
            raise ValueError(
                f"io pad {pid!r} is assigned to unknown net {pnet!r}; "
                "pad nets must be net_ids from the netlist "
                f"(power nets {sorted(power)} are allowed and feed the PDN)")
        raw = tuple(float(v) for v in p["box_um"])
        if len(raw) != 4:
            raise ValueError(f"io pad {pid!r} box_um must be [x1, y1, x2, y2]")
        box = (min(raw[0], raw[2]), min(raw[1], raw[3]),
               max(raw[0], raw[2]), max(raw[1], raw[3]))
        if box[2] - box[0] < 1e-9 or box[3] - box[1] < 1e-9:
            raise ValueError(f"io pad {pid!r} box_um {list(raw)} has zero width "
                             "or height")
        for (pj, bj) in seen:
            if (min(box[2], bj[2]) - max(box[0], bj[0]) > 1e-9
                    and min(box[3], bj[3]) - max(box[1], bj[1]) > 1e-9):
                raise ValueError(
                    f"io pads {pj!r} and {pid!r} overlap ({bj} vs {box}); "
                    "fix the pad table -- pads on a card never overlap")
        seen.append((pid, box))
        # draw=False -> a WIRE-END target, not a pad: the route simply ends
        # there and only the net label is drawn (the no-card mode: every port
        # is a bare labelled trace at the periphery, ready for the user's own
        # pad hookup)
        entries.append({"id": pid, "net": pnet, "box": box,
                        "draw": bool(p.get("draw", True))})
    return {"pad_layer": pad_layer, "block_layers": block_layers,
            "text_size_um": float(io_pads.get("text_size_um", 10.0)),
            "entries": entries}


def _pdn_with_bands(one_pdn, taps, pad_taps, bands, io_entries, *,
                    strap_layer, rail_layer, cut_layer, width_um, pitch_um):
    """REGION PDN for a pad ring that cuts across the block: split the taps at
    the forbidden y-band(s), run the ordinary sparse PDN independently per
    region, then BRIDGE consecutive regions with ONE vertical spine strap per
    net, dropped through the widest pad-free gap of the band -- the power grid
    threads between the card's pads exactly like the signal routes do. Pure
    composition: build_clean_pdn itself is unchanged."""
    from collections import defaultdict as _dd
    bands_s = sorted((tuple(map(float, b)) for b in bands), reverse=True)

    def region_of(y):
        for i, (lo, hi) in enumerate(bands_s):
            if y >= hi:
                return i
            if y > lo:                     # inside the band (a pad tap): nearest edge
                return i if (hi - y) <= (y - lo) else i + 1
        return len(bands_s)

    def band_of(y):
        for i, (lo, hi) in enumerate(bands_s):
            if lo < y < hi:
                return i
        return None

    nreg = len(bands_s) + 1
    tr = [_dd(list) for _ in range(nreg)]
    ar = [_dd(list) for _ in range(nreg)]
    in_band = []                           # power pads sitting INSIDE a band:
    for net, ts in taps.items():           # handled by a two-sided corridor
        for (x, y) in ts:                  # probe after the regional PDNs exist
            tr[region_of(y)][net].append((x, y))
    for net, ts in (pad_taps or {}).items():
        for (x, y) in ts:
            k = band_of(y)
            if k is None:
                ar[region_of(y)][net].append((x, y))
            else:
                in_band.append((net, x, y, k))
    parts = [(i, one_pdn(dict(tr[i]), dict(ar[i]))) for i in range(nreg)
             if any(tr[i].values()) or any(ar[i].values())]

    boxes = _dd(list)
    vias = []
    for _i, p in parts:
        for ly, items in p["boxes_by_layer"].items():
            boxes[ly].extend(items)
        vias.extend(p["vias"])

    def _rail(part, net):
        for it in part["boxes_by_layer"].get(rail_layer, ()):
            if it["net"] == net and it["kind"] == "tie_rail":
                return it["box"]
        return None

    w = width_um
    part_by_reg = dict(parts)

    def _corridor_clear(x, y0, y1, own_xy):
        c = (x - w, min(y0, y1), x + w, max(y0, y1))
        for e in io_entries:
            b = e["box"]
            if b[0] <= own_xy[0] <= b[2] and b[1] <= own_xy[1] <= b[3]:
                continue                   # the pad's own box is the hookup
            if min(c[2], b[2]) - max(c[0], b[0]) > 1e-9 \
                    and min(c[3], b[3]) - max(c[1], b[1]) > 1e-9:
                return False
        return True

    # a power pad INSIDE the band drops a strap straight down/up its own
    # centre to whichever region's rail has a pad-free corridor (nearer rail
    # first); the rail is widened to meet the strap column.
    for (net, x, y, k) in in_band:
        cands = []
        for reg in (k, k + 1):
            part = part_by_reg.get(reg)
            rb = _rail(part, net) if part else None
            if rb is not None:
                cands.append((abs((rb[1] + rb[3]) / 2 - y), reg, rb))
        cands.sort()
        placed = False
        for (_d, reg, rb) in cands:
            ry = (rb[1] + rb[3]) / 2
            if not _corridor_clear(x, y, ry, (x, y)):
                continue
            boxes[strap_layer].append({"net": net, "kind": "pad_spine",
                                       "box": (x - w / 2, min(y, ry) - w / 2,
                                               x + w / 2, max(y, ry) + w / 2)})
            vias.append({"net": net, "point": (x, ry), "from": rail_layer,
                         "to": strap_layer, "cut": cut_layer, "size": w})
            if not (rb[0] <= x <= rb[2]):  # widen the rail to reach the pad column
                nb = (min(rb[0], x - w / 2), rb[1], max(rb[2], x + w / 2), rb[3])
                for it in boxes[rail_layer]:
                    if it["box"] == rb and it["net"] == net:
                        it["box"] = nb
                        break
            placed = True
            break
        if not placed:
            raise ValueError(
                f"power pad of net {net!r} at ({x:.0f},{y:.0f}) sits inside the "
                "pad band and has no pad-free vertical corridor to a tie rail "
                "on either side; move that pad assignment to a corner pad or a "
                "pad outside the ring's crossing row")

    used_lanes: list = []
    for (ia, pa), (ib, pb) in zip(parts, parts[1:]):
        between = bands_s[ia:ib]           # every band separating the two regions
        for net in sorted({n for n in list(tr[ia]) + list(ar[ia])}
                          & {n for n in list(tr[ib]) + list(ar[ib])}):
            ra, rb = _rail(pa, net), _rail(pb, net)
            if ra is None or rb is None:
                continue
            ya, yb = (ra[1] + ra[3]) / 2, (rb[1] + rb[3]) / 2
            win = (max(ra[0], rb[0]) + w, min(ra[2], rb[2]) - w)
            if win[1] <= win[0]:
                raise ValueError(
                    f"PDN bridge for {net!r}: the two regions' tie rails do not "
                    f"overlap in x ({ra} vs {rb}); widen the floorplan so both "
                    "regions share rail span")
            # pad-free gaps inside the separating band(s), grown by a wire width
            blocked = sorted((e["box"][0] - w, e["box"][2] + w) for e in io_entries
                             if any(e["box"][1] < hi and e["box"][3] > lo
                                    for (lo, hi) in between))
            lanes, cur = [], win[0]
            for (bx1, bx2) in blocked + [(win[1], win[1])]:
                if bx1 > cur:
                    lanes.append((cur, min(bx1, win[1])))
                cur = max(cur, bx2)
            lanes = [(a, b) for (a, b) in lanes if b - a >= w * 2]
            lane_x = None
            for (a, b) in sorted(lanes, key=lambda g: g[1] - g[0], reverse=True):
                x = (a + b) / 2
                while any(abs(x - u) < pitch_um for u in used_lanes) and x + pitch_um < b - w:
                    x += pitch_um
                if not any(abs(x - u) < pitch_um for u in used_lanes):
                    lane_x = x
                    break
            if lane_x is None:
                raise ValueError(
                    f"PDN bridge for {net!r}: no pad-free vertical lane wide enough "
                    f"(>= {2 * w} um) crosses the band(s) {between}; use a card with "
                    "a wider pad gap, or assign this net's pad on the outside region")
            used_lanes.append(lane_x)
            boxes[strap_layer].append({"net": net, "kind": "spine",
                                       "box": (lane_x - w / 2, min(ya, yb) - w / 2,
                                               lane_x + w / 2, max(ya, yb) + w / 2)})
            for yy in (ya, yb):
                vias.append({"net": net, "point": (lane_x, yy), "from": rail_layer,
                             "to": strap_layer, "cut": cut_layer, "size": w})
    metal = {ly: [it["box"] for it in items] for ly, items in boxes.items()}
    return {"boxes_by_layer": {k: list(v) for k, v in boxes.items()},
            "vias": vias, "metal_obstacles_by_layer": metal}


def route_and_draw_flexdr(client, cell, netlist, placement, *, profile, layers, vias,
                          cut_layer, geom_path, devices,
                          power_nets=("VDD", "GND"), gcell=10, verbose=False,
                          engine=None, use_rust=False, io_pads=None,
                          pdn_split_bands=None):
    """Faithful FlexDR: PDN-separated signal route on the track grid, realize +
    draw. Same (ok, result, plan) contract as ``route_and_draw`` (LVS stays the
    orchestrator's job). This is the EXTRACTED, shared body of
    ``examples_klink/build_flexdr_lvs.py`` so the one-call build_from_netlist
    tool uses the faithful router instead of the legacy negotiated path. The
    caller passes a coarse-TRACK profile (grid_pitch = wire + clear). flexdr/pdn
    imports are function-local to keep layout_engine's import closure lean.

    ``engine`` selects the routing engine module: None = the frozen lab-fast
    greedy router (`backends/flexdr/flexdr.py`, byte-parity FROZEN); pass an
    alternative multi-layer engine module to override. Draw/realize is
    layer-generic (uses profile.vias), so the same body draws either stack.

    ``io_pads`` (optional) is the PRE-PLACED pad / probe-card contract -- the
    lab-real flow where the pad ring is fixed FIRST and the circuit must meet
    it (possibly using only a few of the card's pads):

        {"pad_layer": "106/0",          # pad metal + terminal layer
         "block_layers": None,          # keep-out layers under pads; None = ALL
         "text_size_um": 10.0,
         "pads": [{"id": "P01", "box_um": [x1, y1, x2, y2],
                   "net": "Y[0]"        # omit/None = unused pad (hard keep-out)
                  }, ...]}

    Assigned pads become the net's OWN fixed metal + an extra route terminal
    (the whole pad face is the target); unused pads are hard keep-outs for
    every net on ``block_layers``. Pad boxes widen the grid bbox, so the ring
    may sit far outside the device block (devices outside the ring work the
    same way -- routes pass between pads). Power-net pads (VDD/GND) are NOT
    routed by this signal engine; tie them to the PDN rail/strap externally."""
    _track1 = engine is None   # Track 1 (frozen flexdr) is the only engine that
    if engine is None:         # takes use_rust; Track 2 engines own their toggle.
        from klink.routing.backends.flexdr import flexdr as engine
    route_flexdr = engine.route_flexdr
    flexgc_lite = engine.flexgc_lite
    flexpa_access_nets = engine.flexpa_access_nets
    _prl_params = engine._prl_params
    from klink.routing.grid.clean_pdn import build_clean_pdn, derive_pdn_layers
    from klink.routing.grid.cell_realize import realize_cell_boxes
    from klink.routing.grid.pathfinder import _halos

    import time as _time
    import os as _os
    _tm = _time.time()
    _stages = _os.environ.get("TG_STAGE_TIME") == "1"

    def _mark(label):
        nonlocal _tm
        if _stages:
            print(f"  STAGE {label}: {_time.time() - _tm:.1f}s", flush=True)
        _tm = _time.time()

    power = set(power_nets)
    device_geom, device_pads, terms = _geom_tables(load_device_geom(geom_path))
    signal = {n["net_id"] for n in netlist["nets"] if n["net_id"] not in power}

    # io_pads: parse + geometry-validate FIRST (validate-before-mutate) so a
    # power pad can feed the PDN as an extra tap below.
    io = _parse_io_pads(io_pads, layers, netlist, power) if io_pads else None

    # pass 1: all nets -> power taps for the PDN
    g0, ni_all, instances = build_grid(
        netlist, placement, profile=profile, layers=layers, vias=vias,
        device_geom=device_geom, device_pads=device_pads, terms=terms)
    taps = {pn: [(x, y) for net in ni_all if net.net == pn for x, y, _l in net.access]
            for pn in power}
    rail_l, strap_l, cut_l = derive_pdn_layers(profile)
    pad_taps = {}
    if io:
        # POWER pads are PDN business, not the signal router's: each one becomes
        # an ATTACH-ONLY tap, so build_clean_pdn plants a strap on the pad
        # reaching the peripheral tie rail (via + keep-out registration come for
        # free) WITHOUT dragging the rail out past the pad ring. This only works
        # if the pad's landing metal is on the strap or rail layer.
        for e in io["entries"]:
            if e["net"] in power:
                if io["pad_layer"] not in (rail_l, strap_l):
                    raise ValueError(
                        f"io pad {e['id']!r} carries power net {e['net']!r} but "
                        f"pad_layer {io['pad_layer']!r} is neither the PDN rail "
                        f"({rail_l!r}) nor strap ({strap_l!r}) layer; put the "
                        "power pads' landing metal on one of those layers, or "
                        "tie power externally")
                x1, y1, x2, y2 = e["box"]
                pad_taps.setdefault(e["net"], []).append(((x1 + x2) / 2, (y1 + y2) / 2))
    def _one_pdn(t, at):
        return build_clean_pdn(t, strap_layer=strap_l, rail_layer=rail_l, cut_layer=cut_l,
                               width_um=profile.wire_width_um, spacing_um=profile.wire_clear_um,
                               margin_um=profile.margin_um, strap_gap_um=15.0,
                               attach_taps_by_net=at)
    if pdn_split_bands:
        pdn = _pdn_with_bands(_one_pdn, taps, pad_taps, pdn_split_bands,
                              io["entries"] if io else (),
                              strap_layer=strap_l, rail_layer=rail_l, cut_layer=cut_l,
                              width_um=profile.wire_width_um,
                              pitch_um=profile.wire_width_um
                              + max(profile.wire_clear_um, 15.0))
    else:
        pdn = _one_pdn(taps, pad_taps)
    extra = {layer: [(it["net"], it["box"]) for it in items]
             for layer, items in pdn["boxes_by_layer"].items()}
    _mark("pass1_grid+pdn")

    # io_pads: pre-placed pads -> own-net fixed metal + extra terminals + bbox;
    # unused pads -> unique-owner hard keep-outs (foreign to every net); power
    # pads already fed the PDN as taps above. A pad may legitimately overlap
    # its OWN net's PDN metal (that IS the hookup), never foreign metal.
    pad_bbox_inc, pad_terms = (), None
    if io:
        _chans, _bods = device_keepouts(instances, device_geom)
        _pdn_metal = [(ly, tuple(it["box"]), it["kind"], it["net"])
                      for ly, items in pdn["boxes_by_layer"].items() for it in items]

        def _hit(a, b):
            return (min(a[2], b[2]) - max(a[0], b[0]) > 1e-9
                    and min(a[3], b[3]) - max(a[1], b[1]) > 1e-9)

        pad_terms = {}
        for e in io["entries"]:
            pid, pnet, box = e["id"], e["net"], e["box"]
            for ob in _bods + list(_chans):
                if _hit(box, tuple(ob)):
                    raise ValueError(
                        f"io pad {pid!r} at {box} overlaps a device body/channel at "
                        f"{tuple(ob)}; move the pad ring or shift the placement "
                        "(e.g. place_grid forbid_y_bands) so they are disjoint")
            for (ly, ob, kind, onet) in _pdn_metal:
                if onet != pnet and ly in io["block_layers"] and _hit(box, ob):
                    raise ValueError(
                        f"io pad {pid!r} at {box} overlaps PDN {kind} of net "
                        f"{onet!r} at {ob} on {ly}; move the pad clear of the "
                        "foreign power metal (straps run inside the device "
                        "block's x-range, tie rails run margin_um outside it)")
            owner = pnet if pnet is not None else f"__pad_{pid}__"
            for L in io["block_layers"]:
                extra.setdefault(L, []).append((owner, box))
            if pnet is not None and pnet not in power:
                pad_terms.setdefault(pnet, []).append(box + (io["pad_layer"],))
        pad_bbox_inc = tuple(e["box"] for e in io["entries"])

    # pass 2: signal-only grid with PDN keep-outs -> FlexPA access -> FlexDR
    g, _ni, _ = build_grid(netlist, placement, profile=profile, layers=layers, vias=vias,
                           device_geom=device_geom, device_pads=device_pads, terms=terms,
                           route_only=signal, extra_pads_by_layer=extra,
                           bbox_include_um=pad_bbox_inc)
    _mark("pass2_grid")
    ni = flexpa_access_nets(g, netlist, placement, device_pads, terms, route_only=signal,
                            wire_width_um=profile.wire_width_um,
                            extra_terminals=pad_terms)
    _mark("flexpa")
    _rf_rust = {"use_rust": use_rust} if _track1 else {}
    r = route_flexdr(g, ni, profile, gcell, width_um=profile.wire_width_um,
                     wire_clear_um=profile.wire_clear_um, via_clear_um=profile.via_clear_um,
                     verbose=verbose, **_rf_rust)
    _mark("route_flexdr")
    wh, vh = _halos(g, profile.wire_width_um, profile.wire_clear_um, profile.via_clear_um)
    ph, pl, dbl = _prl_params(g, profile, profile.wire_width_um)
    markers = flexgc_lite(g, r.routes, r.edges, wh, vh, prl_halo=ph, prl_len=pl, dir_by_li=dbl)
    _mark("flexgc_final")
    if not r.ok or markers:
        # Surface WHAT failed and HOW to fix it (errors are instructions): the
        # net the greedy router could not route (route_flexdr knows it) + the
        # remedy, so a user who is NOT the author can act without reading code.
        unroutable = [p for p in (r.problems or ()) if p.get("type") == "unroutable"]
        problems = [{"type": "flexdr_not_clean",
                     "message": (f"FlexDR routed {len(r.routes)}/{len(ni)} signal nets "
                                 f"with {len(markers)} DRC markers; not clean.")}]
        next_action = None
        if unroutable:
            net = unroutable[0].get("net")
            n_terms = len(next((n["terminals"] for n in netlist["nets"]
                                if n["net_id"] == net), ()))
            problems.append({
                "type": "unroutable", "net": net, "terminals": n_terms,
                "message": (f"net {net!r} ({n_terms} terminals) could not be routed: "
                            "a high-fanout / wide-span net the single-layer greedy "
                            "router cannot complete in this floorplan.")})
            next_action = (
                "give routing more room, then re-run: raise the profile's "
                "col_pitch_um / y_step_um (looser placement) or lower wire_clear_um; "
                "if it persists the net needs more routing layers (Track 2).")
        elif markers:
            next_action = ("the route has DRC violations; loosen spacing "
                           "(raise wire_clear_um / prl_spacing_um) or the floorplan, "
                           "then re-run.")
        return False, {"ok": False, "routed": len(r.routes), "nets": len(ni),
                       "markers": len(markers), "problems": problems,
                       "next_action": next_action}, None

    # realize signal routes + PDN boxes + via stacks (mirrors build_flexdr_lvs)
    def um(c):
        return (round(g.cx(c[0]) / 1000.0, 3), round(g.cy(c[1]) / 1000.0, 3), g.layers[c[2]])
    cutm = {tuple(sorted((lo, up))): int(cut.split("/")[0]) for lo, cut, up in profile.vias}
    sig = {"routes": {n: [um(c) for c in cells] for n, cells in r.routes.items()},
           "edges": {n: [(um(a), um(b)) for a, b in e] for n, e in r.edges.items()},
           "wire_um": profile.wire_width_um, "via_pad_um": profile.via_pad_um,
           "via_cut_um": round(profile.via_pad_um - 2 * profile.litho_tol_um, 3),
           "cut_map": {f"{a}|{b}": c for (a, b), c in cutm.items()}}
    shapes, used, n_sigvia = realize_cell_boxes(sig)
    for layer, items in pdn["boxes_by_layer"].items():
        l, d = (int(x) for x in layer.split("/"))
        used.add((l, d))
        for it in items:
            x1, y1, x2, y2 = it["box"]
            shapes.append({"kind": "box", "layer": l, "datatype": d, "bbox_um": [x1, y1, x2, y2]})
    cutw = sig["via_cut_um"]
    pad = profile.via_pad_um
    for v in pdn["vias"]:
        vx, vy = v["point"]
        frm = int(v["from"].split("/")[0]); to = int(v["to"].split("/")[0]); cl = int(v["cut"].split("/")[0])
        used.add((cl, 0))
        for L in (frm, to):
            used.add((L, 0))
            shapes.append({"kind": "box", "layer": L, "datatype": 0,
                           "bbox_um": [vx - pad / 2, vy - pad / 2, vx + pad / 2, vy + pad / 2]})
        shapes.append({"kind": "box", "layer": cl, "datatype": 0,
                       "bbox_um": [vx - cutw / 2, vy - cutw / 2, vx + cutw / 2, vy + cutw / 2]})

    if io:
        pl, pdt = (int(x) for x in io["pad_layer"].split("/"))
        used.add((pl, pdt))
        tsz = io["text_size_um"]
        for i, e in enumerate(io["entries"]):
            x1, y1, x2, y2 = e["box"]
            if e["draw"]:
                shapes.append({"kind": "box", "layer": pl, "datatype": pdt,
                               "bbox_um": [x1, y1, x2, y2]})
            label = e["net"] or e["id"] or f"NC{i}"
            shapes.append({"kind": "text", "layer": pl, "datatype": pdt,
                           "text": str(label), "position_um": [x1, y2 + 2.0],
                           "size_um": tsz})
    # label the PDN tie rails with their net name -- power must be findable on
    # the layout even with no pads at all (peripheral rails ARE the power ports)
    for layer, items in pdn["boxes_by_layer"].items():
        l, d = (int(x) for x in layer.split("/"))
        for it in items:
            if it.get("kind") == "tie_rail":
                x1, y1, x2, y2 = it["box"]
                shapes.append({"kind": "text", "layer": l, "datatype": d,
                               "text": str(it["net"]),
                               "position_um": [x1, y2 + 2.0],
                               "size_um": max(2.0 * profile.wire_width_um, 4.0)})

    # draw fresh/rebuilt cell: PCell devices + realized shapes (no LVS here).
    # device-internal layers (gate/sd/channel) come from the PROFILE; routing/
    # PDN/via layers are already in `used` from realize. No hardcoded layers.
    used |= {tuple(int(x) for x in l.split("/")) for l in _device_layers(profile)}
    ensure_pcell(client, devices)
    existing = {x["name"] for x in client.cell_list()["cells"]}
    # Redraw must start from a TRULY EMPTY cell. The old per-layer
    # shape.delete clear was silently capped by shape.delete's `limit`
    # (default 10_000): on a cell with >10k shapes on one layer (e.g. alu4
    # has ~16k on 101/0) it deleted only 10k and left the rest -> stale
    # geometry from the PREVIOUS build survived, overlaying the new route
    # and bridging nets (LVS short with 0 DRC markers; add4 stayed clean
    # only because every layer was <10k). Delete the whole cell
    # (non-recursive, so the shared device PCell library cells survive) and
    # recreate it -> no stale shapes regardless of size, scales to any gate
    # count.
    if cell in existing:
        client.cell_delete(cell)
    client.cell_create(cell)
    for (l, d) in used:
        client.layer_ensure(l, d)
    client.instance_insert_pcell_many(cell, [
        _pcell_item(devices, i["device_cell"], i["transform"]["dx_um"],
                    i["transform"]["dy_um"])
        for i in instances])
    # shape.insert_many caps at 100k items per call; a large design (e.g. cpu4 ~
    # 120k shapes) must be chunked or the RPC rejects it (ERR_BAD_PARAMS "too
    # large"). Chunk well under the cap so it scales to any gate count.
    _mark("realize+pcells")
    _CHUNK = 50_000
    for i in range(0, len(shapes), _CHUNK):
        client.shape_insert_many(cell, shapes[i:i + _CHUNK])
    _mark("draw_shapes")
    return True, {"ok": True, "routed": len(r.routes), "nets": len(ni), "markers": 0,
                  "sig_vias": n_sigvia, "pdn_vias": len(pdn["vias"]), "problems": []}, None

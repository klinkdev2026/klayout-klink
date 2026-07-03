"""Faithful cell-box realize + draw + live LVS, from a saved route pickle.

The grid router proves connectivity at the CELL level. To make the DRAWN
geometry's connectivity identical to the grid's, draw every routed cell as a
wire_width box on its layer (same-net adjacent cells abut -> connected) and a
cut box at every cross-layer (via) edge. Because the router kept different nets
>= 2 cells apart (_clear), these boxes never abut across nets -> no shorts.
This removes the merge_polylines gap that dropped high-fanout connectivity.

Iterate draw without re-routing:  python -m klink.routing.grid.cell_realize test_outputs/route_add4.pkl
"""
from __future__ import annotations

import pickle

from klink import KLinkClient
from klink.routing.grid.process_profile import ProcessProfile
from klink.domains.structdevice import layout_engine as eng
from klink.domains.structdevice.orchestrators import lvs_check


def realize_cell_boxes(d, *, overlap=0.0):
    """Pure: turn a saved route ({routes, edges, wire_um, via_*, cut_map}) into
    the EXACT drawable boxes whose connectivity == the grid's. Every routed cell
    -> a wire_width box on its layer; every cross-layer (via) edge -> a cut box.
    Returns (shapes, used_layers, n_vias). No KLayout dependency.

    `overlap` grows boxes so adjacent same-net cells overlap rather than merely
    abut (guards against sub-nm gaps); different nets stay >=2 cells apart via
    the router's `_clear`, so they never touch. See LESSONS #85.
    """
    w = d["wire_um"] + 2 * overlap
    cut = d["via_cut_um"]
    cut_map = {tuple(k.split("|")): v for k, v in d["cut_map"].items()}
    shapes = []
    used = set()
    seen = set()
    for net, cells in d["routes"].items():
        for (x, y, layer) in cells:
            key = (round(x, 3), round(y, 3), layer)
            if key in seen:
                continue
            seen.add(key)
            l, dt = (int(v) for v in layer.split("/"))
            used.add((l, dt))
            shapes.append({"kind": "box", "layer": l, "datatype": dt,
                           "bbox_um": [x - w/2, y - w/2, x + w/2, y + w/2]})
    # Planar edges -> a CONTINUOUS wire box spanning the two cells (not just a
    # per-cell box). Per-cell boxes only abut when the grid pitch <= wire width
    # (fine grid); on a coarser/track grid (pitch = wire + clear) per-cell boxes
    # leave a `clear` gap -> disconnected metal. The span box bridges it. Width
    # stays `w` perpendicular, so a neighbour track (>= pitch away) never shorts.
    for net, elist in d["edges"].items():
        for a, b in elist:
            if a[2] == b[2]:
                l, dt = (int(v) for v in a[2].split("/"))
                used.add((l, dt))
                shapes.append({"kind": "box", "layer": l, "datatype": dt,
                               "bbox_um": [min(a[0], b[0]) - w/2, min(a[1], b[1]) - w/2,
                                           max(a[0], b[0]) + w/2, max(a[1], b[1]) + w/2]})
    vias = set()
    for net, elist in d["edges"].items():
        for a, b in elist:
            if a[2] != b[2] and (a[0], a[1]) == (b[0], b[1]):
                c = cut_map.get((a[2], b[2])) or cut_map.get((b[2], a[2]))
                if c is not None:
                    vias.add((round(a[0], 3), round(a[1], 3), int(c)))
    for (x, y, cl) in vias:
        used.add((cl, 0))
        shapes.append({"kind": "box", "layer": cl, "datatype": 0,
                       "bbox_um": [x - cut/2, y - cut/2, x + cut/2, y + cut/2]})
    return shapes, used, len(vias)


def draw_and_lvs(pkl_path, P: ProcessProfile, *, devices, terminal_provider,
                 placement, port: int, cell=None, overlap=0.0):
    d = pickle.load(open(pkl_path, "rb"))
    cell = cell or ("LAB_" + pkl_path.split("route_")[-1].split(".")[0].upper())
    shapes, used_box, n_vias = realize_cell_boxes(d, overlap=overlap)
    # device-internal layers (gate/sd/channel) come from the PROFILE, never
    # hardcoded; routing/via layers are already in used_box from the realize.
    used = set(used_box) | {tuple(int(x) for x in l.split("/"))
                            for l in eng._device_layers(P)}
    print(f"[{cell}] cell-box shapes={len(shapes)} (wire boxes + {n_vias} vias)", flush=True)
    with KLinkClient(port=port).connect() as c:
        eng.ensure_pcell(c, devices)
        existing = {x["name"] for x in c.cell_list()["cells"]}
        # Full clear by delete+recreate: the old per-layer shape.delete clear
        # was capped by shape.delete's `limit` (default 10_000) and silently
        # truncated on cells with >10k shapes on a layer, leaving stale
        # geometry from the previous build (LVS short, 0 DRC markers).
        # Non-recursive delete keeps the shared device PCell library cells.
        if cell in existing:
            c.cell_delete(cell)
        c.cell_create(cell)
        for (l, dt) in used:
            c.layer_ensure(l, dt)
        c.instance_insert_pcell_many(cell, [
            eng._pcell_item(devices, i["cell"], i["dx"], i["dy"])
            for i in d["instances"]])
        c.shape_insert_many(cell, shapes)
        # lvs_check aligns declared<->layout devices BY POSITION from device_terms
        # (scale-robust; LESSONS #85). No manual remap here.
        res = lvs_check(c, cell, declared=d["declared"], mode="lvsdb",
                        connectivity=P.connectivity_spec(),
                        terminal_provider=terminal_provider, placement=placement,
                        device_terms=d.get("device_terms"))
        dev = res.get("device_lvs", {})
        print(f"[{cell}] LVS ok={res['ok']} match={dev.get('match')} devices={dev.get('device_count')}", flush=True)
        for p in res.get("problems", [])[:10]:
            print("   problem:", p, flush=True)
        return bool(res["ok"])


if __name__ == "__main__":
    raise SystemExit(
        "cell_realize.draw_and_lvs needs an explicit process profile and port; "
        "klink ships no lab default. Run it from an example (e.g. "
        "examples_klink/build_bounded.py) that imports a profile from "
        "your pdk.py and passes P=..., port=... .")

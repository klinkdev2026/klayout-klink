"""Build a circuit cell from a device-level netlist, fully automatic.

Structure-as-Device M6-lite (the first forward generator with zero
hand-written coordinates): naive column placement + the connect_nets
orchestrator.  The netlist format is the pinned extraction format
(instances + nets); placement is deliberately simple — devices in a
column at a fixed pitch — because the routing side already handles
attach points, vias, and keepouts automatically.  A real placement
engine is the M6 line (OpenROAD gpl objective framing noted as input).

Per docs/AGENT_TOOL_DESIGN.md: one call, state on disk, errors are
instructions, validate-before-mutate, next_action in every result.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import hashlib
import json
import os
from collections import Counter
from dataclasses import replace

from klink.domains.structdevice.connectivity import ConnectivitySpec
from klink.domains.structdevice.lvs_lite import declared_nets_from_dicts
from klink.domains.structdevice.orchestrators import (
    DEFAULT_SPEC_ROOT,
    connect_nets,
    lvs_check,
    save_declared_nets,
    save_device_terms,
)
from klink.routing.grid.process_profile import ProcessProfile
from klink.routing.grid.floorplan import derive_grid, derive_row_pitch
from klink.domains.structdevice import layout_engine as eng
from klink.domains.structdevice.recipes import geom_terminal_provider


class NetlistBuildError(ValueError):
    """Bad netlist input.  Messages instruct."""


def validate_device_netlist(netlist: Mapping[str, Any]) -> List[str]:
    """Structural validation of the pinned netlist format."""
    problems: List[str] = []
    instances = netlist.get("instances")
    if not instances:
        problems.append("netlist.instances is empty; nothing to build.")
        return problems
    seen = set()
    for i, inst in enumerate(instances):
        iid = inst.get("instance_id")
        if not iid:
            problems.append(f"instances[{i}]: missing instance_id")
            continue
        if iid in seen:
            problems.append(f"instances[{i}]: duplicate instance_id {iid!r}")
        seen.add(iid)
        if not inst.get("device_cell"):
            problems.append(f"instances[{i}] ({iid}): missing device_cell")
    for j, net in enumerate(netlist.get("nets", [])):
        for ref in net.get("terminals", []):
            inst_id = str(ref).split(".", 1)[0]
            if inst_id not in seen:
                problems.append(
                    f"nets[{j}] ({net.get('net_id')}): terminal {ref!r} "
                    "references an unknown instance")
    try:
        declared_nets_from_dicts([
            {"net": n.get("net_id"), "terminals": list(n.get("terminals", []))}
            for n in netlist.get("nets", [])])
    except Exception as exc:
        problems.append(str(exc))
    return problems


def plan_grouped_placement(
    instances: Sequence[Mapping[str, Any]],
    groups: Sequence[Mapping[str, Any]],
    *,
    origin_um: Tuple[float, float] = (0.0, 0.0),
    pitch_um: float = 40.0,
    group_pitch_um: float = 150.0,
) -> List[Dict[str, Any]]:
    """Module placement (user ruling): each gate group is a
    vertical sub-column; groups spread horizontally with a routing
    channel between columns.  Output order follows the NETLIST instance
    order so X1..Xn ids line up with instance.query insertion order."""
    if pitch_um <= 0 or group_pitch_um <= 0:
        raise NetlistBuildError("pitch_um and group_pitch_um must be positive")
    position: Dict[str, Tuple[float, float]] = {}
    grouped = set()
    col = 0
    for g in groups:
        for row, iid in enumerate(g.get("instances", [])):
            if iid in position:
                raise NetlistBuildError(
                    f"instance {iid!r} appears in more than one group")
            position[iid] = (origin_um[0] + col * group_pitch_um,
                             origin_um[1] + row * pitch_um)
            grouped.add(iid)
        col += 1
    leftover = [i["instance_id"] for i in instances
                if i["instance_id"] not in grouped]
    for row, iid in enumerate(leftover):
        position[iid] = (origin_um[0] + col * group_pitch_um,
                         origin_um[1] + row * pitch_um)
    return [
        {"child": inst["device_cell"],
         "position_um": [position[inst["instance_id"]][0],
                         position[inst["instance_id"]][1]]}
        for inst in instances
    ]


def plan_column_placement(
    instances: Sequence[Mapping[str, Any]],
    *,
    origin_um: Tuple[float, float] = (0.0, 0.0),
    pitch_um: float = 30.0,
) -> List[Dict[str, Any]]:
    """Deterministic naive placement: instances in input order, bottom
    to top, fixed vertical pitch.  No intelligence by design (M6 is the
    real placer); the router's automatic keepouts absorb the rest."""
    if pitch_um <= 0:
        raise NetlistBuildError("pitch_um must be positive")
    return [
        {"child": inst["device_cell"],
         "position_um": [float(origin_um[0]),
                         float(origin_um[1]) + i * float(pitch_um)]}
        for i, inst in enumerate(instances)
    ]


def build_from_netlist(
    client: Any,
    cell: str,
    netlist: Mapping[str, Any],
    *,
    spec_root: str = DEFAULT_SPEC_ROOT,
    profile: Optional[ProcessProfile] = None,
    mode: str = "3L",
    rows: int = 0,
    cols: int = 0,
    confirm: Optional[str] = None,
    devices: Optional[Mapping[str, Any]] = None,
    geom_path: Optional[str] = None,
) -> Dict[str, Any]:
    """One INTENTION, two calls: device netlist in -> DERIVED floorplan ->
    single-pass multilayer route -> draw -> device-LVS-verified cell out.

    Nothing is hand-tuned: layer roles/vias/spacing come from `profile`, the
    grid + row pitch are derived from the netlist's crossing demand. The FIRST
    call (no `confirm`) returns a `proposal` (grid, row pitch, layers, device
    mix) + a `confirm` token + `next_action`; relay the proposal to the user,
    then call again with that token to build. Only a FRESH cell is created (no
    scar); state is persisted to <spec_root>/<cell>.build.json. The placement/
    routing/draw all live in layout_engine -- no agent ever supplies a
    coordinate, a layer, or a pitch."""

    if profile is None:
        return {"ok": False, "cell": cell, "problems": [
            "no process profile supplied; klink ships none -- the lab process is "
            "example-owned, not built into this tool."],
            "next_action": "this build path needs a process profile. Write/run an "
            "example like examples_klink/build_circuit.py that imports a profile "
            "from your pdk.py and passes it in, rather than calling "
            "structdevice.build_from_netlist with no profile."}
    if geom_path is None:
        return {"ok": False, "cell": cell, "problems": [
            "no device-geometry file given; klink ships no path. Harvested device "
            "geometry (channel/pads/terminals) is example/lab data."],
            "next_action": "pass geom_path -- examples use DEVICE_GEOM_FILE from "
            "your pdk.py; harvest it first with "
            "examples_klink/harvest_device_geometry.py."}
    if devices is None:
        return {"ok": False, "cell": cell, "problems": [
            "no device library given; klink ships none. The device DRAW spec "
            "(per device key: params + fitted PCell + style + fit_table) is "
            "process/example data."],
            "next_action": "pass devices -- examples use DEVICES from "
            "your pdk.py; each entry maps a device-cell key to "
            "{params, pcell, library, style, fit_table}."}

    problems = validate_device_netlist(netlist)
    if problems:
        return {"ok": False, "cell": cell, "problems": problems,
                "next_action": "fix the netlist (the problems above say how) and "
                               "call structdevice.build_from_netlist again"}
    if mode not in ("2L", "3L"):
        return {"ok": False, "cell": cell, "problems": [f"mode {mode!r} must be '2L' or '3L'"],
                "next_action": "call again with mode='3L' (3 routing layers) or '2L'"}
    instances = list(netlist["instances"])
    unknown = sorted({str(i["device_cell"]) for i in instances} - set(devices))
    if unknown:
        return {"ok": False, "cell": cell, "problems": [
            f"netlist uses device_cell(s) {unknown} not in the device library "
            f"(have: {sorted(devices)})."],
            "next_action": "add those device keys to the devices library (params "
                           "+ PCell + style + fit_table), or fix the netlist, and "
                           "call again"}
    groups = list(netlist.get("groups") or [])
    if not groups:
        return {"ok": False, "cell": cell, "problems": [
            "netlist has no gate 'groups'; the floorplan needs gate grouping "
            "(map_logic_to_devices produces it)."],
            "next_action": "supply a netlist with 'groups' and call again"}

    # ---- DERIVE the floorplan (no magic numbers) -----------------------------
    routing_layers = list(profile.routing_layers)
    route_layers = routing_layers[:2] if mode == "2L" else routing_layers
    route_vias = profile.via_rules()[:1] if mode == "2L" else profile.via_rules()
    cut_layer = {tuple(sorted((lo, up))): profile.cut_layer(lo, up)
                 for (lo, _cut, up) in profile.vias}
    raw_geom = eng.load_device_geom(geom_path)
    _, _, terms = eng._geom_tables(raw_geom)
    missing = sorted({i["device_cell"] for i in instances} - set(terms))
    if missing:
        return {"ok": False, "cell": cell, "problems": [
            f"no harvested geometry for device cells {missing}; harvest them "
            f"into {geom_path} first (the recipe reads channel/pads/terminals)."],
            "next_action": "harvest the device geometry and call again"}
    if not rows or not cols:
        rows, cols = derive_grid(len(groups))
    rp = derive_row_pitch(netlist, rows, cols, terms, y_step=profile.y_step_um,
                          width_um=profile.wire_width_um, wire_clear_um=profile.wire_clear_um,
                          via_pad_um=profile.via_pad_um, n_horiz_layers=len(route_layers))
    proposal = {
        "cell": cell, "gates": len(groups), "devices": len(instances),
        "device_mix": dict(Counter(i["device_cell"] for i in instances)),
        "grid_rows_cols": [rows, cols], "row_pitch_um": rp,
        "routing_layers": route_layers, "mode": mode,
    }
    token = hashlib.sha1(json.dumps(proposal, sort_keys=True).encode()).hexdigest()[:8]
    os.makedirs(spec_root, exist_ok=True)
    with open(os.path.join(spec_root, f"{cell}.build.json"), "w") as fh:
        json.dump({"proposal": proposal, "token": token}, fh, indent=1)

    # ---- confirmation gate (the user approves the proposal) ------------------
    if confirm != token:
        return {
            "ok": None, "needs_confirmation": True, "cell": cell, "proposal": proposal,
            "next_action": (
                f"READ this proposal to the user: {proposal['gates']} gates on a "
                f"{rows}x{cols} grid, row pitch {rp}um, routing layers "
                f"{route_layers} ({mode}). If they approve, call "
                f"structdevice.build_from_netlist again with the SAME arguments "
                f"plus confirm='{token}'. Do NOT place/route/draw yourself; do "
                f"NOT change rows/cols/mode unless the user asks."),
        }

    # ---- confirmed: fresh cell + place + route + draw + LVS -------------------
    existing = {c["name"] for c in client.cell_list()["cells"]}
    if cell in existing:
        return {"ok": False, "cell": cell, "problems": [
            f"cell {cell!r} already exists; build_from_netlist only creates fresh cells."],
            "next_action": "pick a new cell name (or delete the old cell after "
                           "confirming with the user) and call again with confirm"}

    placement = eng.place_grid(netlist, rows, cols, profile=profile, row_pitch=rp)
    save_declared_nets(spec_root, cell,
                       [{"net": n["net_id"], "terminals": sorted(n["terminals"])}
                        for n in netlist["nets"]])
    # persist declared device terminal positions so lvs_check aligns
    # declared<->layout devices BY POSITION (scale-robust; LESSONS #85)
    device_terms = {xi: {t: [round(dx + terms[dc][t]["center"][0], 3),
                             round(dy + terms[dc][t]["center"][1], 3)]
                         for t in terms[dc]}
                    for xi, (dc, dx, dy) in placement.items()}
    save_device_terms(spec_root, cell, device_terms)
    # Faithful FlexDR on the coarse TRACK grid (grid_pitch = wire + clear),
    # PDN-separated. This is the router build_flexdr_lvs.py proved LVS-clean;
    # the legacy negotiated path (eng.route_and_draw) stays as a library fn.
    track_profile = replace(profile, grid_pitch_um=profile.wire_width_um + profile.wire_clear_um)
    import os as _os
    _use_rust = _os.environ.get("FLEXDR_RUST", "1") != "0"   # default on; =0 forces pure Python
    ok, r, _plan = eng.route_and_draw_flexdr(
        client, cell, netlist, placement, profile=track_profile, layers=route_layers,
        vias=route_vias, cut_layer=cut_layer, geom_path=geom_path, devices=devices,
        use_rust=_use_rust)
    if not ok:
        prob = (r.get("problems") or [{"type": "no_converge"}])[0]
        return {"ok": False, "cell": cell, "problems": [prob],
                "next_action": ("FlexDR did not produce a clean route. Relay this to the "
                                "user: try mode='3L' (more routing layers) if you used '2L', "
                                "or the netlist is too dense for the derived floorplan.")}
    declared = [{"net": n["net_id"], "terminals": n["terminals"]} for n in netlist["nets"]]
    res = lvs_check(client, cell, declared=declared, mode="lvsdb",
                    connectivity=profile.connectivity_spec(), spec_root=spec_root,
                    terminal_provider=geom_terminal_provider(raw_geom),
                    placement=placement, device_terms=device_terms)
    dev = res.get("device_lvs", {})
    ok_lvs = bool(res["ok"])
    return {
        "ok": ok_lvs, "cell": cell, "device_match": dev.get("match"),
        "devices": dev.get("device_count"), "lvsdb_path": dev.get("lvsdb_path"),
        "grid_rows_cols": [rows, cols], "row_pitch_um": rp,
        "routing_layers": route_layers,
        "routed_nets": r.get("routed"), "pdn_vias": r.get("pdn_vias"),
        "problems": res.get("problems", [])[:8],
        "next_action": ("done -- the interactive LVS browser is open for layout<->netlist "
                        "cross-probe; the cell is LVS-clean."
                        if ok_lvs else
                        "LVS mismatch -- relay the problems above to the user VERBATIM and "
                        "reconcile the differing nets; do not guess a fix."),
    }

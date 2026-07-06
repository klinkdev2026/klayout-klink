"""One-call orchestrators for the Structure-as-Device chain.

Per docs/AGENT_TOOL_DESIGN.md: one user intention = one call, intent
state persists on disk (``.klink/specs/``), errors are instructions,
validation precedes mutation (these orchestrators never mutate the
layout at all — they only write fact files).

Live connectivity uses the blessed worker pattern: the layout is saved
through the existing ``layout.save_file`` RPC and extraction runs
offline on the file (klayout.db in this interpreter) — no plugin
changes, no custom tracing.

SEND gesture for electrical nets (reusing the proven photonics gesture
machinery): ONE SEND framing two or more device terminals = ONE
declared net.  Terminals are recipe-derived, so the user frames plain
geometry — no markers required.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from klink.domains.structdevice.connectivity import (
    ConnectivityExtractor,
    ConnectivitySpec,
    PlacedTerminal,
)
from klink.domains.structdevice.lvs_lite import (
    align_declared_by_position,
    declared_nets_from_dicts,
    reconcile,
)
from klink.domains.structdevice.recipes import RecipeError

DEFAULT_SPEC_ROOT = ".klink/specs"

# klink's RESERVED scratch keepout layer. connect_nets draws transient
# "everything-not-this-net" obstacle boxes here for the damped router, then
# deletes them. This is klink INFRASTRUCTURE (like the 999/99 Port / 999/1
# Anchor marker layers), NOT process data: the generic routing backends no
# longer default obstacle_layers to it (there it means the USER's design
# obstacle layers), so connect_nets passes it EXPLICITLY and never relies on a
# router default.
KLINK_KEEPOUT_LAYER = "900/0"
_KEEPOUT_LAYER_NUM, _KEEPOUT_DATATYPE = (int(x) for x in KLINK_KEEPOUT_LAYER.split("/"))


def _nets_path(spec_root: str, cell: str) -> Path:
    return Path(spec_root) / f"{cell}.elec_nets.json"


def load_declared_nets(spec_root: str, cell: str) -> List[Dict[str, Any]]:
    path = _nets_path(spec_root, cell)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["nets"]


def save_declared_nets(
    spec_root: str, cell: str, nets: Sequence[Mapping[str, Any]]
) -> str:
    declared_nets_from_dicts(nets)  # validate before persisting
    path = _nets_path(spec_root, cell)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"cell": cell, "nets": [dict(n) for n in nets]},
                   indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return str(path)


def collect_placed_terminals(
    client: Any,
    cell: str,
    *,
    terminal_provider: Any,
    device_cells: Any,
) -> Dict[str, Any]:
    """Derive terminals for every device-cell child instance of ``cell`` and
    place them in top coordinates.  Read-only.

    ``device_cells`` is the set of device-cell KEYS to treat as devices (no
    name-prefix guessing); ``terminal_provider(client, device_cell) -> {name:
    DerivedTerminal}`` supplies each device's terminals -- the recipe-free
    harvested-geometry provider (build path) or an injected device recipe
    (interactive path). klink ships neither: both are example/process data."""

    device_cells = set(device_cells)
    result = client.instance_query(cell)
    dbu = float(client.layout_info()["dbu"])
    placed: List[PlacedTerminal] = []
    instances: List[Dict[str, Any]] = []
    terminals_by_cell: Dict[str, Any] = {}
    problems: List[str] = []
    n = 0
    for inst in result.get("instances", []):
        child = str(inst.get("child") or "")
        if child not in device_cells:
            continue
        trans = inst.get("trans") or {}
        if trans.get("rotation_deg") or trans.get("mirror"):
            problems.append(
                f"instance of {child!r} is rotated/mirrored "
                f"({trans}); v1 placement supports r0 only — extend "
                "deliberately before trusting its terminals."
            )
            continue
        if child not in terminals_by_cell:
            try:
                terminals_by_cell[child] = terminal_provider(client, child)
            except RecipeError as exc:
                problems.append(
                    f"terminal provider failed for cell {child!r}: {exc}")
                terminals_by_cell[child] = None
        terms = terminals_by_cell[child]
        if terms is None:
            continue
        n += 1
        inst_id = f"X{n}"
        dx = float(trans.get("dx_dbu", 0)) * dbu
        dy = float(trans.get("dy_dbu", 0)) * dbu
        instances.append({
            "instance_id": inst_id,
            "device_cell": child,
            "transform": {"dx_um": dx, "dy_um": dy,
                          "rotation_deg": 0.0, "mirror": False},
        })
        for name, term in terms.items():
            placed.append(PlacedTerminal(
                inst_id, name, term.layer,
                (term.center_um[0] + dx, term.center_um[1] + dy),
                width_um=term.width_um,
                orientation_deg=term.orientation_deg,
                length_um=term.length_um,
                forbidden_attach_deg=term.forbidden_attach_deg,
            ))
    return {
        "instances": instances,
        "placed": placed,
        "terminals_by_cell": {
            k: v for k, v in terminals_by_cell.items() if v is not None
        },
        "problems": problems,
    }


def collect_built_devices(
    client: Any,
    cell: str,
    placement: Mapping[str, Any],
    terminal_provider: Any,
) -> Dict[str, Any]:
    """Build-path collector: recipe-free, but extraction-compatible.

    The drawn devices are PCell VARIANT cells whose names (``transistorBG$1`` ...)
    are decoupled from the device keys, AND device extraction (device_lvs_db)
    finds them by their LIVE name. So: query the layout for the variant cells +
    positions, match each back to the KNOWN ``placement`` BY POSITION to recover
    its device key, record the device under its LIVE variant name (so the
    extractor finds it), and read terminals from DATA via
    ``terminal_provider(_, device_key)`` -- no device geometry rule is evaluated.

    ``placement`` maps ``instance_id -> (device_key, dx_um, dy_um)``."""
    result = client.instance_query(cell)
    dbu = float(client.layout_info()["dbu"])
    pos_index = {(round(dx, 3), round(dy, 3)): (inst_id, key)
                 for inst_id, (key, dx, dy) in placement.items()}
    placed: List[PlacedTerminal] = []
    instances: List[Dict[str, Any]] = []
    terms_by_variant: Dict[str, Any] = {}
    problems: List[str] = []
    matched: set = set()
    for inst in result.get("instances", []):
        trans = inst.get("trans") or {}
        dx = float(trans.get("dx_dbu", 0)) * dbu
        dy = float(trans.get("dy_dbu", 0)) * dbu
        hit = pos_index.get((round(dx, 3), round(dy, 3)))
        if hit is None or hit[0] in matched:
            continue
        inst_id, key = hit
        matched.add(inst_id)
        variant = str(inst.get("child") or "")
        if variant not in terms_by_variant:
            try:
                terms_by_variant[variant] = terminal_provider(None, key)
            except Exception as exc:
                problems.append(
                    f"terminal provider failed for device {key!r} "
                    f"(cell {variant!r}): {exc}")
                terms_by_variant[variant] = None
        td = terms_by_variant[variant]
        if td is None:
            continue
        instances.append({
            "instance_id": inst_id, "device_cell": variant,
            "transform": {"dx_um": dx, "dy_um": dy,
                          "rotation_deg": 0.0, "mirror": False}})
        for name, term in td.items():
            placed.append(PlacedTerminal(
                inst_id, name, term.layer,
                (term.center_um[0] + dx, term.center_um[1] + dy),
                width_um=term.width_um,
                orientation_deg=term.orientation_deg,
                length_um=term.length_um,
                forbidden_attach_deg=term.forbidden_attach_deg))
    missing = sorted(set(placement) - matched)
    if missing:
        problems.append(
            f"{len(missing)} placed device(s) had no instance at their "
            f"placement position in the layout: {missing[:8]}")
    return {"instances": instances, "placed": placed,
            "terminals_by_cell": {k: v for k, v in terms_by_variant.items() if v},
            "problems": problems}


def extract_live_connectivity(
    client: Any,
    cell: str,
    *,
    spec: Optional[ConnectivitySpec] = None,
) -> ConnectivityExtractor:
    """Save the live layout via RPC and extract connectivity offline."""

    tmpdir = tempfile.mkdtemp(prefix="klink_lvs_")
    gds_path = os.path.join(tmpdir, "live_snapshot.gds")
    client.call("layout.save_file", {"path": gds_path})
    try:
        return ConnectivityExtractor.from_file(gds_path, cell, spec)
    finally:
        try:
            os.remove(gds_path)
            os.rmdir(tmpdir)
        except OSError:
            pass


_DIRS = {0.0: (1.0, 0.0), 90.0: (0.0, 1.0),
         180.0: (-1.0, 0.0), 270.0: (0.0, -1.0)}


def attach_point_um(t: PlacedTerminal) -> Tuple[float, float]:
    """Routing attach point: the pad's OUTER edge along the recipe
    orientation.  Agents must never compute this themselves — the
    recipe carries the pad geometry (foolproofing ruling, Update 24)."""
    d = _DIRS.get(float(t.orientation_deg))
    if d is None:
        raise ValueError(
            f"{t.instance}.{t.terminal}: non-cardinal orientation "
            f"{t.orientation_deg}; extend attach_point_um deliberately."
        )
    half = float(t.length_um) / 2.0
    return (t.point_um[0] + d[0] * half, t.point_um[1] + d[1] * half)


def declare_nets_from_sends(
    sends: Sequence[Mapping[str, Any]],
    *,
    probe_um: Any,
    terminals_by_net: Mapping[str, Sequence[str]],
    dbu: float,
    existing: Sequence[Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    """ONE SEND on the wiring = ONE declared net, resolved through
    derived connectivity (no geometric tolerance to tune — ever).

    Each selected wiring shape is probed at its bbox center
    (``probe_um(layer, x, y) -> net_id``); the declared net is the set
    of terminals on every touched connected component
    (``terminals_by_net``).  Consequences, by construction:

    - matching is exact: the wire IS part of the component;
    - an unintended short surfaces AT DECLARATION TIME (the component
      carries extra terminals — the result lists them for review);
    - one SEND spanning two components declares them as one intended
      net, and lvs_check will report the open until the wire is drawn.

    Geometric tolerance matching was tried and rejected live
    (sel_0011: 50um-wide load pads over-match on width-as-tolerance).
    """

    nets = [dict(n) for n in existing]
    problems: List[str] = []
    taken = {ref for n in nets for ref in n.get("terminals", [])}
    seq = len(nets)
    for send in sends:
        items = list(send.get("items") or [])
        sel_id = send.get("selection_id") or send.get("id") or "?"
        touched: List[str] = []
        notes: List[str] = []
        for item in items:
            if item.get("is_cell_inst"):
                notes.append(
                    "an instance was selected — select the wiring "
                    "shapes themselves, not device instances"
                )
                continue
            bbox = item.get("bbox_dbu")
            layer = item.get("layer")
            datatype = item.get("datatype", 0)
            if not bbox or len(bbox) != 4 or layer is None:
                continue
            key = f"{layer}/{datatype}"
            cx = (bbox[0] + bbox[2]) / 2.0 * dbu
            cy = (bbox[1] + bbox[3]) / 2.0 * dbu
            try:
                net_id = probe_um(key, cx, cy)
            except Exception as exc:
                notes.append(
                    f"layer {key} is not a declared conductor ({exc})"
                )
                continue
            if net_id is None:
                notes.append(
                    f"no conducting geometry under the selected shape "
                    f"at ({cx:.2f}, {cy:.2f}) on {key}"
                )
                continue
            if net_id not in touched:
                touched.append(net_id)
        if not touched:
            problems.append(
                f"SEND {sel_id}: resolved no connected component "
                f"({'; '.join(notes) or 'no usable shape items'}). "
                "Select the wiring of the intended connection and SEND "
                "again."
            )
            continue
        refs = sorted({
            ref for net in touched
            for ref in terminals_by_net.get(net, ())
        })
        fresh = [r for r in refs if r not in taken]
        if not fresh:
            problems.append(
                f"SEND {sel_id}: component(s) {touched} carry no "
                f"unassigned terminals (found: {refs or 'none'}); "
                "this wiring is already declared or reaches no device "
                "terminal."
            )
            continue
        seq += 1
        nets.append({"net": f"net_{seq:03d}", "terminals": fresh})
        taken.update(fresh)
    declared_nets_from_dicts(nets)
    return {"nets": nets, "problems": problems}


def write_spec_file(
    client: Any,
    cell: str,
    *,
    layer_roles: Mapping[str, str],
    declared: Optional[Sequence[Mapping[str, Any]]] = None,
    spec_root: str = DEFAULT_SPEC_ROOT,
    terminal_provider: Any = None,
    device_cells: Any = None,
    device_class: str = "device",
    connectivity: Optional[ConnectivitySpec] = None,
    stack: Any = None,
) -> Dict[str, Any]:
    """One call: project the live cell into a klink.spec.json v1 file.

    ``stack`` (optional StackSpec): when given, its declaration is
    persisted as process.stack -- the single source of layer relations
    for routing and LVS (F0). Its conductor/via layers must all appear
    in ``layer_roles`` (validate_spec enforces this).

    ``layer_roles`` maps "L/D" -> role and is recorded as user_declared
    (RULED-3 pattern: roles are per-layout declarations).  Device
    parameters are NOT inferred from cell names here -- that mapping is
    a per-layout convention; the omission is recorded as an assumption.
    """

    from klink.spec import build_spec, write_spec

    if declared is None:
        declared = load_declared_nets(spec_root, cell)
    if terminal_provider is None or device_cells is None:
        return {"ok": False, "cell": cell, "problems": [
            "no terminal provider / device-cell set; klink ships none. The spec "
            "needs to know which child cells are devices and their terminals."],
            "next_action": "pass terminal_provider + device_cells -- an "
            "interactive flow injects a device recipe from "
            "your recipes with the device keys."}
    collected = collect_placed_terminals(
        client, cell, terminal_provider=terminal_provider, device_cells=device_cells
    )
    if connectivity is None:
        return {"ok": False, "cell": cell, "problems": [
            "no connectivity given; klink ships no process -- the spec needs YOUR "
            "process's conductor layers + via stacks."],
            "next_action": "pass connectivity (profile.connectivity_spec() from a "
            "profile in your pdk.py) or conductors=[...]/vias=[...]."}
    extractor = extract_live_connectivity(client, cell, spec=connectivity)
    table = extractor.terminal_net_table(collected["placed"])

    devices = []
    for cell_name, terms in sorted(collected["terminals_by_cell"].items()):
        # the device's source is its terminals' source (the recipe/provider that
        # produced them), not a baked-in device name.
        dev_source = (next(iter(terms.values())).source if terms else "derived")
        devices.append({
            "device_id": f"dev.{cell_name}",
            "device_class": device_class,
            "cell": cell_name,
            "source": dev_source,
            "terminals": [
                {"name": t.name, "layer": t.layer,
                 "center_um": list(t.center_um),
                 "orientation_deg": t.orientation_deg,
                 "width_um": t.width_um}
                for t in terms.values()
            ],
        })
    instances = [
        {"instance_id": i["instance_id"],
         "device_id": f"dev.{i['device_cell']}",
         "transform": i["transform"]}
        for i in collected["instances"]
    ]
    derived_terms: Dict[str, List[str]] = {}
    for row in table["rows"]:
        if row["net_id"]:
            derived_terms.setdefault(row["net_id"], []).append(
                f"{row['instance']}.{row['terminal']}")
    nets: Dict[str, Any] = {
        "declared": [{**dict(n), "source": "user_declared"}
                     for n in declared],
        "derived": [
            {"net_id": n["net_id"],
             "terminals": sorted(derived_terms.get(n["net_id"], [])),
             "shapes_by_layer": n["shapes_by_layer"],
             "source": "derived:layout_to_netlist"}
            for n in extractor.nets()
        ],
    }
    if declared:
        nets["reconciliation"] = reconcile(
            declared_nets_from_dicts(declared), table)

    info = client.layout_info()
    spec = build_spec(
        layout={"file": info.get("file"), "top_cell": cell,
                "dbu": float(info["dbu"])},
        process_layers=[
            {"layer": int(k.split("/")[0]), "datatype": int(k.split("/")[1]),
             "role": role, "source": "user_declared"}
            for k, role in sorted(layer_roles.items())
        ],
        devices=devices,
        instances=instances,
        nets=nets,
        assumptions=[
            {"statement": "device parameters not extracted from cell "
                          "names; naming conventions are per-layout",
             "source": "user_declared"},
        ],
        stack=stack.to_dict() if stack is not None else None,
    )
    path = Path(spec_root) / f"{cell}.klink.spec.json"
    write_spec(spec, str(path))
    return {
        "ok": not collected["problems"],
        "spec_path": str(path),
        "devices": len(devices),
        "instances": len(instances),
        "declared_nets": len(nets["declared"]),
        "derived_nets": len(nets["derived"]),
        "reconciliation_ok": nets.get("reconciliation", {}).get("ok"),
        "problems": collected["problems"],
        "missing_layers": extractor.missing_layers,
        "next_action": "done — the spec file is the machine-readable "
                       "fact projection of this cell; tell the user "
                       "where it is",
    }


def _edge_attach(
    bbox: Tuple[float, float, float, float], orientation_deg: float,
    point: Tuple[float, float],
) -> Tuple[float, float]:
    """Attach point on a bbox edge in the given cardinal direction,
    aligned with the terminal's other coordinate."""
    d = _DIRS.get(float(orientation_deg))
    if d is None:
        raise ValueError(f"non-cardinal orientation {orientation_deg}")
    if d[0] > 0:
        return (bbox[2], point[1])
    if d[0] < 0:
        return (bbox[0], point[1])
    if d[1] > 0:
        return (point[0], bbox[3])
    return (point[0], bbox[1])


def _corridor_clear(
    start: Tuple[float, float], direction: Tuple[float, float],
    obstacles: Sequence[Tuple[float, float, float, float]],
    *, width_um: float, probe_um: float, clearance_um: float,
) -> bool:
    end = (start[0] + direction[0] * probe_um,
           start[1] + direction[1] * probe_um)
    half = width_um / 2.0 + clearance_um
    corridor = (min(start[0], end[0]) - half, min(start[1], end[1]) - half,
                max(start[0], end[0]) + half, max(start[1], end[1]) + half)
    for b in obstacles:
        if (min(corridor[2], b[2]) > max(corridor[0], b[0])
                and min(corridor[3], b[3]) > max(corridor[1], b[1])):
            return False
    return True


def _choose_attach(
    patch_bbox: Tuple[float, float, float, float],
    point: Tuple[float, float],
    partners: Sequence[Tuple[float, float]],
    obstacles: Sequence[Tuple[float, float, float, float]],
    *, width_um: float, clearance_um: float,
    allowed: Optional[Sequence[float]] = None,
) -> Tuple[Tuple[float, float], float, Optional[str]]:
    """Pick the attach edge + launch direction on a patch/pad: cardinal
    directions ranked by alignment toward the net's other terminals,
    first one whose immediate corridor is obstacle-free wins.  Agents
    never make this judgment call (Principle 7); if no direction is
    clear, the best-ranked one is used and a problem note returned.

    ``allowed`` restricts the candidates (pad terminals exclude the
    channel-facing side — crossing the gap would short S/D)."""
    cx = sum(p[0] for p in partners) / len(partners) - point[0]
    cy = sum(p[1] for p in partners) / len(partners) - point[1]
    ranked = sorted(
        ((o, d) for o, d in _DIRS.items()
         if allowed is None or o in allowed),
        key=lambda kv: -(kv[1][0] * cx + kv[1][1] * cy))
    # probe far enough to clear a full device row: a short probe can
    # approve a direction whose pocket dead-ends just beyond it (live
    # finding: net B's via chose south into a wall; west was open)
    for orientation, d in ranked:
        attach = _edge_attach(patch_bbox, orientation, point)
        if _corridor_clear(attach, d, obstacles, width_um=width_um,
                           probe_um=12 * width_um,
                           clearance_um=clearance_um):
            return attach, orientation, None
    orientation, d = ranked[0]
    return (_edge_attach(patch_bbox, orientation, point), orientation,
            "no obstacle-free launch corridor found around the patch at "
            f"{point}; using the best-aligned direction {orientation} — "
            "expect a routing failure that names this net.")


def _terminal_pad_bbox(t: PlacedTerminal) -> Tuple[float, float, float, float]:
    d = _DIRS[float(t.orientation_deg)]
    half_l = float(t.length_um) / 2.0
    half_w = float(t.width_um) / 2.0
    if d[0]:  # oriented along x: length in x, width in y
        return (t.point_um[0] - half_l, t.point_um[1] - half_w,
                t.point_um[0] + half_l, t.point_um[1] + half_w)
    return (t.point_um[0] - half_w, t.point_um[1] - half_l,
            t.point_um[0] + half_w, t.point_um[1] + half_l)


def _route_bboxes_um(groups: Sequence[Mapping[str, Any]]) -> List[
        Tuple[float, float, float, float]]:
    """Segment bboxes of routed polylines (used as keepouts for the
    nets routed after them)."""
    out: List[Tuple[float, float, float, float]] = []
    for group in groups:
        for route in group.get("routes", ()):
            pts = route.get("points_um") or []
            half = float(route.get("width_um", 0.0)) / 2.0
            for a, b in zip(pts, pts[1:]):
                out.append((min(a[0], b[0]) - half, min(a[1], b[1]) - half,
                            max(a[0], b[0]) + half, max(a[1], b[1]) + half))
    return out


def _snapshot_paths(client: Any, cell: str, layer: str, dbu: float):
    """Identify route paths on a layer by exact geometry.  Backend
    result formats differ (segment groups carry no route geometry), so
    the layout itself is the one honest source for what a routing call
    actually added."""
    layer_no, dt = (int(v) for v in layer.split("/"))
    out = {}
    for s in client.shape_query(cell, layers=[layer],
                                kinds=["paths"], limit=5000)["shapes"]:
        key = (tuple(tuple(p) for p in s["points_dbu"]),
               s.get("width_dbu"))
        out[key] = s
    return out


def _paths_metrics_um(new_paths, dbu: float):
    length = 0.0
    bboxes: List[Tuple[float, float, float, float]] = []
    for (points, width_dbu), _ in new_paths.items():
        half = float(width_dbu or 0) * dbu / 2.0
        for a, b in zip(points, points[1:]):
            length += (abs(b[0] - a[0]) + abs(b[1] - a[1])) * dbu
            bboxes.append((min(a[0], b[0]) * dbu - half,
                           min(a[1], b[1]) * dbu - half,
                           max(a[0], b[0]) * dbu + half,
                           max(a[1], b[1]) * dbu + half))
    return length, bboxes


def connect_nets(
    client: Any,
    cell: str,
    *,
    spec_root: str = DEFAULT_SPEC_ROOT,
    terminal_provider: Any = None,
    device_cells: Any = None,
    connectivity: Optional[ConnectivitySpec] = None,
    route_layer: str,
    route_width_um: float,
    via_cell: str,
    damping_distance_um: float = 1.0,
    min_spacing_um: float = 0.0,
    min_width_um: float = 0.0,
    route_channels: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """One call: wire every declared-but-unconnected net and verify.

    The whole Update-24 lesson set runs inside this call so no agent
    ever repeats it: attach points come from recipes, vias follow the
    layer-bridging convention (reusing existing patches instead of
    stacking new ones), obstacles are built automatically
    (everything-not-this-net is keepout), routing uses the damped
    backends, and LVS gates the result — on mismatch every mutation is
    undone (no scars).  Results carry next_action (Principle 7a).
    """

    from klink.routing.backends.geometric.damped import (
        route_damped_segment_cell,
        route_damped_steiner_cell,
    )

    # routing-tier process rules (two-tier model, IR doc §8b): these
    # constrain ROUTER OUTPUT only — cell-internal geometry is the
    # litho tier, checked by DRC, never touched here.
    if min_width_um > 0 and route_width_um < min_width_um:
        return {"ok": False, "cell": cell, "problems": [
            f"route_width_um {route_width_um} violates the declared "
            f"routing-tier minimum width {min_width_um}; raise "
            "route_width_um or correct the rule."],
            "next_action": "call structdevice.connect_nets again with a "
                           "compliant route_width_um"}
    # the spacing rule is a HARD floor under the soft quality damping:
    # the damped router expands obstacles by (width/2 + clearance), so
    # clearance = max(damping, min_spacing) guarantees edge-to-edge
    # spacing >= min_spacing against every keepout
    effective_clearance = max(float(damping_distance_um),
                              float(min_spacing_um))

    declared = load_declared_nets(spec_root, cell)
    if not declared:
        return {
            "ok": False, "cell": cell,
            "problems": [
                f"no declared nets for cell {cell!r}; declare intent "
                "first."
            ],
            "next_action": "structdevice.declare_nets "
                           f"{{recent_sends: 1, cell: '{cell}'}} after the "
                           "user SENDs the wiring/terminal region",
        }

    if terminal_provider is None or device_cells is None:
        return {"ok": False, "cell": cell, "problems": [
            "no terminal provider / device-cell set; klink ships none. Routing "
            "+ LVS must know which child cells are devices and their terminals."],
            "next_action": "pass terminal_provider + device_cells -- an "
            "interactive flow injects a device recipe from "
            "your recipes with the device keys."}
    collected = collect_placed_terminals(
        client, cell, terminal_provider=terminal_provider, device_cells=device_cells)
    if collected["problems"]:
        return {"ok": False, "cell": cell,
                "problems": collected["problems"],
                "next_action": "fix the reported instances, then call "
                               "structdevice.connect_nets again"}

    if connectivity is None:
        return {"ok": False, "cell": cell, "problems": [
            "no connectivity given; klink ships no process -- routing/LVS needs "
            "YOUR process's conductor layers + via stacks."],
            "next_action": "pass connectivity (profile.connectivity_spec() from a "
            "profile in your pdk.py) or conductors=[...]/vias=[...]."}
    extractor = extract_live_connectivity(client, cell, spec=connectivity)
    table = extractor.terminal_net_table(collected["placed"])
    by_ref = {f"{r['instance']}.{r['terminal']}": r["net_id"]
              for r in table["rows"]}
    placed_by_ref = {f"{t.instance}.{t.terminal}": t
                     for t in collected["placed"]}

    try:
        existing_keepouts = client.shape_query(
            cell, layers=[KLINK_KEEPOUT_LAYER], limit=1)
    except Exception:
        # layer 900/0 not present in this layout = no keepouts (legal)
        existing_keepouts = {"shapes": []}
    if existing_keepouts.get("shapes"):
        return {"ok": False, "cell": cell,
                "problems": [
                    f"cell {cell!r} already has shapes on keepout layer "
                    "900/0; connect_nets manages that layer itself. Move "
                    "or remove the existing keepouts first."],
                "next_action": "clean layer 900/0 in this cell, then call "
                               "structdevice.connect_nets again"}

    declared_names = {n["net"] for n in declared}
    stale = [p["name"] for p in client.call(
        "port.list", {"cell": cell}).get("ports", [])
        if str(p.get("net") or "") in declared_names]
    if stale:
        return {"ok": False, "cell": cell,
                "problems": [
                    f"cell {cell!r} already has Port markers on declared "
                    f"nets: {stale} (stale markers from a previous "
                    "attempt or manual session). The router would treat "
                    "them as extra terminals."],
                "next_action": "remove them (port.unmark each, or "
                               "port.delete_all if the cell has no other "
                               "markers), then call "
                               "structdevice.connect_nets again"}

    # via cell geometry (only needed when a terminal must change layers)
    via_boxes_by_layer: Dict[str, List[Tuple[float, float, float, float]]] = {}
    layer_by_index = {
        e["layer_index"]: f"{e['layer']}/{e['datatype']}"
        for e in client.layer_list()["layers"]}
    dbu = float(client.layout_info()["dbu"])
    for s in client.shape_query(via_cell)["shapes"]:
        if s.get("type") != "box":
            continue
        key = layer_by_index.get(s["layer_index"])
        b = s["bbox_dbu"]
        via_boxes_by_layer.setdefault(key, []).append(
            (b[0] * dbu, b[1] * dbu, b[2] * dbu, b[3] * dbu))

    # plan each declared net
    pending, infos, problems = [], [], []
    for net in declared:
        refs = list(net["terminals"])
        nets_of = {by_ref.get(r) for r in refs}
        if None in nets_of:
            floating = [r for r in refs if by_ref.get(r) is None]
            problems.append(
                f"net {net['net']!r}: terminal(s) {floating} hit no "
                "conducting geometry; fix the layout first.")
            continue
        if len(nets_of) == 1:
            infos.append(f"net {net['net']!r} is already connected "
                         f"({next(iter(nets_of))}); skipped.")
            continue
        own_nets = {by_ref[r] for r in refs}
        obstacles = []
        for n in extractor.nets():
            if n["net_id"] in own_nets:
                continue
            obstacles.extend(
                extractor.net_shape_bboxes_um(n["net_id"], route_layer))

        terminals, vias, via_patches = [], [], []
        for ref in refs:
            t = placed_by_ref[ref]
            if t.layer == route_layer:
                terminals.append({"ref": ref, "t": t, "kind": "pad",
                                  "bbox": _terminal_pad_bbox(t)})
                continue
            own = extractor.net_shape_bboxes_um(by_ref[ref], route_layer)
            if own:
                patch_box = min(own, key=lambda b: abs(
                    (b[0] + b[2]) / 2 - t.point_um[0]) + abs(
                    (b[1] + b[3]) / 2 - t.point_um[1]))
            else:
                patch = via_boxes_by_layer.get(route_layer)
                bridge = via_boxes_by_layer.get(t.layer)
                if not patch or not bridge:
                    problems.append(
                        f"net {net['net']!r}: terminal {ref} is on "
                        f"{t.layer} and via cell {via_cell!r} has no box "
                        f"on {'/'.join(k for k in (route_layer, t.layer) if not via_boxes_by_layer.get(k))}; "
                        "declare a via cell that bridges these layers.")
                    break
                vias.append({"child": via_cell,
                             "position_um": list(t.point_um)})
                local = patch[0]
                patch_box = (local[0] + t.point_um[0],
                             local[1] + t.point_um[1],
                             local[2] + t.point_um[0],
                             local[3] + t.point_um[1])
                via_patches.append(patch_box)
            terminals.append({"ref": ref, "t": t, "kind": "patch",
                              "bbox": patch_box})
        else:
            pending.append({"net": net["net"], "terminals": terminals,
                            "vias": vias, "via_patches": via_patches,
                            "obstacles": obstacles})
    if problems:
        return {"ok": False, "cell": cell, "problems": problems,
                "infos": infos,
                "next_action": "fix the reported items, then call "
                               "structdevice.connect_nets again"}

    # net ordering: local nets first, long fanouts last — a fanout
    # branch routed early can block a later local launch zone (live
    # finding on the half-adder); routed-last nets dodge via keepouts
    def _net_extent(plan):
        xs = [term["t"].point_um[0] for term in plan["terminals"]]
        ys = [term["t"].point_um[1] for term in plan["terminals"]]
        return (max(xs) - min(xs)) + (max(ys) - min(ys))
    pending.sort(key=lambda p: (len(p["terminals"]), _net_extent(p)))

    # auto-corridor lanes (ESCALATION level 2 only — they rescue
    # congested builds but make compact cells balloon: a 3-terminal
    # local net hauled to an east lane was a 4.5x-HPWL user-reported
    # regression). Level 0 routes with no corridors at all.
    geometry_max_x = None
    for n in extractor.nets():
        for b in extractor.net_shape_bboxes_um(n["net_id"], route_layer):
            geometry_max_x = b[2] if geometry_max_x is None else max(
                geometry_max_x, b[2])
    long_extent = 20 * route_width_um

    def _assign_corridors(enabled: bool) -> None:
        lane_counts: Dict[Any, int] = {}
        lane_pitch = 2 * route_width_um
        for plan in pending:
            # lanes for multi-terminal nets AND long two-terminal nets:
            # long nets threading device/via columns hit walls of
            # other-net keepouts (live finding: input net B, 200um span)
            if not enabled or geometry_max_x is None or (
                    len(plan["terminals"]) < 3
                    and _net_extent(plan) <= long_extent):
                plan["corridor"] = None
                continue
            ys = [term["t"].point_um[1] for term in plan["terminals"]]
            xs = [term["t"].point_um[0] for term in plan["terminals"]]
            # the channel nearest the net's x-midpoint: trunks belong
            # BETWEEN their terminals (grouped placement hands channel
            # centers over; without channels, east of everything)
            if route_channels:
                net_mid_x = (min(xs) + max(xs)) / 2.0
                base = min(route_channels,
                           key=lambda c: abs(c - net_mid_x))
                lane_key = round(base, 3)
            else:
                base = geometry_max_x + 3 * route_width_um
                lane_key = "global"
            lane_n = lane_counts.get(lane_key, 0)
            lane_counts[lane_key] = lane_n + 1
            lane_x = base + lane_n * lane_pitch
            mid = (min(ys) + max(ys)) / 2.0
            plan["corridor"] = {
                "id": f"COR_{plan['net']}",
                "kind": "corridor",
                "net": plan["net"],
                "center_um": [lane_x, mid],
                "path_points": "0,%.3f;0,%.3f" % (min(ys) - 5 - mid,
                                                  max(ys) + 5 - mid),
            }

    if not pending:
        return {"ok": True, "cell": cell, "routed": [], "infos": infos,
                "next_action": "structdevice.spec_write "
                               f"{{cell: '{cell}', layer_roles: ...}}"}

    # execute, counting every mutating RPC for no-scars rollback
    ops = 0
    routed = []

    class _NetRouteFailure(RuntimeError):
        def __init__(self, plan, message):
            super().__init__(message)
            self.plan = plan

    def _cleanup_net(plan, ports, had_boxes):
        nonlocal ops
        for p in ports:
            try:
                client.call("port.unmark",
                            {"cell": cell, "name": p["name"]})
                ops += 1
            except Exception:
                pass
        if plan.get("corridor"):
            try:
                client.call("anchor.unmark",
                            {"cell": cell, "id": plan["corridor"]["id"]})
                ops += 1
            except Exception:
                pass
        if had_boxes:
            client.call("shape.delete", {"cell": cell, "layer": _KEEPOUT_LAYER_NUM,
                                         "datatype": _KEEPOUT_DATATYPE})
            ops += 1

    def _route_pass(order, initial_paths, flexible_attach, seg_sink=None):
        """Route nets in the given order; raises _NetRouteFailure with
        the failed plan (its markers already cleaned up).

        If ``seg_sink`` (dict) is given, it is filled net -> [segment
        dicts {a,b,width_um}] for every net routed in this pass — the
        negotiation loop reads it to feed the resource cost table."""
        nonlocal ops
        pass_routed = []
        extra_keepouts: List[Tuple[float, float, float, float]] = []
        seen_paths = dict(initial_paths)
        for plan in order:
            other_patches = [b for q in pending if q is not plan
                             for b in q["via_patches"]]
            boxes = plan["obstacles"] + other_patches + extra_keepouts
            # attach sides are chosen NOW, against the keepout field as
            # it exists in this pass — a side picked at planning time
            # is blind to wires routed since (R12 live finding)
            ports = []
            for term in plan["terminals"]:
                t = term["t"]
                partners = [q["t"].point_um for q in plan["terminals"]
                            if q is not term]
                # forbidden attach sides are RECIPE knowledge (device
                # physics), never inferred here — keeps the orchestrator
                # device-class-agnostic (user generality ruling).
                # Pad-side flexibility is an ESCALATION feature: the
                # compact recipe-orientation attach is level 0 (user-
                # reported regression: flexible sides made easy cells
                # ugly); patches always probe (no physical orientation).
                if term["kind"] == "pad" and not flexible_attach:
                    attach = attach_point_um(t)
                    orientation = t.orientation_deg
                elif partners:
                    allowed = None
                    if t.forbidden_attach_deg is not None:
                        allowed = [o for o in _DIRS
                                   if o != float(t.forbidden_attach_deg)]
                    attach, orientation, note = _choose_attach(
                        term["bbox"], t.point_um, partners, boxes,
                        width_um=route_width_um,
                        clearance_um=effective_clearance,
                        allowed=allowed)
                    if note:
                        infos.append(f"net {plan['net']!r}: {note}")
                else:
                    attach = attach_point_um(t)
                    orientation = t.orientation_deg
                ports.append(
                    {"name": f"{plan['net']}_{term['ref']}".replace(".", "_"),
                     "center": attach, "orientation": orientation})
            if boxes:
                client.call("shape.insert_many", {"cell": cell, "items": [
                    {"kind": "box", "layer": _KEEPOUT_LAYER_NUM, "datatype": _KEEPOUT_DATATYPE,
                     "bbox_um": list(b)} for b in boxes]})
                ops += 1
            for p in ports:
                client.call("port.mark", {
                    "cell": cell, "name": p["name"],
                    "center_um": list(p["center"]),
                    "orientation": p["orientation"],
                    "width_um": route_width_um, "net": plan["net"],
                    "target_layer": route_layer})
                ops += 1
            if plan.get("corridor"):
                client.call("anchor.mark", {"cell": cell,
                                            **plan["corridor"]})
                ops += 1
            router = (route_damped_segment_cell if len(ports) == 2
                      else route_damped_steiner_cell)
            # Pass our reserved scratch keepout layer EXPLICITLY — the geometric
            # backends no longer default obstacle_layers to it.
            kwargs = dict(damping_distance_um=effective_clearance,
                          clear=False,
                          obstacle_layers=[KLINK_KEEPOUT_LAYER])
            if router is route_damped_steiner_cell:
                kwargs["route_layer"] = route_layer
            try:
                result = router(client, cell, **kwargs)
                if not result.get("ok"):
                    raise RuntimeError(
                        "routing failed: "
                        f"{[g.get('errors') for g in result.get('groups', ())] or result.get('errors')}")
            except Exception as exc:
                _cleanup_net(plan, ports, bool(boxes))
                raise _NetRouteFailure(
                    plan,
                    f"net {plan['net']!r} "
                    f"({[p['name'] for p in ports]}): {exc}"
                ) from exc
            ops += sum(1 for g in result.get("groups", ())
                       if g.get("write"))
            current_paths = _snapshot_paths(client, cell, route_layer, dbu)
            new_paths = {k: v for k, v in current_paths.items()
                         if k not in seen_paths}
            seen_paths = current_paths
            length, new_bboxes = _paths_metrics_um(new_paths, dbu)
            extra_keepouts.extend(new_bboxes)
            if seg_sink is not None:
                segs = []
                for (points, width_dbu), _ in new_paths.items():
                    for a, b in zip(points, points[1:]):
                        segs.append({"a": (a[0] * dbu, a[1] * dbu),
                                     "b": (b[0] * dbu, b[1] * dbu),
                                     "width_um": (width_dbu or 0) * dbu})
                seg_sink[plan["net"]] = segs
            _cleanup_net(plan, ports, bool(boxes))
            xs = [p["center"][0] for p in ports]
            ys = [p["center"][1] for p in ports]
            hpwl = (max(xs) - min(xs)) + (max(ys) - min(ys))
            entry = {"net": plan["net"],
                     "ports": [p["name"] for p in ports],
                     "vias": len(plan["vias"]),
                     "length_um": round(length, 3),
                     "hpwl_lower_bound_um": round(hpwl, 3)}
            if hpwl > 0 and length > 1.5 * hpwl:
                infos.append(
                    f"net {plan['net']!r}: routed length {length:.1f}um is "
                    f"{length / hpwl:.1f}x the HPWL lower bound "
                    f"{hpwl:.1f}um — connected but wasteful geometry; "
                    "see ROUTING_MINIMALITY_SPEC defects.")
            pass_routed.append(entry)
        return pass_routed

    try:
        ensure = client.call("layer.ensure", {"layer": _KEEPOUT_LAYER_NUM, "datatype": _KEEPOUT_DATATYPE,
                                              "name": "KLINK_KEEPOUT"})
        if ensure.get("created"):
            ops += 1
        initial_paths = _snapshot_paths(client, cell, route_layer, dbu)
        # place ALL planned vias up front: a lazily-placed via patch
        # cannot be avoided by nets routed before it (live finding —
        # input net B blocked a later net's via launch zone)
        all_vias = [v for plan in pending for v in plan["vias"]]
        if all_vias:
            client.call("instance.insert_many",
                        {"parent": cell, "items": all_vias})
            ops += 1
        # progressive escalation (user-reported regression: congestion
        # rescue features as DEFAULTS make compact cells balloon):
        #   level 0  compact classic — no corridors, recipe-orientation
        #            pad attach (the behavior the accepted Inverter/
        #            NAND2 builds had)
        #   level 1  + flexible pad attach sides
        #   level 2  + corridor lanes
        # within each level: bounded multi-round negotiation (each
        # failed net promoted at most once; cycle -> next level).
        def _rip_up():
            nonlocal ops
            deleted = client.call("shape.delete", {
                "cell": cell,
                "layer": int(route_layer.split("/")[0]),
                "datatype": int(route_layer.split("/")[1]),
                "kinds": ["paths"]})
            if deleted.get("deleted"):
                ops += 1

        routed = None
        last_failure: Optional[BaseException] = None
        for level in (0, 1, 2):
            _assign_corridors(enabled=(level >= 2))
            order = list(pending)
            promoted: List[str] = []
            if level and last_failure is not None:
                infos.append(f"escalation to level {level}: "
                             f"{last_failure}")
            while True:
                try:
                    routed = _route_pass(order, initial_paths,
                                         flexible_attach=(level >= 1))
                    break
                except _NetRouteFailure as failure:
                    last_failure = failure
                    if initial_paths:
                        raise RuntimeError(
                            f"{failure}; negotiated retry skipped "
                            "because the cell already contains route "
                            "paths (rip-up would lose their end "
                            "extensions)") from failure
                    _rip_up()
                    if failure.plan["net"] in promoted:
                        routed = None
                        break  # cycle at this level -> escalate
                    promoted.append(failure.plan["net"])
                    infos.append(
                        f"negotiated retry round {len(promoted)} "
                        f"(level {level}): {failure}; promoting this "
                        "net.")
                    order = [failure.plan] + [p for p in order
                                              if p is not failure.plan]
            if routed is not None:
                break

        # level 3: cost-ordered iterative reroute with global history
        # (negotiated routing v2, docs/NEGOTIATED_ROUTING_V2_DESIGN.md).
        # Runs only after the cheap levels exhaust; PathFinder ORDER +
        # HISTORY breaks the cyclic displacement levels 0-2 cannot.
        if routed is None and not initial_paths:
            from klink.routing.backends.negotiated.negotiated import (
                negotiation_order, repopulate_occupancy)
            from klink.routing.backends.negotiated.negotiated_resources import ResourceCostTable

            _assign_corridors(enabled=True)

            def _orient_side(deg):
                return {0.0: "right", 90.0: "up", 180.0: "left",
                        270.0: "down"}.get(float(deg) % 360.0, "right")

            def _cost_plan(plan, segments):
                ports = []
                for term in plan["terminals"]:
                    t = term["t"]
                    inst, _, tname = term["ref"].partition(".")
                    ports.append({
                        "name": f"{plan['net']}_{term['ref']}".replace(".", "_"),
                        "center_um": t.point_um,
                        "orientation_deg": t.orientation_deg,
                        "width_um": t.width_um or route_width_um,
                        "instance": inst, "terminal": tname or term["ref"]})
                corridor = ({"id": plan["corridor"]["id"]}
                            if plan.get("corridor") else None)
                return {"net": plan["net"], "ports": ports,
                        "segments": segments, "corridor": corridor}

            def _sides(plan):
                return {f"{plan['net']}_{term['ref']}".replace(".", "_"):
                        _orient_side(term["t"].orientation_deg)
                        for term in plan["terminals"]}

            table = ResourceCostTable()
            order = list(pending)
            pres_fac, hist_fac = 0.5, 1.0
            heuristic = {id(p): (len(p["terminals"]), _net_extent(p))
                         for p in pending}
            # history accumulates fast; if order has not converged in a
            # few rounds the obstruction is physical (multilayer), not
            # orderable — fail honestly rather than burn minutes
            max_iters = 6
            for it in range(max_iters):
                _rip_up()
                seg_sink: Dict[str, list] = {}
                try:
                    routed = _route_pass(order, initial_paths,
                                         flexible_attach=True,
                                         seg_sink=seg_sink)
                    infos.append(
                        f"negotiated v2 converged at iteration {it + 1}")
                    break
                except _NetRouteFailure as failure:
                    last_failure = failure
                    _rip_up()
                    plans = [_cost_plan(p, seg_sink.get(p["net"], []))
                             for p in pending]
                    sides = {p["net"]: _sides(p) for p in pending}
                    repopulate_occupancy(
                        table, plans, spacing_um=effective_clearance,
                        allowed_sides_by_port=sides)
                    # the failed net always accrues memory, even when the
                    # table cannot fully see its conflict (ripped-up geom)
                    from klink.routing.backends.negotiated.negotiated_resources import (
                        LaunchZoneResource)
                    for term in failure.plan["terminals"]:
                        nm = (f"{failure.plan['net']}_{term['ref']}"
                              .replace(".", "_"))
                        table.add_claim(
                            "_FAILMEM",
                            LaunchZoneResource(net=failure.plan["net"],
                                               port_name=nm))
                        table.add_claim(
                            failure.plan["net"],
                            LaunchZoneResource(net=failure.plan["net"],
                                               port_name=nm))
                    table.bump_history(hist_fac)
                    pres_fac *= 1.5
                    order = negotiation_order(
                        pending, table, pres_fac=pres_fac,
                        fallback_key=lambda p: heuristic[id(p)])

        if routed is None:
            raise RuntimeError(
                f"{last_failure}; all escalation levels exhausted "
                "(compact, flexible attach, corridor lanes, cost-ordered "
                "negotiation v2). This is an irreducible crossing — needs "
                "a bridge layer (multilayer escape) or roomier placement "
                "(raise pitch_um / group_pitch_um).")

        report = lvs_check(client, cell, declared=declared,
                           spec_root=spec_root,
                           terminal_provider=terminal_provider,
                           device_cells=device_cells,
                           connectivity=connectivity)
        if not report["ok"]:
            raise RuntimeError(
                "post-routing LVS failed: " + "; ".join(report["problems"]))
    except Exception as exc:
        for _ in range(ops):
            try:
                client.call("edit.undo", {})
            except Exception:
                break
        return {"ok": False, "cell": cell, "routed": routed or [],
                "rolled_back_ops": ops,
                "problems": [f"{exc}", "all mutations were undone."],
                "infos": infos,
                "next_action": "inspect the reported nets, fix layout or "
                               "declaration, then call "
                               "structdevice.connect_nets again"}

    return {"ok": True, "cell": cell, "routed": routed, "infos": infos,
            "lvs": {"matches": report["matches"],
                    "report_path": report.get("report_path")},
            "next_action": "structdevice.spec_write "
                           f"{{cell: '{cell}', layer_roles: ...}}"}


def _device_terminals_from_table(rows, inst_to_cell):
    """Per device cell, the (sorted, deterministic) terminal-name list,
    derived from the live terminal table — NOT hardcoded G/S/D, so it
    generalizes to any device's declared terminals (W7)."""
    by_cell: Dict[str, set] = {}
    for r in rows:
        cell = inst_to_cell.get(r["instance"])
        if cell is None:
            continue
        by_cell.setdefault(cell, set()).add(r["terminal"])
    return {cell: sorted(names) for cell, names in by_cell.items()}


def device_lvs(
    client: Any,
    cell: str,
    declared: Sequence[Mapping[str, Any]],
    table: Mapping[str, Any],
    instances: Sequence[Mapping[str, Any]],
    *,
    connectivity: Optional[ConnectivitySpec] = None,
) -> Dict[str, Any]:
    """Device-level LVS: build a reference netlist from the declared
    netlist (C2) and an extracted netlist from a layout snapshot (C3),
    then compare with KLayout-native NetlistComparer.  Returns a result
    with ``match`` (bool) and instructive ``problems``.  Read-only (a
    temp snapshot only); never mutates the layout (AGENT_TOOL_DESIGN P4).
    The geometry-linked interactive .lvsdb is a follow-up (C6 device
    extractor); this tier gives device+net pass/fail."""
    try:
        import klayout.db as kdb
        from klink.domains.structdevice.reference_netlist import build_reference_netlist
        from klink.domains.structdevice.extracted_netlist import build_extracted_netlist
    except Exception as exc:  # pragma: no cover - import guard
        return {"match": None, "problems": [
            f"device LVS unavailable: {exc}. Ensure klayout.db and the "
            "reference/extracted netlist modules are importable."]}

    inst_min = [{"instance_id": i["instance_id"], "device_cell": i["device_cell"]}
                for i in instances]
    inst_to_cell = {i["instance_id"]: i["device_cell"] for i in instances}
    device_terminals = _device_terminals_from_table(table["rows"], inst_to_cell)
    # declared persistence uses key 'net'; the netlist builders use 'net_id'
    nets = [{"net_id": d.get("net_id", d.get("net")), "terminals": list(d["terminals"])}
            for d in declared]
    device_netlist = {"instances": inst_min, "nets": nets}
    terminal_points = {f'{r["instance"]}.{r["terminal"]}':
                       [r["point_um"][0], r["point_um"][1], r["layer"]]
                       for r in table["rows"] if r.get("point_um")}

    tmpdir = tempfile.mkdtemp(prefix="klink_devlvs_")
    gds_path = os.path.join(tmpdir, "snapshot.gds")
    client.call("layout.save_file", {"path": gds_path})
    try:
        ref = build_reference_netlist(device_netlist, device_terminals, top_name=cell)
        ext = build_extracted_netlist(
            gds_path, cell,
            conductors=list(connectivity.conductors),
            vias=[list(v) for v in connectivity.vias],
            device_instances=inst_min, device_terminals=device_terminals,
            terminal_points=terminal_points)
        match = bool(kdb.NetlistComparer().compare(ext, ref))
        ndev = sum(len(list(c.each_device())) for c in ext.each_circuit())
        problems = [] if match else [
            "device-level LVS mismatch: extracted netlist does not match the "
            "declared netlist topology. Open the per-net reconcile (net-level "
            "report) for which nets/terminals differ, fix the layout, rerun."]
        return {"match": match, "device_count": ndev,
                "device_terminals": device_terminals, "problems": problems}
    finally:
        for p in (gds_path,):
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass


def device_lvs_db(
    client: Any,
    cell: str,
    declared: Sequence[Mapping[str, Any]],
    table: Mapping[str, Any],
    instances: Sequence[Mapping[str, Any]],
    *,
    connectivity: Optional[ConnectivitySpec] = None,
    lvsdb_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Geometry-linked device-level LVS -> a native KLayout ``.lvsdb`` that
    opens in the Netlist Browser with layout<->netlist cross-probing.

    Recipe (validated, STATUS 68): LayoutVsSchematic + connect(nets) +
    per-terminal regions built from the recipe terminal points on snapshot
    temp layers (so same-layer S/D separate) + C6 GenericDeviceExtractor +
    extract + flatten the device-cell subcircuit shells + compare against
    the C2 reference netlist + write .lvsdb. Read-only (temp snapshot);
    terminal names come from the live table, not hardcoded (W7)."""
    try:
        import klayout.db as kdb
        from klink.domains.structdevice.reference_netlist import build_reference_netlist
        from klink.domains.structdevice.device_extractor import register_device_extractors
    except Exception as exc:  # pragma: no cover
        return {"match": None, "problems": [
            f"device .lvsdb unavailable: {exc}."]}

    inst_to_cell = {i["instance_id"]: i["device_cell"] for i in instances}
    device_terminals = _device_terminals_from_table(table["rows"], inst_to_cell)
    nets = [{"net_id": d.get("net_id", d.get("net")), "terminals": list(d["terminals"])}
            for d in declared]
    inst_min = [{"instance_id": i["instance_id"], "device_cell": i["device_cell"]}
                for i in instances]

    tmpdir = tempfile.mkdtemp(prefix="klink_lvsdb_")
    gds_path = os.path.join(tmpdir, "snapshot.gds")
    client.call("layout.save_file", {"path": gds_path})
    out_path = lvsdb_path or str(Path(DEFAULT_SPEC_ROOT) / f"{cell}.lvsdb")
    try:
        ly = kdb.Layout(); ly.read(gds_path)
        top = ly.cell(cell)
        if top is None:
            return {"match": None, "problems": [
                f"cell {cell!r} not in snapshot; check the session/cell."]}
        dbu = ly.dbu

        def lyr(key):
            l, d = (int(x) for x in key.split("/"))
            idx = ly.find_layer(l, d)
            # a declared-but-undrawn layer (e.g. 106/105 here) must be a real
            # empty layer, not None -> make_layer(None) silently breaks LVS
            return idx if idx is not None else ly.layer(l, d)

        # per (cell, terminal) small box at each terminal point on a fresh
        # temp layer -> separates same-layer S/D; records its conductor layer
        temp_specs: Dict[tuple, tuple] = {}
        next_dt = 0
        def _temp_layer():
            nonlocal next_dt
            idx = ly.layer(64000, next_dt); next_dt += 1
            return idx
        half = int(round(0.5 / dbu))
        rows_by_key: Dict[tuple, list] = {}
        for r in table["rows"]:
            c = inst_to_cell.get(r["instance"])
            if c is None or not r.get("point_um"):
                continue
            rows_by_key.setdefault((c, r["terminal"]), []).append(r)
        for (c, term), rows in rows_by_key.items():
            tl = _temp_layer()
            cond_key = rows[0]["layer"]
            for r in rows:
                x = int(round(r["point_um"][0] / dbu)); y = int(round(r["point_um"][1] / dbu))
                top.shapes(tl).insert(kdb.Box(x - half, y - half, x + half, y + half))
            temp_specs[(c, term)] = (tl, cond_key)

        # layer NAMES must be slash-free or the written .lvsdb cannot be
        # read back ("101/0" breaks the parser); sanitize to a token.
        def _nm(s):
            return "".join(ch if ch.isalnum() else "_" for ch in str(s))

        L = kdb.LayoutVsSchematic(kdb.RecursiveShapeIterator(
            ly, top, lyr(connectivity.conductors[0])))
        cond_region = {k: L.make_layer(lyr(k), _nm("c_" + k)) for k in connectivity.conductors}
        via_region = {v[1]: L.make_layer(lyr(v[1]), _nm("v_" + v[1])) for v in connectivity.vias}
        for rg in cond_region.values():
            L.connect(rg)
        for a, v, b in connectivity.vias:
            L.connect(via_region[v])
            L.connect(cond_region[a], via_region[v])
            L.connect(via_region[v], cond_region[b])
        terminal_layers: Dict[str, Dict[str, Any]] = {}
        for (c, term), (tl, cond_key) in temp_specs.items():
            reg = L.make_layer(tl, _nm(f"t_{c}_{term}"))
            L.connect(reg)
            if cond_key in cond_region:
                L.connect(reg, cond_region[cond_key])
            terminal_layers.setdefault(c, {})[term] = reg

        register_device_extractors(L, device_terminals=device_terminals,
                                   terminal_layers=terminal_layers,
                                   layout=ly, top_cell=top)
        L.extract_netlist()
        ext = L.netlist()
        for c in device_terminals:                 # flatten device-cell shells
            cc = ext.circuit_by_name(c)
            if cc is not None:
                ext.flatten_circuit(cc)
        ref = build_reference_netlist({"instances": inst_min, "nets": nets},
                                      device_terminals, top_name=cell)
        L.reference = ref
        try:
            match = bool(L.compare(kdb.NetlistComparer()))
        except RuntimeError as exc:
            # KLayout's comparer can hit an internal assertion (dbNetlistCompareCore
            # bt_count != failed_match) on FULLY SYMMETRIC netlists -- e.g. a ring
            # of identical gates has automorphisms the backtracker cannot anchor.
            # Surface it as an instruction instead of a crash.
            raise RuntimeError(
                "KLayout netlist compare crashed internally -- this is known to "
                "happen on fully SYMMETRIC netlists (e.g. a ring of identical "
                "gates: the comparer has no anchor to break the automorphism). "
                "Break the symmetry (vary a device size, or add a distinct "
                f"port/pin) and re-run. Original error: {exc}") from exc
        out_abs = os.path.abspath(out_path)
        Path(out_abs).parent.mkdir(parents=True, exist_ok=True)
        L.write(out_abs)
        # auto-open the interactive Netlist/LVS browser (like DRC's marker
        # browser). Graceful: if the plugin isn't reloaded yet (no
        # view.show_lvsdb) or there is no view, the .lvsdb is still saved.
        shown = False
        try:
            client.call("view.show_lvsdb", {"path": out_abs})
            shown = True
        except Exception:
            shown = False
        topc = ext.circuit_by_name(cell)
        ndev = len(list(topc.each_device())) if topc else 0
        problems = [] if match else [
            "device .lvsdb mismatch: extracted devices/nets do not match the "
            "declared netlist; open the net reconcile for the differing nets."]
        return {"match": match, "device_count": ndev, "lvsdb_path": out_abs,
                "shown_in_browser": shown,
                "device_terminals": device_terminals, "problems": problems}
    finally:
        try:
            os.remove(gds_path)
        except OSError:
            pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass


def device_lvs_general(
    client: Any,
    cell: str,
    declared: Sequence[Mapping[str, Any]],
    table: Mapping[str, Any],
    instances: Sequence[Mapping[str, Any]],
    *,
    connectivity: Optional[ConnectivitySpec] = None,
    show: bool = False,
    spec_root: str = DEFAULT_SPEC_ROOT,
) -> Dict[str, Any]:
    """Device-level LVS via the GENERAL `lvs.run` server RPC (the engine
    lives in the thin plugin, not this domain). structdevice only builds
    the device-extraction config + reference netlist from its declared
    netlist + recipe terminal table and calls the general infra. Falls
    back to the MCP-side helper if the RPC isn't registered yet."""
    inst_to_cell = {i["instance_id"]: i["device_cell"] for i in instances}
    devices: Dict[str, Any] = {}
    for r in table["rows"]:
        c = inst_to_cell.get(r["instance"])
        if c is None or not r.get("point_um"):
            continue
        d = devices.setdefault(c, {"terminals": [], "terminal_layer": {},
                                   "terminal_points_um": {}})
        t = r["terminal"]
        if t not in d["terminals"]:
            d["terminals"].append(t)
        d["terminal_layer"][t] = r["layer"]
        d["terminal_points_um"].setdefault(t, []).append(
            [r["point_um"][0], r["point_um"][1]])
    for d in devices.values():
        d["terminals"] = sorted(d["terminals"])
    nets = [{"net_id": dn.get("net_id", dn.get("net")), "terminals": list(dn["terminals"])}
            for dn in declared]
    inst_min = [{"instance_id": i["instance_id"], "device_cell": i["device_cell"]}
                for i in instances]
    out_abs = os.path.abspath(str(Path(spec_root) / f"{cell}.lvsdb"))
    try:
        res = client.call("lvs.run", {
            "cell": cell,
            "conductors": list(connectivity.conductors),
            "vias": [list(v) for v in connectivity.vias],
            "devices": devices,
            "reference": {"netlist": {"instances": inst_min, "nets": nets}},
            "out_lvsdb": out_abs,
            "show": bool(show),
        })
        match = res.get("match")
        return {"match": match, "device_count": res.get("device_count"),
                "lvsdb_path": res.get("lvsdb_path"),
                "shown_in_browser": res.get("shown"),
                "problems": [] if match else [
                    "device-level LVS mismatch (general lvs.run); open the "
                    "net reconcile for the differing nets."]}
    except Exception as exc:
        # plugin not reloaded yet (no lvs.run) or call failed -> MCP-side
        # fallback (still produces match + .lvsdb, no auto-popup)
        fb = device_lvs_db(client, cell, declared, table, instances,
                           connectivity=connectivity,
                           lvsdb_path=out_abs if show else None)
        fb["fallback"] = f"lvs.run unavailable ({exc}); used MCP-side engine"
        return fb


def _device_terms_path(spec_root: str, cell: str) -> Path:
    return Path(spec_root) / f"{cell}.device_terms.json"


def save_device_terms(spec_root: str, cell: str,
                      device_terms: Mapping[str, Mapping[str, Sequence[float]]]) -> str:
    """Persist declared device terminal positions (P2: state on disk) so a
    later lvs_check -- any session, any agent -- can align declared<->layout
    devices BY POSITION without being handed coordinates."""
    p = _device_terms_path(spec_root, cell)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(device_terms, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return str(p)


def _load_device_terms(spec_root: str, cell: str):
    p = _device_terms_path(spec_root, cell)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def lvs_check(
    client: Any,
    cell: str,
    *,
    declared: Optional[Sequence[Mapping[str, Any]]] = None,
    spec_root: str = DEFAULT_SPEC_ROOT,
    terminal_provider: Any = None,
    device_cells: Any = None,
    placement: Optional[Mapping[str, Any]] = None,
    connectivity: Optional[ConnectivitySpec] = None,
    mode: str = "net",
    device_terms: Optional[Mapping[str, Mapping[str, Sequence[float]]]] = None,
) -> Dict[str, Any]:
    """One call: derive terminals, extract derived nets from the live
    layout, reconcile against declared nets, persist the report.

    Device identity is matched BY POSITION, not by instance order: KLayout does
    not return many instances in insertion order, so the extractor numbers
    layout devices differently than the declared netlist (a name-based match
    then compares the wrong devices -- false opens/shorts at scale, LESSONS #85).
    ``device_terms`` (``{instance_id: {terminal: [x, y]}}``) supplies the
    declared placement; if omitted it is read from
    ``<spec_root>/<cell>.device_terms.json`` (written by build_from_netlist).
    With neither present, falls back to name/order matching (fine for small
    cells where the orders coincide).

    ``mode``: ``"net"`` (default) = LVS-lite net reconcile only;
    ``"device"`` = ALSO device-level LVS (reference vs extracted netlist
    compared with KLayout-native NetlistComparer; result under
    ``device_lvs``); ``"lvsdb"`` = device-level LVS that ALSO writes a
    geometry-linked native ``.lvsdb`` (opens in the Netlist Browser with
    layout<->netlist cross-probing; result under ``device_lvs`` with
    ``lvsdb_path``). All fold into ``ok``."""

    if declared is None:
        declared = load_declared_nets(spec_root, cell)
    if not declared:
        return {
            "ok": False,
            "problems": [
                f"no declared nets for cell {cell!r}; declare them first "
                "(structdevice.declare_nets after SENDs, or pass "
                "declared inline). LVS needs an intent to audit against."
            ],
        }
    if terminal_provider is None:
        return {"ok": False, "problems": [
            "no terminal provider; klink ships none. LVS must know how to read "
            "each device's terminals."],
            "next_action": "pass terminal_provider -- the build path uses "
            "recipes.geom_terminal_provider(device_geom) + placement=...; an "
            "interactive flow injects a device recipe from "
            "your recipes + device_cells=..."}
    if placement is not None:
        # BUILD path: recipe-free. Devices are matched to the known placement BY
        # POSITION (their PCell-variant names are decoupled from device keys);
        # terminals come from harvested DATA, not from re-reading geometry.
        collected = collect_built_devices(client, cell, placement, terminal_provider)
    elif device_cells is not None:
        # INTERACTIVE path: derive terminals from live hand-drawn geometry.
        collected = collect_placed_terminals(
            client, cell, terminal_provider=terminal_provider,
            device_cells=device_cells)
    else:
        return {"ok": False, "problems": [
            "lvs_check needs either placement=... (build path) or device_cells=... "
            "(interactive path) to locate the devices."],
            "next_action": "pass placement (instance_id -> (device_key, dx, dy)) "
            "for a built cell, or device_cells for a hand-drawn cell."}
    if connectivity is None:
        return {"ok": False, "problems": [
            "no connectivity given; klink ships no process -- LVS needs YOUR "
            "process's conductor layers + via stacks."],
            "next_action": "pass connectivity (e.g. profile.connectivity_spec() "
            "from a profile in your pdk.py), or call this MCP tool "
            "with conductors=[...] and vias=[...]."}
    extractor = extract_live_connectivity(client, cell, spec=connectivity)
    table = extractor.terminal_net_table(collected["placed"])
    # Align declared<->layout device names BY POSITION (scale-robust; see #85).
    align_problems: List[str] = []
    if device_terms is None:
        device_terms = _load_device_terms(spec_root, cell)
    if device_terms:
        layout_term_pos: Dict[str, Dict[str, Any]] = {}
        for t in collected["placed"]:
            layout_term_pos.setdefault(t.instance, {})[t.terminal] = (t.point_um[0], t.point_um[1])
        declared, align_problems, _n_aligned = align_declared_by_position(
            declared, device_terms, layout_term_pos)
    report = reconcile(declared_nets_from_dicts(declared), table)
    out = {
        "ok": report["ok"] and not collected["problems"] and not align_problems,
        "cell": cell,
        "instances": collected["instances"],
        "matches": report["matches"],
        "problems": collected["problems"] + align_problems + table["problems"]
        + report["problems"],
        "infos": report["infos"],
        "derived_nets": extractor.nets(),
        "terminal_table": table["rows"],
        "missing_layers": extractor.missing_layers,
    }
    if mode in ("device", "both", "lvsdb"):
        # device-level LVS via the GENERAL lvs.run infra (show=true pops the
        # interactive .lvsdb browser); structdevice only supplies config.
        dev = device_lvs_general(client, cell, declared, table,
                                 collected["instances"], connectivity=connectivity,
                                 show=(mode == "lvsdb"), spec_root=spec_root)
        out["device_lvs"] = dev
        if dev.get("match") is False:
            out["ok"] = False
        out["problems"] = out["problems"] + dev.get("problems", [])
    path = Path(spec_root) / f"{cell}.lvs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    out["report_path"] = str(path)
    out["next_action"] = (
        f"structdevice.spec_write {{cell: '{cell}', layer_roles: ...}}"
        if out["ok"] else
        "relay the problems to the user verbatim; after the layout or "
        "declaration is fixed, call structdevice.lvs_check again "
        "(or structdevice.connect_nets to wire pending nets)"
    )
    return out

"""SEND-driven net intent: clicks -> ports -> auto-named nets -> route style.

The conversation layer (agent) parses user utterances; this module only
takes structured inputs. Pipeline:

    interaction.selection.recent  ->  resolve_selection_to_port(...)
      -> pair of port names       ->  auto_net_name(...)
      -> RouteStyle (typed)       ->  assign_net(client, ...)
      -> route_gdsfactory_ports(source=[...], target=[...], **style kwargs)

Resolution rules (deterministic, ambiguity is explicit):

1. If the SEND selection contains a klink Port PCell marker, that marker's
   name wins (exact identity, the designed click target).
2. Otherwise the selection bbox center is matched to the nearest harvested
   port within `tolerance_um`; if the nearest and second-nearest are too
   close to call, the result is flagged ambiguous instead of guessed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from klink.routing.geom.bends import euler_setback_ratio, round_manhattan_corners
from klink.routing.geom.geometry import (
    crossing_pairs,
    expand_bbox,
    route_hits_bboxes,
)


# ----------------------------------------------------------------------
# Route style: typed "how to connect" parameters (agent parses language)
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class RouteStyle:
    """Per-net routing style mapped onto klink's gdsfactory bridge kwargs.

    Parametric v1: width/radius/bend/separation/layer. Named PDK
    cross-sections (rib etc.) become possible once a real gf PDK package
    is registered; until then they are rejected explicitly.
    """

    width_um: float | None = None        # None -> port width
    radius_um: float | None = None       # None -> gf cross-section default
    bend: str = "euler"                  # "euler" | "circular"
    separation_um: float = 3.0
    route_layer: str | None = None       # None -> caller default
    router: str = "bundle"               # any GF_ROUTERS key (bundle/single/
                                         # sbend/all_angle/dubins/astar/...)
    auto_taper: bool = False             # width transitions at ports
    waypoints_um: tuple = ()             # ((x, y), ...) pin the route path
    steps: tuple = ()                    # ({"dx": ..}/{"dy": ..}, ...)
    named_cross_section: str | None = None  # requires a registered PDK

    def validate(self) -> list[str]:
        from klink.routing.backends.gdsfactory.gdsfactory_backend import (
            GF_ROUTERS, router_params)

        problems = []
        if self.bend != "euler":
            problems.append(
                f"bend {self.bend!r} not supported yet: the klink gf backend "
                "does not expose route_bundle's bend parameter (v2 item); "
                "euler (gf default) only for now"
            )
        if self.router not in GF_ROUTERS:
            problems.append(
                f"unknown router: {self.router!r}; available: "
                + ", ".join(sorted(GF_ROUTERS)))
        else:
            honored = router_params(self.router)
            for param, value in (("waypoints_um", self.waypoints_um),
                                 ("steps", self.steps)):
                if value and param not in honored:
                    takers = sorted(r for r in GF_ROUTERS
                                    if param in router_params(r))
                    problems.append(
                        f"router {self.router!r} does not honor {param}; "
                        f"use one of: {', '.join(takers)}")
        if self.named_cross_section is not None:
            problems.append(
                "named cross-sections need a registered gdsfactory PDK; "
                "use parametric width/radius/layer for now"
            )
        if self.width_um is not None and self.width_um <= 0:
            problems.append("width_um must be positive")
        if self.radius_um is not None and self.radius_um <= 0:
            problems.append("radius_um must be positive")
        return problems

    def group_key(self) -> tuple:
        """Nets sharing a key may be routed in the same gf call.

        Nets with pinned paths (waypoints/steps) never share a call: the
        pin applies to the whole gf call, so each pinned net routes alone.
        """
        return (
            self.width_um, self.radius_um, self.bend, self.separation_um,
            self.route_layer, self.router, self.auto_taper,
            self.waypoints_um, self.steps,
        )


# ----------------------------------------------------------------------
# Auto net naming
# ----------------------------------------------------------------------

def auto_net_name(port_a: str, port_b: str, existing: Sequence[str] = ()) -> str:
    """Deterministic, readable, collision-free net name from endpoints."""
    left, right = sorted([str(port_a), str(port_b)])
    base = f"n_{left}__{right}"
    name = base
    suffix = 2
    taken = set(existing)
    while name in taken:
        name = f"{base}_{suffix}"
        suffix += 1
    return name


# ----------------------------------------------------------------------
# SEND selection -> port resolution
# ----------------------------------------------------------------------

@dataclass
class PortResolution:
    port_name: str | None
    method: str                    # "marker" | "nearest" | "none"
    distance_um: float | None = None
    ambiguous_with: str | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.port_name is not None and self.ambiguous_with is None


def _bbox_center_um(item: dict, dbu: float) -> tuple[float, float] | None:
    bbox = item.get("bbox_dbu")
    if not bbox or len(bbox) < 4:
        return None
    return ((bbox[0] + bbox[2]) / 2.0 * dbu, (bbox[1] + bbox[3]) / 2.0 * dbu)


def _is_port_marker_item(item: dict) -> bool:
    """A selected klink Port PCell instance (the designed click target)."""
    if not item.get("is_cell_inst"):
        return False
    cell_name = str(item.get("cell") or item.get("child") or "")
    return "Port" in cell_name or str(item.get("name", "")).startswith("KLINK")


def resolve_selection_to_port(
    selection: dict,
    ports: Sequence[dict],
    *,
    dbu: float = 0.001,
    tolerance_um: float = 50.0,
    ambiguity_ratio: float = 0.6,
) -> PortResolution:
    """Resolve one SEND selection record to a harvested port.

    `selection` is an interaction-context record (selection_sent event dict
    with `items`). `ports` are port dicts with `name` and `center_um` (as
    returned by port.list after harvesting).
    """
    items = list(selection.get("items") or [])
    if not items or not ports:
        return PortResolution(None, "none", detail="empty selection or no ports")

    # Rule 1: a selected Port marker instance wins; match marker position
    # to the exactly-coincident port.
    marker_items = [i for i in items if _is_port_marker_item(i)]
    candidates = marker_items if marker_items else items

    # Rule 2: nearest harvested port to the (first) candidate bbox center.
    center = None
    for item in candidates:
        center = _bbox_center_um(item, dbu)
        if center is not None:
            break
    if center is None:
        return PortResolution(None, "none", detail="selection has no bbox")

    scored = []
    for port in ports:
        c = port.get("center_um") or [0.0, 0.0]
        dist = ((c[0] - center[0]) ** 2 + (c[1] - center[1]) ** 2) ** 0.5
        scored.append((dist, str(port.get("name", ""))))
    scored.sort()
    best_dist, best_name = scored[0]
    method = "marker" if marker_items else "nearest"

    if best_dist > tolerance_um:
        return PortResolution(
            None, method,
            distance_um=round(best_dist, 3),
            detail=f"nearest port {best_name} is {best_dist:.1f} um away "
                   f"(tolerance {tolerance_um} um)",
        )
    if len(scored) > 1 and method == "nearest":
        second_dist, second_name = scored[1]
        if second_dist > 0 and best_dist / second_dist > ambiguity_ratio:
            return PortResolution(
                best_name, method,
                distance_um=round(best_dist, 3),
                ambiguous_with=second_name,
                detail=f"{best_name} ({best_dist:.1f} um) vs "
                       f"{second_name} ({second_dist:.1f} um) too close to call",
            )
    return PortResolution(best_name, method, distance_um=round(best_dist, 3))


# ----------------------------------------------------------------------
# Net assignment on live ports
# ----------------------------------------------------------------------

@dataclass
class NetIntent:
    net: str
    port_a: str
    port_b: str
    style: RouteStyle = field(default_factory=RouteStyle)


def assign_net(client, cell: str, intent: NetIntent) -> dict:
    """Write the net onto both live Port markers via port.update."""
    problems = intent.style.validate()
    if problems:
        raise ValueError("invalid route style: " + "; ".join(problems))
    for name in (intent.port_a, intent.port_b):
        client.call("port.update", {"cell": cell, "name": name, "net": intent.net})
    return {"net": intent.net, "ports": [intent.port_a, intent.port_b]}


# ----------------------------------------------------------------------
# SEND gesture extraction (exact identity from Port PCell params)
# ----------------------------------------------------------------------

def port_names_from_send(selection: dict) -> list[str]:
    """Extract exact port names from Port PCell markers inside one SEND.

    SEND events carry full PCell params for selected Port markers, so when
    the user clicks/frames markers no geometric guessing is needed at all.
    """
    names = []
    for item in selection.get("items") or []:
        pcell = item.get("pcell") or {}
        params = pcell.get("params") or {}
        if pcell.get("lib") == "klink_port" and params.get("port_name"):
            names.append(str(params["port_name"]))
    return names


def pairs_from_sends(selections: Sequence[dict]) -> tuple[list[tuple[str, str]], list[str]]:
    """Turn SEND records into port pairs. Returns (pairs, problems).

    Gesture rules:
    - a SEND containing exactly TWO port markers = one pair ("frame two");
    - SENDs containing exactly ONE marker pair up consecutively;
    - anything else is reported as a problem with the fix spelled out.
    """
    pairs: list[tuple[str, str]] = []
    pending: str | None = None
    problems: list[str] = []
    for sel in selections:
        names = port_names_from_send(sel)
        sel_id = str(sel.get("id") or "?")
        if len(names) == 2:
            pairs.append((names[0], names[1]))
        elif len(names) == 1:
            if pending is None:
                pending = names[0]
            else:
                pairs.append((pending, names[0]))
                pending = None
        else:
            problems.append(
                f"{sel_id}: contains {len(names)} port markers; select exactly "
                "one or two klink Port markers (the labeled triangles), press "
                "SEND, and call this again"
            )
    if pending is not None:
        problems.append(
            f"unpaired port {pending!r}: send one more selection with its "
            "partner port marker"
        )
    return pairs, problems


# ----------------------------------------------------------------------
# Persistent net table (.klink/specs) — state lives on disk, not in the
# agent's memory, so any agent can re-route later with one call.
# ----------------------------------------------------------------------

def spec_path_for(cell: str, spec_root: str | None = None):
    from pathlib import Path

    root = Path(spec_root) if spec_root else Path(".klink") / "specs"
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in cell)
    return root / f"{safe}.nets.json"


@dataclass
class NetTable:
    cell: str
    tags: dict[str, str] = field(default_factory=dict)
    entries: list[dict] = field(default_factory=list)  # {net, a, b, style{}}
    #: how ports are re-derived from live instances. Empty -> waveguide-stub
    #: convention (needs wg_layer/stub_size_um); {"mode": "gf_templates",
    #: "templates": {...}, "route_layer": ...} -> imported gf port tables
    #: (see gf_import.import_gf_component).
    harvest: dict = field(default_factory=dict)

    def net_names(self) -> list[str]:
        return [e["net"] for e in self.entries]

    def nets_for_harvest(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for e in self.entries:
            out[e["a"]] = e["net"]
            out[e["b"]] = e["net"]
        return out

    def add_pair(self, a: str, b: str, style: "RouteStyle | None" = None) -> dict:
        ports_seen = {p for e in self.entries for p in (e["a"], e["b"])}
        for port in (a, b):
            if port in ports_seen:
                raise ValueError(
                    f"port {port!r} is already connected (net table {self.cell}); "
                    "to change it, remove its entry first or re-pair explicitly"
                )
        entry = {
            "net": auto_net_name(a, b, existing=self.net_names()),
            "a": a,
            "b": b,
            "style": _style_to_dict(style or RouteStyle()),
        }
        self.entries.append(entry)
        return entry

    def save(self, spec_root: str | None = None) -> str:
        import json

        path = spec_path_for(self.cell, spec_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "cell": self.cell, "tags": self.tags,
                   "entries": self.entries, "harvest": self.harvest}
        path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        return str(path)

    @classmethod
    def load(cls, cell: str, spec_root: str | None = None) -> "NetTable | None":
        import json

        path = spec_path_for(cell, spec_root)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(cell=data["cell"], tags=dict(data.get("tags") or {}),
                   entries=list(data.get("entries") or []),
                   harvest=dict(data.get("harvest") or {}))


def _style_to_dict(style: RouteStyle) -> dict:
    return {
        "width_um": style.width_um,
        "radius_um": style.radius_um,
        "bend": style.bend,
        "separation_um": style.separation_um,
        "route_layer": style.route_layer,
        "router": style.router,
        "auto_taper": style.auto_taper,
        "waypoints_um": [list(p) for p in style.waypoints_um],
        "steps": [dict(s) for s in style.steps],
    }


def _style_from_dict(data: dict) -> RouteStyle:
    kwargs = {k: data[k] for k in _style_to_dict(RouteStyle()) if k in data}
    # JSON round-trips tuples as lists; the frozen dataclass hashes them.
    kwargs["waypoints_um"] = tuple(tuple(p) for p in kwargs.get("waypoints_um") or ())
    kwargs["steps"] = tuple(
        tuple(sorted(s.items())) if isinstance(s, dict) else tuple(s)
        for s in kwargs.get("steps") or ())
    return RouteStyle(**kwargs)


# ----------------------------------------------------------------------
# Auto-tagging: any child cell with stub ports gets harvested; no config
# needed from the calling agent.
# ----------------------------------------------------------------------

def auto_tags(client, cell: str, *, wg_layer: str,
              stub_size_um: float) -> dict[str, str]:
    from .blackbox import stub_template

    dbu = float(client.layout_info().get("dbu", 0.001))
    result = client.call("instance.query", {"parent": cell, "limit": 5000})
    children = sorted({str(i.get("child") or "") for i in result.get("instances", []) if i.get("child")})
    tags: dict[str, str] = {}
    for child in children:
        stubs = stub_template(client, child, wg_layer=wg_layer,
                              stub_size_um=stub_size_um, dbu=dbu)
        if stubs:
            # Deterministic, collision-free, readable-enough tag.
            tag = "".join(ch for ch in child if ch.isalnum())[:24] or "BB"
            base = tag
            n = 2
            while tag in tags.values():
                tag = f"{base}{n}"
                n += 1
            tags[child] = tag
    return tags


# ----------------------------------------------------------------------
# One-call orchestrators (the foolproof surface for MCP tools/buttons)
# ----------------------------------------------------------------------

def infer_tags_from_markers(
    client,
    cell: str,
    *,
    port_layer: str = "999/99",
    wg_layer: str,
    stub_size_um: float,
) -> dict[str, str]:
    """Reconstruct the child->tag map from EXISTING Port markers.

    Existing marker names follow {tag}{ordinal}_{stubIndex}; the first
    instance of each child has ordinal 0, so the marker sitting on its
    first stub is named {tag}0_0. Matching that marker by exact position
    recovers the tag, keeping re-harvest names identical to what the user
    sees and SENDs reference.
    """
    from .blackbox import _apply_trans, stub_template

    ports = client.call("port.list", {"cell": cell, "layer": port_layer}).get("ports", [])
    if not ports:
        return {}
    dbu = float(client.layout_info().get("dbu", 0.001))
    result = client.call("instance.query", {"parent": cell, "limit": 5000})

    def _nearest_marker(point):
        best = None
        best_d = 0.01  # um tolerance
        for p in ports:
            c = p.get("center_um") or [0, 0]
            d = max(abs(c[0] - point[0]), abs(c[1] - point[1]))
            if d < best_d:
                best, best_d = str(p.get("name", "")), d
        return best

    tags: dict[str, str] = {}
    templates: dict[str, list] = {}
    for inst in result.get("instances", []):
        child = str(inst.get("child") or "")
        if not child or child in tags:
            continue
        if child not in templates:
            templates[child] = stub_template(
                client, child, wg_layer=wg_layer,
                stub_size_um=stub_size_um, dbu=dbu,
            )
        stubs = templates[child]
        if not stubs:
            continue
        center = _apply_trans(stubs[0]["center_dbu"], inst.get("trans") or {}, dbu)
        name = _nearest_marker(center)
        if name and name.endswith("0_0"):
            tags[child] = name[: -len("0_0")]
    return tags


def connect_and_route(
    client,
    *,
    sends: Sequence[dict],
    cell: str | None = None,
    style: RouteStyle | None = None,
    tags: dict[str, str] | None = None,
    port_layer: str = "999/99",
    wg_layer: str | None = None,
    stub_size_um: float | None = None,
    route_layer: str | None = None,
    spec_root: str | None = None,
) -> dict:
    """SEND records -> pairs -> persisted net table -> harvest -> route.

    Everything an agent previously had to orchestrate by hand, in one call:
    gesture parsing, auto net naming, idempotent re-harvest from live
    instance positions, style-grouped routing, on-disk persistence.
    Problems come back as instructions, never silent guesses.
    """
    pairs, problems = pairs_from_sends(sends)
    if problems:
        return {"ok": False, "problems": problems, "pairs": pairs}
    if not pairs:
        return {"ok": False, "problems": [
            "no port pairs found in the given SENDs; select one or two klink "
            "Port markers per SEND and try again"
        ]}

    cells = {str(s.get("cell") or "") for s in sends if s.get("cell")}
    if cell is None:
        if len(cells) != 1:
            return {"ok": False, "problems": [
                f"SENDs span cells {sorted(cells)}; pass cell= explicitly"
            ]}
        cell = next(iter(cells))

    table = NetTable.load(cell, spec_root) or NetTable(cell=cell)
    if route_layer is None:
        route_layer = table.harvest.get("route_layer") or None
    if not route_layer:
        return {"ok": False, "problems": [
            "connect needs route_layer='L/D' (the layer to route on for "
            "YOUR process; klink ships no default)"]}
    if tags:
        table.tags.update(tags)
    if not table.tags and table.harvest.get("mode") != "gf_templates":
        # Stub convention: existing markers define the names the user's
        # SENDs reference; only auto-tag fresh when no markers exist yet.
        if not wg_layer or not stub_size_um:
            return {"ok": False, "problems": [
                "this cell has no tag map yet; pass wg_layer='L/D' + "
                "stub_size_um (YOUR PDK's stub port convention) so ports "
                "can be harvested, or import the circuit via "
                "gf_import.import_gf_component first"]}
        table.tags = infer_tags_from_markers(
            client, cell, port_layer=port_layer,
            wg_layer=wg_layer, stub_size_um=stub_size_um)
        if not table.tags:
            table.tags = auto_tags(
                client, cell, wg_layer=wg_layer, stub_size_um=stub_size_um)
        if not table.tags:
            return {"ok": False, "problems": [
                f"no blackbox children with stub ports found in {cell!r}; "
                "is this the right cell, and do its components follow the "
                "waveguide-stub port convention?"
            ]}
    new_entries = []
    for a, b in pairs:
        new_entries.append(table.add_pair(a, b, style))

    result = _harvest_and_route(client, table, port_layer=port_layer,
                                route_layer=route_layer,
                                wg_layer=wg_layer, stub_size_um=stub_size_um)
    if not result["ok"]:
        # Do not poison the persisted table with a failed attempt.
        result.setdefault("problems", []).append(
            "the new pairs were NOT persisted because routing/harvest "
            "failed; fix the issue and call connect again"
        )
        return {"ok": False, "cell": cell,
                "new_nets": [e["net"] for e in new_entries], **result}
    spec_path = table.save(spec_root)
    return {"ok": True, "cell": cell, "spec_path": spec_path,
            "new_nets": [e["net"] for e in new_entries], **result}


def reroute(
    client,
    *,
    cell: str,
    port_layer: str = "999/99",
    wg_layer: str | None = None,
    stub_size_um: float | None = None,
    route_layer: str | None = None,
    spec_root: str | None = None,
) -> dict:
    """Re-harvest from live positions and re-route the persisted net table.

    Call after the user moves components. Requires only the cell name; all
    intent state comes from the spec file. Stub-convention tables need
    wg_layer/stub_size_um; gf-imported tables carry their own templates and
    default route_layer.
    """
    table = NetTable.load(cell, spec_root)
    if table is None or not table.entries:
        return {"ok": False, "problems": [
            f"no persisted net table for cell {cell!r} "
            f"(expected {spec_path_for(cell, spec_root)}); run connect first"
        ]}
    if route_layer is None:
        route_layer = table.harvest.get("route_layer") or None
    if not route_layer:
        return {"ok": False, "problems": [
            "reroute needs route_layer='L/D' (the layer to route on for "
            "YOUR process; klink ships no default)"]}
    result = _harvest_and_route(client, table, port_layer=port_layer,
                                route_layer=route_layer,
                                wg_layer=wg_layer, stub_size_um=stub_size_um)
    return {"ok": result["ok"], "cell": cell,
            "spec_path": str(spec_path_for(cell, spec_root)), **result}


def _harvest_and_route(client, table: NetTable, *, port_layer: str,
                       route_layer: str, wg_layer: str | None = None,
                       stub_size_um: float | None = None) -> dict:
    from klink.routing.backends.gdsfactory.gdsfactory_ports import route_gdsfactory_ports

    from .blackbox import harvest_instance_ports, mark_ports

    # Compute the harvest FIRST (pure), validate the table against it, and
    # only then touch the layout — a failed connect must not delete or
    # rename the user's visible markers.
    is_gf_mode = table.harvest.get("mode") == "gf_templates"
    if is_gf_mode:
        from .gf_import import harvest_gf_template_ports

        # Always the FULL set here (mark_policy="all"): internal bookkeeping
        # below (missing-port validation, abutment detection) needs every
        # harvested port, not just the ones already in a net. Visual
        # filtering to only in-net ports happens right before mark_ports,
        # below — gf components routinely carry duplicate/unused access
        # ports (e.g. a bond pad's 4 edges + a redundant center point) that
        # would otherwise paint one marker per port once wired.
        marks = harvest_gf_template_ports(
            client, table.cell, tags=table.tags,
            templates=table.harvest.get("templates") or {},
            nets=table.nets_for_harvest(), port_layer=port_layer,
            mark_policy="all",
        )
    else:
        if not wg_layer or not stub_size_um:
            return {"ok": False, "problems": [
                "this net table uses the waveguide-stub port convention; "
                "pass wg_layer='L/D' and stub_size_um (YOUR PDK's stub "
                "marker convention — klink ships no default)"]}
        marks = harvest_instance_ports(
            client, table.cell, tags=table.tags,
            nets=table.nets_for_harvest(), port_layer=port_layer,
            wg_layer=wg_layer, stub_size_um=stub_size_um,
        )
    harvested = {m["name"] for m in marks}
    missing = [p for e in table.entries for p in (e["a"], e["b"])
               if p not in harvested]
    if missing:
        return {"ok": False, "problems": [
            f"net table references ports {missing} that the harvest does not "
            f"produce in {table.cell!r} (component deleted/renamed, or marker "
            f"names drifted from the tag map {table.tags}); re-harvest with "
            f"the intended tags or re-connect those pairs"
        ], "harvested_ports": len(marks)}
    client.call("port.delete_all", {"cell": table.cell, "layer": port_layer})
    # Stub convention (blackbox harvest): keep marking EVERY harvested port —
    # SEND point-selection depends on the full marker set being on screen.
    # gf-template mode: only ports actually in a net stay visible once
    # routed; the full template is untouched on disk (re-import shows all).
    visible_marks = [m for m in marks if m.get("net")] if is_gf_mode else marks
    mark_ports(client, visible_marks)

    # Ports connected by ABUTMENT (gf `connect()` snapping, or the user
    # dragging a component flush) coincide and face each other: the net is
    # already made by geometry, so routing it would only produce a loop.
    # Skip those; when a drag separates them again they route normally.
    mark_by_name = {m["name"]: m for m in marks}
    dbu = float(client.layout_info().get("dbu", 0.001))
    tol = 2.0 * dbu

    def _abutted(entry) -> bool:
        a = mark_by_name.get(entry["a"])
        b = mark_by_name.get(entry["b"])
        if not a or not b:
            return False
        ca = a.get("center_um")
        cb = b.get("center_um")
        if not ca or not cb:
            return False
        if abs(ca[0] - cb[0]) > tol or abs(ca[1] - cb[1]) > tol:
            return False
        gap = (float(a.get("orientation", 0.0)) - float(b.get("orientation", 0.0))) % 360.0
        return abs(gap - 180.0) <= 1.0

    abutted = [e for e in table.entries if _abutted(e)]
    to_route = [e for e in table.entries if not _abutted(e)]

    # Group nets by style so mixed styles each get a correct gf call.
    groups: dict[tuple, list[dict]] = {}
    for entry in to_route:
        key = _style_from_dict(entry.get("style") or {}).group_key()
        groups.setdefault(key, []).append(entry)

    # Device bboxes feed klink's OWN post-route check only. Do NOT hand them
    # to kfactory's route_bundle: its `bboxes` are escape-length heuristics,
    # not obstacle avoidance, and proven live they wrap collinear chains
    # (GC -> device -> GC on one axis) into giant loops around the union box.
    # PER LAYER: a heater's metal overhang must not count as an obstacle for
    # the optical route underneath it, and vice versa.
    check_layers = {route_layer}
    for entry in table.entries:
        style_layer = (entry.get("style") or {}).get("route_layer")
        if style_layer:
            check_layers.add(str(style_layer))
    device_bboxes = _device_layer_bboxes_um(
        client, table.cell, table.tags, sorted(check_layers))

    def _call_route(entries, *, output_mode, clear):
        style_obj = _style_from_dict(entries[0].get("style") or {})
        kwargs = route_kwargs_for(style_obj, default_route_layer=route_layer)
        report = route_gdsfactory_ports(
            client, table.cell,
            port_layer=port_layer,
            output_mode=output_mode,
            clear=clear,
            # Crossings are judged ONCE below over ALL routes; the per-call
            # check only sees its own style group and would both miss
            # cross-group conflicts and abort mid-write on same-group ones.
            allow_crossing=True,
            source=[e["a"] for e in entries],
            target=[e["b"] for e in entries],
            pair_by="order",
            **kwargs,
        )
        group_routes = report.get("routes", [])
        # The bridge numbers route_ids per CALL (gf_route_0..) and its
        # angle/cluster partition REORDERS routes, so match each route back
        # to its entry by the source port name (we passed source=[e["a"]]),
        # then rename to the net so ids stay unique across style groups and
        # reports speak the user's language.
        route_by_source = {str(r.get("source", "")): r for r in group_routes}
        for entry in entries:
            route = route_by_source.get(entry["a"])
            if route is not None:
                route["route_id"] = entry["net"]
                route["net"] = entry["net"]
        return report

    def _group_layer(entries) -> str:
        style_layer = (entries[0].get("style") or {}).get("route_layer")
        return str(style_layer or route_layer)

    # --- phase 1: PLAN everything dry (nothing written yet). A gf failure
    # must NOT abort the run — it is exactly the signal that klink's own
    # obstacle-aware planning has to take over for that net.
    entry_by_net = {e["net"]: e for e in to_route}
    detours: dict[str, dict] = {}   # net -> {points, width, layer}
    needs_detour: dict[str, str] = {}   # net -> gf failure reason
    failed: dict[str, str] = {}         # net -> final instructive problem
    routes = []
    for entries in groups.values():
        try:
            routes.extend(_call_route(
                entries, output_mode="dry_run", clear=False).get("routes", []))
        except ValueError:
            # One bad net must not take its whole style group down: retry
            # each net alone, and remember the ones gf cannot build at all.
            for entry in entries:
                try:
                    routes.extend(_call_route(
                        [entry], output_mode="dry_run",
                        clear=False).get("routes", []))
                except ValueError as exc:
                    needs_detour[entry["net"]] = str(exc)

    # --- phase 2: gf routers do NOT avoid obstacles; klink plans real ----
    # detours. Two triggers: gf could not build the net at all (phase 1
    # failure), or its route cuts a FOREIGN device's footprint on its own
    # layer. Both are re-planned with klink's visibility router around the
    # device footprints and re-routed with the resulting waypoints (only
    # routers that honor waypoints_um can be fixed this way).
    from klink.routing.backends.gdsfactory.gdsfactory_backend import router_params
    from klink.routing.geom.constraints import port_launch_point
    from klink.routing.geom.geometric import route_points_geometric

    hit_nets = {str(h.get("route_id", ""))
                for h in _route_device_hits(routes, device_bboxes,
                                            entry_by_net=entry_by_net,
                                            mark_by_name=mark_by_name)}
    for net in sorted(hit_nets | set(needs_detour)):
        entry = entry_by_net.get(net)
        if entry is None or net in detours:
            continue
        style_obj = _style_from_dict(entry.get("style") or {})
        mark_a = mark_by_name.get(entry["a"])
        mark_b = mark_by_name.get(entry["b"])
        fixable = ("waypoints_um" in router_params(style_obj.router)
                   and not style_obj.waypoints_um and mark_a and mark_b)
        if not fixable:
            if net in needs_detour:
                failed[net] = (
                    "net %r: gdsfactory router %r failed (%s) and cannot "
                    "take klink waypoints; re-place the components or switch "
                    "the net's style to a waypoint-capable router (bundle/"
                    "electrical/single)" % (net, style_obj.router,
                                            needs_detour[net]))
            continue  # hit-only & unfixable: the verdict below reports it
        layer = str(style_obj.route_layer or route_layer)
        width = float(mark_a.get("width_um", 0.5) or 0.5)
        is_optical = _is_optical_net(mark_a, mark_b)
        bend_radius = _optical_bend_radius_um(style_obj) if is_optical else 0.0
        clearance = max(width, 1.0)
        if is_optical and math.isfinite(bend_radius):
            # An euler bend needs setback = ratio * R of straight run at
            # EACH corner (vs. exactly R for a circular fillet) -- give the
            # visibility plan that much extra room so it doesn't hug a
            # device edge closer than the bend can actually round. When the
            # radius itself is unbounded (no style/PDK source), the corner
            # clamp inside round_manhattan_corners alone decides the fit.
            clearance += bend_radius * euler_setback_ratio()
        # `grow` mirrors route_points_geometric's own internal expansion
        # margin (route half-width + safe_distance) -- obstacles below are
        # raw/unexpanded, exactly like the electrical branch, so the notch
        # sizing has to compensate for that same uniform inflation or the
        # port's own launch point ends up re-swallowed by the "open" side.
        grow = width / 2.0 + clearance
        if is_optical:
            # Optical keep-out is LAYER-INDEPENDENT (the device's whole
            # body), with an entry notch at each own endpoint's port face
            # instead of the old blanket own-device exemption.
            full_entries = device_bboxes.get("__full__", [])
            owners = {e["owner"] for e in full_entries}
            own = {_owner_of(entry["a"], owners), _owner_of(entry["b"], owners)}
            obstacles = [
                [round(float(v), 6) for v in bbox]
                for bbox in _optical_keepouts(
                    full_entries, own, mark_a, mark_b,
                    notch_halfwidth=grow + width / 2.0 + 0.2,
                    notch_depth=grow + 0.2)
            ]
        else:
            layer_entries = device_bboxes.get(layer, [])
            owners = {e["owner"] for e in layer_entries}
            own = {_owner_of(entry["a"], owners), _owner_of(entry["b"], owners)}
            # Rounded to kill float noise from the rotation/translation
            # pipeline (real coordinates are all multiples of the layout's
            # dbu, far coarser than 1e-6 um): un-rounded bboxes proven live
            # to nudge the visibility graph into picking a spurious near-tie
            # candidate node, producing an extra sub-clearance corner that
            # gf's router then silently fails to place any geometry for
            # (empty writeback, no error) instead of a clean detour.
            obstacles = [[round(float(v), 6) for v in e["bbox"]]
                         for e in layer_entries if e["owner"] not in own]
        # klink's own routes don't avoid each other by construction (each gf
        # call only sees its own style group); a detour planned blind to
        # OTHER already-routed nets on the same layer would just trade a
        # device hit for a route-vs-route crossing (proven live: the clean
        # detour under TOPS ran straight through the TOPS0_1->GC1_0 arm).
        # Treat every other net's already-planned backbone as a keep-out
        # strip: pad each raw (zero-width) segment by ONLY the other route's
        # own half-width here, same as a device's real footprint bbox below
        # -- route_points_geometric's own safe_distance_um already adds OUR
        # own half-width + safety margin on top of EVERY obstacle uniformly,
        # so padding by more than the other route's physical footprint here
        # double-counts that margin and can wrongly trap the launch point
        # (proven live: start point landed "inside" a stacked-margin box).
        for other in routes:
            if str(other.get("layer", "")) != layer:
                continue
            if str(other.get("net", "")) == net:
                continue
            other_width = float(other.get("width_um", 0.5) or 0.5)
            obstacles.extend(
                [round(float(v), 6) for v in bbox]
                for bbox in _route_segment_bboxes_um(
                    other.get("points_um") or [], other_width / 2.0))
        try:
            path = route_points_geometric(
                port_launch_point(mark_a),
                port_launch_point(mark_b),
                obstacle_bboxes=obstacles,
                route_width_um=width,
                safe_distance_um=clearance,
            )
        except ValueError as exc:
            failed[net] = (
                "net %r has no clear corridor around the placed components "
                "(%s); move the components apart" % (net, exc))
            continue
        # Written as a plain klink path, NOT handed to gdsfactory's
        # route_bundle/route_single: proven live, both gf strategies
        # mishandle a lone, multi-corner, explicitly waypointed pair in this
        # gdsfactory version -- route_bundle inserts long spurious
        # backtracking loops (still connects, but wanders into other nets),
        # and route_single's place_manhattan can silently under-build the
        # route (reports "ok" while writing only a stub, its failure path
        # leaves no trace short of introspecting instance counts). klink's
        # own visibility router already guarantees this exact polyline is
        # obstacle- and other-route-clear, so drawing it directly is both
        # simpler and the only proven-correct option.
        full_points = ([list(mark_a["center_um"])] + list(path)
                       + [list(mark_b["center_um"])])
        full_points = _simplify_collinear_points(full_points)
        if is_optical:
            # User ruling: optical routes are never right-angle, electrical/
            # pad routes stay Manhattan (that is the process convention for
            # metal). Only klink's OWN native detour path draws corners
            # directly (gf-routed nets already get gf's euler bend=euler
            # default); this is that one landing spot.
            full_points = round_manhattan_corners(full_points, bend_radius)
        # Belt-and-suspenders: re-check the FULL drawn path (including the
        # port-to-launch stubs route_points_geometric doesn't see) against
        # every foreign device one more time before accepting it.
        candidate_route = {"route_id": net, "net": net, "layer": layer,
                           "width_um": width, "points_um": full_points,
                           "source": entry["a"], "target": entry["b"]}
        if _route_device_hits([candidate_route], device_bboxes,
                              entry_by_net=entry_by_net,
                              mark_by_name=mark_by_name):
            failed[net] = (
                "net %r: the klink-planned detour clips a component even "
                "after simplification; move the components apart" % net)
            continue
        detours[net] = {
            "points": [[round(float(p[0]), 3), round(float(p[1]), 3)]
                      for p in full_points],
            "width": width,
            "layer": layer,
        }

    # --- phase 3: WRITE the final configuration (failed nets are left
    # undrawn — a missing route is honest, a wrong one is not).
    routes = []
    inserted = 0
    cleared_layers: set[str] = set()

    def _clear_layer_once(layer: str) -> bool:
        clear_now = layer not in cleared_layers
        cleared_layers.add(layer)
        return clear_now

    for entries in groups.values():
        plain = [e for e in entries
                 if e["net"] not in detours and e["net"] not in failed]
        layer = _group_layer(entries)
        if plain:
            clear_now = _clear_layer_once(layer)
            try:
                report = _call_route(plain, output_mode="batch_polygons",
                                     clear=clear_now)
            except ValueError as exc:
                for entry in plain:
                    failed.setdefault(entry["net"], (
                        "net %r: gdsfactory failed while writing (%s)"
                        % (entry["net"], exc)))
            else:
                routes.extend(report.get("routes", []))
                inserted += (report.get("writeback") or {}).get("inserted", 0)
        for entry in entries:
            info = detours.get(entry["net"])
            if info is None:
                continue
            clear_now = _clear_layer_once(info["layer"])
            if clear_now:
                client.call("shape.delete", {
                    "cell": table.cell, "layers": [info["layer"]],
                    "kinds": ["polygons", "paths"], "limit": 10000})
            layer_s, datatype_s = str(info["layer"]).split("/", 1)
            client.call("layer.ensure", {
                "layer": int(layer_s), "datatype": int(datatype_s)})
            client.call("shape.insert_path", {
                "cell": table.cell, "layer": int(layer_s),
                "datatype": int(datatype_s), "points_um": info["points"],
                "width_um": info["width"]})
            length = sum(
                ((info["points"][i + 1][0] - info["points"][i][0]) ** 2
                 + (info["points"][i + 1][1] - info["points"][i][1]) ** 2) ** 0.5
                for i in range(len(info["points"]) - 1))
            routes.append({
                "route_id": entry["net"], "net": entry["net"],
                "source": entry["a"], "target": entry["b"],
                "layer": info["layer"], "width_um": info["width"],
                "points_um": info["points"], "length_um": round(length, 3),
            })
            inserted += 1

    # Collective verdict over the FULL route set: crossings across style
    # groups and routes cutting through device interiors are both real
    # conflicts that no single gf call can see. Both checks are LAYER-AWARE:
    # routes on different layers may overlap freely.
    by_layer: dict[str, list[dict]] = {}
    for route in routes:
        by_layer.setdefault(str(route.get("layer", "")), []).append(route)
    crossings = []
    for layer_routes in by_layer.values():
        crossings.extend(crossing_pairs(layer_routes))
    device_hits = _route_device_hits(routes, device_bboxes,
                                     entry_by_net=entry_by_net,
                                     mark_by_name=mark_by_name)
    problems = []
    seen_pairs = set()
    for crossing in crossings:
        pair = tuple(sorted((str(crossing.get("route_a")),
                             str(crossing.get("route_b")))))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        problems.append(
            "routes for nets %r and %r cross; separate the components, or "
            "give one net waypoints/steps in its style, or route it on "
            "another layer" % pair)
    seen_hits = set()
    for hit in device_hits:
        key = (str(hit.get("route_id")), tuple(hit.get("bbox_um") or ()))
        if key in seen_hits:
            continue
        seen_hits.add(key)
        problems.append(
            "route for net %r cuts through a placed component near bbox %s; "
            "move the component or give the net waypoints/steps in its "
            "style" % (hit.get("route_id"), hit.get("bbox_um")))
    problems.extend(failed[net] for net in sorted(failed))

    client.call("view.show_cell", {"cell": table.cell, "zoom_fit": True})
    lengths = {r.get("route_id"): round(float(r.get("length_um", 0.0)), 2)
               for r in routes}
    for entry in abutted:
        lengths[entry["net"]] = 0.0
    result = {
        "ok": (not crossings and not device_hits and not failed
               and len(routes) == len(to_route) - len(failed)),
        "harvested_ports": len(marks),
        "routes": len(routes),
        "expected_routes": len(to_route),
        "abutted": len(abutted),
        "detoured": len(detours),
        "failed": len(failed),
        "crossings": len(seen_pairs),
        "device_hits": len(seen_hits),
        "inserted": inserted,
        "nets": table.net_names(),
        "lengths_um": lengths,
    }
    if problems:
        result["problems"] = problems
    return result


def _device_layer_bboxes_um(client, cell: str, tags: dict[str, str],
                            layers: list[str]) -> dict[str, list[list[float]]]:
    """Per-LAYER bounding boxes (um) of all tagged instances in `cell`, plus
    an always-present ``"__full__"`` bucket (union bbox over EVERY layer of
    each instance, regardless of `layers`).

    A device's PER-LAYER footprint differs per layer (heater metal overhangs
    the waveguide, pads exist only on metal); an ELECTRICAL route only
    conflicts with the footprint on ITS OWN layer, so those callers key off
    the per-layer buckets. An OPTICAL net's keep-out is the device's WHOLE
    body (any layer can hide a real physical obstruction, e.g. a component
    placed so its full-layer envelope overlaps a neighbor even though the
    two don't share a drawn layer at that spot) -- those callers use
    ``out["__full__"]`` instead.
    """
    from .blackbox import _apply_trans

    dbu = float(client.layout_info().get("dbu", 0.001))
    index_to_layer = {
        entry.get("layer_index"): "%s/%s" % (entry.get("layer"), entry.get("datatype"))
        for entry in client.layer_list().get("layers", [])
    }
    wanted = set(layers)
    child_data: dict[str, tuple[dict[str, list[float]], list[float] | None]] = {}

    def _child_bboxes(child: str) -> tuple[dict[str, list[float]], list[float] | None]:
        if child in child_data:
            return child_data[child]
        per_layer: dict[str, list[float]] = {}
        xs_all: list[float] = []
        ys_all: list[float] = []
        result = client.call("shape.query", {"cell": child, "limit": 5000})
        for shape in result.get("shapes", []):
            bbox = shape.get("bbox_dbu")
            if not bbox:
                continue
            xs_all.extend((bbox[0], bbox[2]))
            ys_all.extend((bbox[1], bbox[3]))
            layer = index_to_layer.get(shape.get("layer_index"))
            if layer not in wanted:
                continue
            agg = per_layer.get(layer)
            if agg is None:
                per_layer[layer] = list(bbox)
            else:
                agg[0] = min(agg[0], bbox[0])
                agg[1] = min(agg[1], bbox[1])
                agg[2] = max(agg[2], bbox[2])
                agg[3] = max(agg[3], bbox[3])
        full = ([min(xs_all), min(ys_all), max(xs_all), max(ys_all)]
                if xs_all else None)
        child_data[child] = (per_layer, full)
        return child_data[child]

    def _transform_bbox(bbox: list[float], trans: dict) -> list[float]:
        corners = [
            _apply_trans([bbox[0], bbox[1]], trans, dbu),
            _apply_trans([bbox[2], bbox[1]], trans, dbu),
            _apply_trans([bbox[2], bbox[3]], trans, dbu),
            _apply_trans([bbox[0], bbox[3]], trans, dbu),
        ]
        xs = [p[0] for p in corners]
        ys = [p[1] for p in corners]
        return [min(xs), min(ys), max(xs), max(ys)]

    out: dict[str, list[dict]] = {layer: [] for layer in layers}
    out["__full__"] = []
    counters: dict[str, int] = {}
    result = client.call("instance.query", {"parent": cell, "limit": 5000})
    for inst in result.get("instances", []):
        child = str(inst.get("child") or "")
        if child not in tags:
            continue
        tag = tags[child]
        ordinal = counters.get(tag, 0)
        counters[tag] = ordinal + 1
        owner = "%s%d" % (tag, ordinal)   # same identity rule as the harvest
        trans = inst.get("trans") or {}
        per_layer, full_local = _child_bboxes(child)
        for layer, bbox in per_layer.items():
            out[layer].append({"owner": owner,
                               "bbox": _transform_bbox(bbox, trans)})
        if full_local is not None:
            out["__full__"].append({"owner": owner,
                                    "bbox": _transform_bbox(full_local, trans)})
    return out


# ----------------------------------------------------------------------
# Optical-net detection + euler corner rounding for klink-planned detours.
#
# The visibility router (route_points_geometric) only ever produces exact
# Manhattan turns. Electrical/pad nets keep those sharp right angles as-is
# (that is the process convention for metal routing); optical nets must
# never carry a right-angle corner (excess loss / back-reflection). The
# clothoid-pair (euler) bend geometry itself lives in
# klink.routing.geom.bends (pure geometry, no client/process data) -- this
# module only decides WHICH nets are optical and where the radius fact
# comes from.
# ----------------------------------------------------------------------

def _is_optical_port_type(port_type: Any) -> bool:
    """gdsfactory port-type convention: "optical" and out-of-plane grating
    couplers ("vertical_te"/"vertical_tm") are optical; everything else
    (electrical, placement, ...) is not."""
    value = str(port_type or "").strip().lower()
    return value == "optical" or value.startswith("vertical_")


def _is_optical_net(mark_a: dict | None, mark_b: dict | None) -> bool:
    return (_is_optical_port_type((mark_a or {}).get("port_type"))
            and _is_optical_port_type((mark_b or {}).get("port_type")))


def _optical_bend_radius_um(style_obj: "RouteStyle") -> float:
    """Radius source order: explicit style, then the active gf PDK's strip
    cross-section, then +inf (meaning: let round_manhattan_corners's own
    per-corner clamp pick the largest radius that fits — klink ships no
    hardcoded fallback number, that would be a process fact)."""
    if style_obj.radius_um:
        return float(style_obj.radius_um)
    try:
        from klink.routing.backends.gdsfactory.gdsfactory_backend import _load_gdsfactory

        gf = _load_gdsfactory()
        radius = float(gf.get_cross_section("strip").radius)
        if radius > 0:
            return radius
    except Exception:
        pass
    return float("inf")


def _simplify_collinear_points(points: list, eps: float = 1e-6) -> list[list[float]]:
    """Drop waypoints that sit exactly on the line through their neighbors.

    A "waypoint" that isn't an actual direction change is not a corner, it's
    a redundant pass-through point. Proven live: passing one through to
    gdsfactory's route_single/route_bundle right before the real port
    approach corrupts their bend placement (see the two call sites).
    """
    pts = [[float(p[0]), float(p[1])] for p in points]
    if len(pts) < 3:
        return pts
    # A point is redundant iff (prev->point) and (point->next) are parallel
    # (cross product ~0), i.e. no direction change happens there. Compares
    # against the last KEPT point so chains of several redundant points in a
    # row collapse correctly, not just pairwise.
    simplified = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, b, c = simplified[-1], pts[i], pts[i + 1]
        d1x, d1y = b[0] - a[0], b[1] - a[1]
        d2x, d2y = c[0] - b[0], c[1] - b[1]
        cross = d1x * d2y - d1y * d2x
        if abs(cross) <= eps:
            continue  # b is not a real corner; skip it
        simplified.append(b)
    simplified.append(pts[-1])
    return simplified


def _route_segment_bboxes_um(points: list, margin: float) -> list[list[float]]:
    """Thin keep-out rectangles along an already-planned route's backbone.

    Used to treat an OTHER net's route as an obstacle when planning a
    detour: klink's routes don't avoid each other by construction (each gf
    call only sees its own style group), so without this a detour blind to
    other nets just trades a device hit for a route-vs-route crossing.
    """
    boxes = []
    pts = [[float(p[0]), float(p[1])] for p in points]
    for a, b in zip(pts, pts[1:]):
        x0, x1 = sorted((a[0], b[0]))
        y0, y1 = sorted((a[1], b[1]))
        boxes.append([x0 - margin, y0 - margin, x1 + margin, y1 + margin])
    return boxes


def _owner_of(port_name: str, owners: set[str]) -> str | None:
    """Instance key a harvested port name belongs to (longest prefix wins)."""
    best = None
    for owner in owners:
        if port_name.startswith(owner + "_"):
            if best is None or len(owner) > len(best):
                best = owner
    return best


def _optical_keepouts(
    device_full_entries: list[dict],
    own_owners: set,
    mark_a: dict | None,
    mark_b: dict | None,
    *,
    notch_halfwidth: float,
    notch_depth: float,
) -> list[list[float]]:
    """Net-level obstacle set for ONE optical net (used by both the
    device-hit checker and the detour planner -- only the margins differ).

    * a FOREIGN instance (owner not in `own_owners`) contributes its full
      (all-layer) bbox UNCHANGED -- optical keep-out is never per-layer;
    * an OWN instance (the net's own endpoint device) contributes its full
      bbox with an entry NOTCH cut at the matching port's position
      (``subtract_port_notch``), so a route may enter through its own port
      face but nothing else grazes or crosses the rest of the body;
    * when ONE device owns BOTH endpoints of this net, both notches are cut
      in sequence (the second cut applies only to whichever piece the
      second port actually sits near);
    * when a port cannot be proven to sit on the device's boundary (a tilted
      placement whose axis-aligned envelope swallowed it, or a port buried
      under a metal overhang), the whole device is dropped from the
      obstacle list for this net -- the legacy "own device is fully exempt"
      fallback, scoped to just this one net/device pair.
    """
    from klink.routing.geom.geometry import subtract_port_notch

    def _near(bbox: Sequence[float], point: Sequence[float], eps: float = 0.5) -> bool:
        x0, y0, x1, y1 = bbox
        return (x0 - eps <= point[0] <= x1 + eps
                and y0 - eps <= point[1] <= y1 + eps)

    owners_all = {e["owner"] for e in device_full_entries}
    owner_a = _owner_of(str((mark_a or {}).get("name", "")), owners_all)
    owner_b = _owner_of(str((mark_b or {}).get("name", "")), owners_all)
    port_a = (mark_a or {}).get("center_um")
    port_b = (mark_b or {}).get("center_um")
    ports_by_owner: dict[str, list] = {}
    if owner_a is not None and port_a is not None:
        ports_by_owner.setdefault(owner_a, []).append(port_a)
    if owner_b is not None and port_b is not None:
        ports_by_owner.setdefault(owner_b, []).append(port_b)

    obstacles: list[list[float]] = []
    for entry in device_full_entries:
        owner = entry["owner"]
        if owner not in own_owners:
            obstacles.append(list(entry["bbox"]))
            continue
        ports_here = ports_by_owner.get(owner)
        if not ports_here:
            continue  # own device we can't place a port on -> full exemption
        boxes = [list(entry["bbox"])]
        dropped_whole = False
        for i, port_xy in enumerate(ports_here):
            next_boxes: list[list[float]] = []
            for box in boxes:
                if not _near(box, port_xy):
                    next_boxes.append(box)
                    continue
                pieces = subtract_port_notch(
                    box, port_xy, notch_halfwidth=notch_halfwidth,
                    notch_depth=notch_depth)
                if pieces is None:
                    if i == 0:
                        # whole footprint (first cut) swallowed the port ->
                        # legacy whole-device exemption for this net.
                        dropped_whole = True
                        break
                    next_boxes.append(box)  # keep this later-stage piece
                    continue
                next_boxes.extend(pieces)
            if dropped_whole:
                break
            boxes = next_boxes
        if dropped_whole:
            continue
        obstacles.extend(boxes)
    return obstacles


def _route_device_hits(routes: list[dict],
                       device_bboxes: dict[str, list[dict]],
                       *, entry_by_net: dict[str, dict] | None = None,
                       mark_by_name: dict[str, dict] | None = None) -> list[dict]:
    """Routes whose backbone cuts through a FOREIGN device's interior.

    Electrical nets keep the legacy per-layer check UNCHANGED (a route only
    conflicts with a device's footprint on ITS OWN layer, own endpoint
    devices exempt entirely, every block additionally shrunk by (route
    width/2 + 0.1um) so a route ending exactly on a FOREIGN device's
    boundary -- e.g. two pads placed flush -- does not trip it; electrical
    has no notch mechanism, so it still needs that blanket tolerance).

    Optical nets (both endpoints optical port_type, resolved via
    `entry_by_net`/`mark_by_name`) use the LAYER-INDEPENDENT full-body
    keep-out with a port-entry notch for their own endpoint devices instead
    of a blanket exemption -- see `_optical_keepouts`. Optical obstacles are
    checked WITHOUT that extra (width/2 + 0.1um) pre-shrink: it nets out to
    a flat -0.1um regardless of width (the width/2 terms in the shrink and
    in `route_hits_bboxes`'s own expansion exactly cancel), which was tuned
    for a route ending AT a boundary point. With the notch already carving
    out the legitimate port-entry area, that same blanket tolerance would
    also hide a route that runs PARALLEL to and along a device's TRUE edge
    somewhere else -- e.g. an approach corridor placed level with a port,
    which shares that port's y (or x) coordinate with the device's own bbox
    edge for a long stretch outside the notch. Proven live: klink's own
    drawn detours still terminate exactly on `mark_a`/`mark_b`'s boundary
    port without tripping this check, because `route_hits_bboxes`'s width/2
    expansion alone already tolerates a route ending flush at the true edge.
    """
    owners: set[str] = set()
    for entries in device_bboxes.values():
        owners.update(e["owner"] for e in entries)
    entry_by_net = entry_by_net or {}
    mark_by_name = mark_by_name or {}
    hits = []
    for route in routes:
        width = float(route.get("width_um", 0.5) or 0.5)
        net = str(route.get("net") or route.get("route_id") or "")
        entry = entry_by_net.get(net)
        mark_a = mark_by_name.get(entry["a"]) if entry else None
        mark_b = mark_by_name.get(entry["b"]) if entry else None
        if entry is not None and _is_optical_net(mark_a, mark_b):
            full_entries = device_bboxes.get("__full__", [])
            if not full_entries:
                continue
            own = {_owner_of(str(route.get("source", "")), owners),
                   _owner_of(str(route.get("target", "")), owners)}
            obstacles = _optical_keepouts(
                full_entries, own, mark_a, mark_b,
                notch_halfwidth=width / 2.0 + 0.3, notch_depth=0.3)
        else:
            layer_entries = device_bboxes.get(str(route.get("layer", "")), [])
            if not layer_entries:
                continue
            own = {_owner_of(str(route.get("source", "")), owners),
                   _owner_of(str(route.get("target", "")), owners)}
            shrink = -(width / 2.0 + 0.1)
            obstacles = []
            for entry_bbox in (e["bbox"] for e in layer_entries
                               if e["owner"] not in own):
                b = expand_bbox(entry_bbox, shrink)
                if b[0] < b[2] and b[1] < b[3]:
                    obstacles.append(b)
        for hit in route_hits_bboxes(route.get("points_um", []), obstacles, width):
            hits.append({"route_id": route.get("route_id", ""),
                         "bbox_um": hit.get("bbox_um")})
    return hits


def route_kwargs_for(style: RouteStyle, *, default_route_layer: str) -> dict[str, Any]:
    """Translate a RouteStyle into route_gdsfactory_ports kwargs.

    Pure offline / no gdsfactory import here: any concrete gf CrossSection
    object is constructed by the backend strategy function itself, INSIDE
    the real gf call (see gdsfactory_backend.route_gf_bundle's `cross_section
    is None` branch) -- this function only ever hands plain kwargs across
    the boundary. `cross_section` is therefore always None; width/radius
    travel as plain floats: `route_width_um` is a common parameter every
    strategy accepts, `radius_um` only for routers that declare it. (A
    previous version eagerly built `gf.get_cross_section("strip", ...)`
    here -- besides dragging a hard gdsfactory import into every offline
    net_intent test, its `width=` override never actually took effect on
    the "strip" cross-section, a silent no-op the gf backend now warned
    about; route_width_um is the real, working knob.)

    Only parameters the chosen router honors are emitted (the strategies have
    disjoint parameter sets and reject foreign kwargs); style fields at their
    defaults are simply dropped for routers that lack them, while explicitly
    set but unhonored fields already failed style.validate().
    """
    from klink.routing.backends.gdsfactory.gdsfactory_backend import router_params

    problems = style.validate()
    if problems:
        raise ValueError("invalid route style: " + "; ".join(problems))

    honored = router_params(style.router)
    route_layer = style.route_layer or default_route_layer
    kwargs: dict[str, Any] = {
        "router": style.router,
        "route_layer": route_layer,
        "cross_section": None,
    }
    if "separation_um" in honored:
        kwargs["separation_um"] = float(style.separation_um)
    if "auto_taper" in honored:
        kwargs["auto_taper"] = bool(style.auto_taper)
    if style.waypoints_um:
        kwargs["waypoints_um"] = [list(p) for p in style.waypoints_um]
    if style.steps:
        kwargs["steps"] = [dict(s) for s in style.steps]
    if style.width_um is not None:
        kwargs["route_width_um"] = float(style.width_um)
    if style.radius_um is not None and "radius_um" in honored:
        kwargs["radius_um"] = float(style.radius_um)
    return kwargs

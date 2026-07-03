"""Domain taxonomy + on-demand usage guidance for klink-mcp.

This is the data behind progressive disclosure. The contract:

* ``tools/list`` stays FULL — every local handler and plugin RPC is listed so
  Claude Code (and any schema-deferring client) can defer the big inputSchema
  and still CALL every tool. We do NOT hide tools from tools/list (a client can
  only invoke what tools/list advertises).
* This module is where the *detailed, skill-like* per-domain guidance lives. It
  is disclosed ON DEMAND through ``klink.find_tools(query, domain?)`` — never in
  the always-on tools/list descriptions, which stay tight.
* Every tool maps to exactly ONE domain via ``domain_for(name)``. The same
  ``domain`` token is also a ``--profile <domain>`` selector for harnesses that
  do NOT defer schemas and genuinely want a smaller flat list.

Edit the ``usage`` text freely — it is documentation, reviewed domain by domain.
Order of DOMAINS is the order find_tools lists them.
"""

from __future__ import annotations

from collections import OrderedDict


# token -> {title, summary, usage, prefixes}
# `prefixes` = plugin-RPC / local-tool first name segments that default here.
DOMAINS: "OrderedDict[str, dict]" = OrderedDict([
    ("connection_and_view", {
        "title": "Connection, self-check, discovery & view",
        "summary": "Is KLayout reachable, what is open, which tools exist, and how to look at the canvas (viewport / tabs / screenshot).",
        "prefixes": ["meta", "hello", "view", "klink"],  # klink.* except session_*/transfer_* (see domain_for)
        "usage": (
            "START HERE when unsure. `klink.status` reports connection, active "
            "session, interpreter and optional capabilities (gdsfactory, "
            "klayout.db). `klink.guide` reports what is open + on-disk intent "
            "state + the literal next call. `klink.find_tools` (this) navigates "
            "the rest by domain/keyword. `klink.reconnect` recovers a dropped "
            "link.\n"
            "View tools are read-mostly navigation: view.list_tabs / "
            "activate_tab / close_tab; view.show_cell (switch the canvas to a "
            "cell — a freshly cell.create'd cell is INVISIBLE until you "
            "show_cell it or instance it into the current top; zoom-fits by "
            "default); view.show_lvsdb; view.zoom_fit / zoom_box / viewport. "
            "Screenshots (view.screenshot) are a USER-REQUESTED artifact only — "
            "never an agent verification step; prefer geometry queries.\n"
            "Destructive here: view.close_tab — only on disposable test tabs."
        ),
    }),
    ("multi_session_transfer", {
        "title": "Multi-session registry & cross-session transfer",
        "summary": "Discover/label/switch between several running KLayout sessions, and move geometry between them.",
        "prefixes": ["session", "transfer"],  # + klink.session_*/klink.transfer_* (see domain_for)
        "usage": (
            "klink runs one MCP bridge over MANY KLayout sessions (each a port "
            "in the local registry). `klink.session_list` enumerates them; "
            "`klink.session_label` attaches a human label/aliases; "
            "`klink.session_resolve` turns a label/alias/active-cell into an id; "
            "`klink.session_use` repoints the bridge's primary RPC target; "
            "`klink.session_set_klive_target` chooses the 8082 klive endpoint.\n"
            "Transfer is two-phase and confirmation-safe: "
            "`klink.transfer_prepare` builds a package (flat_selection or "
            "shallow_instance), dry-runs it on the target, and persists it "
            "pending; `klink.transfer_commit` writes it. Sessions are equal "
            "peers — pass the session explicitly, there is no privileged port."
        ),
    }),
    ("geometry_authoring", {
        "title": "Geometry & cell-structure authoring",
        "summary": "Create/inspect layouts, cells, layers, shapes, instances and PCells. The core drawing surface.",
        "prefixes": ["layout", "cell", "layer", "shape", "instance", "pcell", "edit"],
        "usage": (
            "Read first: layout.info, cell.list / cell.tree, layer.list, "
            "shape.query, instance.query, pcell.libraries / list / info.\n"
            "Author with BATCH RPCs for anything generated — never one RPC per "
            "object: shape.insert_boxes (many boxes one layer), shape.insert_many "
            "(mixed box/polygon/path/text), instance.insert_many (child cells), "
            "instance.insert_pcell_many (Basic/library PCells). Singletons "
            "(shape.insert_box/polygon/path/text, instance.insert*) are for "
            "debugging one object.\n"
            "layer.ensure before drawing on a layer. edit.undo/redo/status wrap "
            "edits in transactions. Destructive: layout.clear, cell.delete — "
            "disposable/test cells only; do NOT touch the user's working "
            "tab/layout UNLESS the user explicitly instructs it."
        ),
    }),
    ("selection_and_send_memory", {
        "title": "Selection & SEND interaction memory",
        "summary": "The live KLayout selection, and the session-scoped memory of selections the user explicitly SENT.",
        "prefixes": ["selection", "interaction"],
        "usage": (
            "Two distinct things. selection.* is the LIVE current selection in "
            "KLayout: selection.get (exact current state), selection.set_box "
            "(select all shapes in a cell whose bbox intersects a box on the "
            "given layers — REPLACES the current selection), selection.clear, "
            "selection.send_context (agent-side explicit SEND).\n"
            "interaction.* is durable session memory of selections the user "
            "explicitly SENT (toolbar SEND, recorded as ids like sel_0006): "
            "interaction.selection.latest / recent (default latest 5, ordered "
            "NOT time-pruned) / get / label, and interaction.context (current "
            "selection + recent memory together).\n"
            "Use these whenever the user says \"just sent\", \"this area\", "
            "\"here\", \"that one\". Resolve by order/count, not age. Bind user "
            "phrases to these ids/queries, NOT to screenshots."
        ),
    }),
    ("ports_and_anchors", {
        "title": "Ports & anchors (routing markers)",
        "summary": "Mark, list, move, repair the Port and Anchor PCells that the routing backends consume.",
        "prefixes": ["port", "anchor"],
        "usage": (
            "Ports are net endpoints (klink_Port PCells: carry net + "
            "orientation + width). Anchors are routing constraints (klink_Anchor "
            "PCells) whose `kind` is one of waypoint_region, bend_region, or "
            "corridor (a plain corridor is a REQUIRED pass-through; label a "
            "corridor choice_group=BUS to make it an OPTIONAL channel for "
            "routing.global_channel_cell). port.mark / list / update / transform "
            "/ set_layer / unmark / delete_all / repair_names; the same verbs on "
            "anchor.* (+ anchor.repair_ids). The routing tools default "
            "port_layer=999/99, anchor_layer=999/1.\n"
            "Keepouts are NOT an anchor kind — they are obstacle LAYER(s) you "
            "pass to routing tools as obstacle_layers; pass your OWN design's "
            "keepout layer(s). klink ships NO default keepout layer (the generic "
            "routing tools default obstacle_layers to none — you must name the "
            "layers that are obstacles in your layout). 900/0 is klink's RESERVED "
            "keepout layer (like 999/99 Port / 999/1 Anchor); structdevice's "
            "connect_nets uses it internally as a scratch keepout and passes it "
            "explicitly.\n"
            "These are the INPUT to routing_backends: mark Ports+Anchors, then "
            "call a routing.* tool. (port.harvest_blackbox is NOT here — it is a "
            "photonics PDK tool; see device_photonics.)"
        ),
    }),
    ("routing_backends", {
        "title": "Routing backends",
        "summary": "Turn Port/Anchor markers into routed geometry — tapered, Steiner, damped, global-channel, multilayer-escape, or gdsfactory.",
        "prefixes": ["routing"],
        "usage": (
            "All read Port/Anchor PCells in a cell and write routes. Pick by "
            "topology/quality:\n"
            "- routing.tapered_hybrid_cell — main path+patch backend (angle_mode "
            "any/manhattan/fortyfive).\n"
            "- routing.tapered_polygon_cell — continuous taper polygons (first-"
            "class, not a fallback).\n"
            "- routing.steiner_cell — multi-terminal nets (>2 ports); split star "
            "nets into 2-port nets otherwise.\n"
            "- routing.damped_{segment,polygon,steiner}_cell — explicit extra "
            "obstacle clearance (only when asked / default too close).\n"
            "- routing.global_channel_cell — a global-DECISION router on top of "
            "tapered hybrid: candidate-sink assignment + corridor-capacity "
            "load-balancing, then it reuses the tapered hybrid geometry. NOT "
            "deprecated (it is the 'stronger assignment' path), but NOT a full "
            "negotiated congestion router (no full rip-up/reroute).\n"
            "- routing.multilayer_escape_cell — wall-blocked nets via bridge "
            "layer + vias (narrow; no via-enclosure modelling).\n"
            "- routing.gdsfactory_ports — reads the cell's Port markers (RPC "
            "port.list), routes them with ONE named gdsfactory strategy, and "
            "writes the routes back to KLayout (RPC; output_mode = "
            "batch_polygons via shape.insert_many / klink_paths / dry_run). "
            "Pick router by intent: bundle = Manhattan river routing with "
            "separation (DEFAULT; also honors waypoints_um/steps, radius_um, "
            "start/end_straight_um, path_length_match for length matching, "
            "collision_check_layers, sbend_fallback); electrical = bundle with "
            "sharp wire corners + electrical port typing; sbend = smooth "
            "S-transition for laterally offset facing ports; all_angle = "
            "non-Manhattan bundle (optional backbone_um spine); single = "
            "independent Manhattan route per pair; dubins = arc-based "
            "any-heading per pair; astar = EXPERIMENTAL grid A* per pair "
            "around obstacle_bboxes_um — gf's astar is fragile, so klink "
            "verifies the result and ERRORS instead of returning a wall-"
            "crossing route; reliable avoidance lives in klink's own "
            "tapered_hybrid/damped backends with obstacle_layers. A "
            "parameter the chosen "
            "router cannot honor is an ERROR naming the routers that honor "
            "it (nothing is silently ignored). cross_section picks optical "
            "('strip') or electrical ('metal_routing'); null = draw "
            "route_width on route_layer directly — OPTICAL and ELECTRICAL, "
            "not just photonic. Needs gdsfactory in the MCP interpreter; "
            "multi-port OPTICAL nets need an explicit splitter first "
            "(point-to-point).\n"
            "ALWAYS inspect the structured result: not ok if ok=false, "
            "obstacle_hit_count>0, sibling overlaps, short route_count. Report "
            "router limits honestly."
        ),
    }),
    ("drc_and_lvs_verification", {
        "title": "DRC & LVS verification",
        "summary": "Run design-rule checks and layout-vs-schematic on a cell.",
        "prefixes": ["drc", "lvs"],
        "usage": (
            "drc.run is an escape hatch: it runs arbitrary DRC DSL (Ruby) "
            "script code YOU supply against the layout (optional output_rdb); "
            "exceptions inside the script come back as results, they do NOT fail "
            "the RPC. lvs.run is the connectivity counterpart: it extracts the "
            "live layout into a device netlist (per-cell device extractors from "
            "a 'devices' config + the conductor layers you pass) and compares it "
            "against a REFERENCE netlist you supply, writes a native .lvsdb and "
            "(show=true default) opens it in the Netlist/LVS browser for "
            "layout<->netlist cross-probe. Both are long-running, pure pya, "
            "domain-agnostic. A P&R/device stage counts as DONE only on a real "
            "live LVS match=True — offline fixtures and marker counts never "
            "substitute. For the structdevice flow prefer structdevice.lvs_check "
            "(it builds the reference + extracted netlists and reconciles them "
            "against the declared/spec nets for you)."
        ),
    }),
    ("device_structdevice", {
        "title": "Custom-device netlist -> auto place/route/LVS",
        "summary": "Give a netlist of ARBITRARY custom devices (recipe-derived terminals) and get an automatic, LVS-verified layout. Device-agnostic — transistors are just one case.",
        "prefixes": ["structdevice"],
        "usage": (
            "This is the device-AGNOSTIC custom-device P&R flow. A 'device' is "
            "any cell with an arbitrary parameter set + terminals; klink assumes "
            "no parameter names/count and no device vocabulary. The device "
            "library (key -> params + fitted PCell), the process profile, and the "
            "terminal source are EXAMPLE/PDK data passed in explicitly — the MCP "
            "tools ship none and return an instructive 'write/run an example' "
            "error (see your pdk.py + recipes).\n"
            "- structdevice.build_from_netlist — the headline one-call flow: give "
            "a device-level netlist ({instances, nets, groups}) and it derives a "
            "floorplan, single-pass multilayer routes, draws, and device-LVS-"
            "verifies a FRESH cell. Confirmation-gated (call once -> proposal; "
            "again with confirm -> build). Routing runs on the flexdr engine "
            "(layout_engine.route_and_draw_flexdr — the single-pass router the "
            "public demos prove LVS-clean), using a compact physical model where "
            "the device's OWN metal layers double as routing layers (e.g. a stack "
            "where the gate layer and the S/D layer are also routing layers); "
            "mode 2L/3L picks 2 or 3 of them. Nothing is hand-tuned — "
            "layers/vias/spacing come from the process profile.\n"
            "- structdevice.declare_nets — one SEND framing >=2 terminals = one "
            "declared net (persisted).\n"
            "- structdevice.connect_nets — wire declared-but-unconnected nets + "
            "verify; any LVS mismatch undoes everything.\n"
            "- structdevice.lvs_check — net-level reconcile (+ device-level "
            "NetlistComparer / .lvsdb in mode=device/both/lvsdb).\n"
            "- structdevice.spec_write — project the live cell into a "
            "klink.spec.json fact file.\n"
            "- structdevice.register_pcell — register a fitted-device PCell at "
            "runtime (no plugin reload).\n"
            "klink ships NO process: pass conductors=[...]+vias=[...] (your "
            "stack) or run an example from your pdk.py. A call "
            "without a process returns an INSTRUCTIVE error, not a guess."
        ),
    }),
    ("device_nanodevice", {
        "title": "Nanodevices (Hall bar, EBL, flake traces)",
        "summary": "Build/route a Hall bar device, or detect & commit nanodevice flake traces, in one call.",
        "prefixes": ["nanodevice"],
        "usage": (
            "- nanodevice.hallbar — a one-call closed loop. What it adds OVER "
            "generic port-marking + routing is the parameterized Hall-bar DEVICE "
            "GEOMETRY: from a HallBarSpec (bar length/width, contact_count, "
            "contact/pad dims, pitch, gaps) it computes and draws the WHOLE "
            "device — bar + N symmetric contact arms + pads + Port markers + "
            "labels — that the generic shape/port tools would make you lay out "
            "box-by-box. It then DELEGATES the actual routing to the generic "
            "router (route_tapered_hybrid_many) to wire contacts->pads (overlap "
            "validation on), with optional EBL writefield walls as keepouts, and "
            "commits to a disposable cell (dry_run supported). So it is a device "
            "GENERATOR + EBL + orchestration, not a new router. Failures return "
            "problems/next_action and change nothing.\n"
            "- nanodevice.detect_commit — commit flake traces as polygons from a "
            "precomputed traces.json, or run live detection from a microscope "
            "image. Live image detection needs cv2 (OpenCV) + numpy in this "
            "interpreter; the traces.json path needs neither. (The MCP tool's "
            "own one-line description mis-states the deps as scipy/scikit-* — the "
            "real imports are cv2 + numpy; tool-description fix pending.)"
        ),
    }),
    ("device_photonics", {
        "title": "Photonics (gdsfactory import / connect / reroute)",
        "summary": "Import user gdsfactory scripts, harvest blackbox PDK ports, and connect/reroute optical nets with the gdsfactory backend, driven by SENDs.",
        "prefixes": ["photonics"],
        "name_overrides": ["port.harvest_blackbox"],
        "usage": (
            "Photonic circuit flow. Needs gdsfactory in the MCP interpreter. "
            "Two port sources, one interactive loop:\n"
            "- photonics.import_gf — ONE call takes a finished user gdsfactory "
            "script over into the loop: device instances become real KLayout "
            "cells+instances (batch RPC), routed/snapped connections collapse "
            "to device-level nets, per-device port templates persist in the "
            "spec, nets are routed by klink (the script's own routes are "
            "replaced). After it: drag in the GUI -> photonics.reroute with "
            "just the cell name.\n"
            "- port.harvest_blackbox — derive klink Ports from LIVE blackbox "
            "instance positions via the foundry stub convention (pass YOUR "
            "wg_layer/stub_size_um); re-run after moving instances, then "
            "route.\n"
            "- photonics.connect — read the latest N SENDs as port pairs, "
            "auto-name nets, persist, re-harvest, route with gdsfactory. The "
            "KLayout session is derived from the SENDs. Stub-convention cells "
            "need wg_layer/stub_size_um/route_layer; gf-imported cells need "
            "nothing extra (spec carries templates + route layer).\n"
            "- photonics.reroute — re-route a cell after the user moved "
            "components (reads the persisted net table). Multi-port optical nets "
            "need an explicit splitter/MMI; route the resulting 2-port nets. "
            "Ports already touching (gf connect()/flush drag) are reported as "
            "abutted and not routed."
        ),
    }),
    ("escape_hatch", {
        "title": "Escape hatch (pya exec, events, recorder)",
        "summary": "Raw KLayout pya execution, the event stream, and the replay recorder. Use only when typed RPCs don't cover it.",
        "prefixes": ["exec", "events", "recorder"],
        "usage": (
            "Prefer typed RPCs. exec.python runs raw pya for operations no typed "
            "RPC covers / debugging / compact one-offs (exec.reset clears its "
            "namespace); it still schedules recorder + layout-diff detection.\n"
            "events.* (channels/status/subscribe/unsubscribe) is the live event "
            "stream the bridge subscribes to for SEND memory — usually you read "
            "interaction.* instead.\n"
            "recorder.* (start/stop/status) generates a replay SCRIPT (not a "
            "literal RPC log); a bulk RPC may expand into replay actions. Check "
            "recorder.status before tests so you never clobber a user recording."
        ),
    }),
])


# Tools whose name prefix does not match their domain.
# - port.harvest_blackbox: a photonics PDK tool, not a generic port marker.
# - pcell.register_fitted: the RPC behind structdevice.register_pcell (the
#   other pcell.* are generic geometry).
_NAME_OVERRIDES = {
    "port.harvest_blackbox": "device_photonics",
    "pcell.register_fitted": "device_structdevice",
}

# prefix -> domain (built from DOMAINS[*]["prefixes"]).
_PREFIX_TO_DOMAIN = {
    prefix: token
    for token, meta in DOMAINS.items()
    for prefix in meta.get("prefixes", [])
}

UNCATEGORIZED = "uncategorized"


def domain_for(name: str) -> str:
    """Map a tool name (local handler or plugin RPC) to its domain token."""
    if name in _NAME_OVERRIDES:
        return _NAME_OVERRIDES[name]
    # klink.* splits: session/transfer go to multi_session_transfer,
    # everything else (status/reconnect/guide/find_tools) is connection_and_view.
    if name.startswith("klink.session") or name.startswith("klink.transfer"):
        return "multi_session_transfer"
    prefix = name.split(".", 1)[0]
    return _PREFIX_TO_DOMAIN.get(prefix, UNCATEGORIZED)


def domain_tokens() -> list[str]:
    return list(DOMAINS.keys())

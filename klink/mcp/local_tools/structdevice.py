"""structdevice.* local MCP tool handlers.

SEND-driven net declaration, LVS-lite + device-level LVS, automatic net wiring,
netlist build, fitted-PCell registration, and spec projection. Handlers are
functions (ctx, arguments); the spec root, connectivity builder, and
session-scoped client live on the bridge (ctx).
"""

from __future__ import annotations

from ..results import _error_result, _json_result
from . import local_tool


@local_tool(
    "structdevice.declare_nets",
    "Declare electrical nets from the user's SENDs: ONE SEND framing two "
    "or more device terminals = ONE declared net; declarations persist to "
    "<cell>.elec_nets.json and feed structdevice.lvs_check / spec_write. "
    "EXAMPLE-DRIVEN, NOT standalone: reading device terminals needs a recipe "
    "injected from your project (klink ships none), so called as-is it returns "
    "an instructive error naming the recipe to provide -- never a guess.",
    {
        "type": "object",
        "properties": {
            "recent_sends": {
                "type": "integer", "minimum": 1, "maximum": 20,
                "description": "How many of the latest SEND selections to consume (one net each).",
            },
            "cell": {"type": "string", "description": "Parent cell whose device terminals are framed."},
            "conductors": {"type": "array", "items": {"type": "string"},
                           "description": "Conductor layers 'L/D' for YOUR process (required; klink ships no default)."},
            "vias": {"type": "array",
                     "items": {"type": "array", "items": {"type": "string"},
                               "minItems": 3, "maxItems": 3}},
        },
        "required": ["recent_sends", "cell"],
        "additionalProperties": False,
    },
)
def _tool_structdevice_declare_nets(ctx, arguments: dict) -> dict:
    # klink ships no device terminal recipe (purity): declaring nets from SENT
    # geometry needs one to read each device's terminals, so this interactive
    # tool cannot complete as shipped -- it returns an instructive error rather
    # than pretend. The lab/build flow is example-driven: inject a recipe from
    # your recipes (with the device-cell keys) via the domain orchestrators
    # (see the digital P&R starter scaffolded by `klink init`).
    return _error_result(
        "structdevice.declare_nets needs a device terminal recipe to read the "
        "SENT geometry's terminals; klink ships none. Run an example that "
        "injects a recipe from your recipes (with the device-cell keys) to "
        "drive declaration.")


@local_tool(
    "structdevice.lvs_check",
    "LVS in one call: derive device terminals (recipe), extract the "
    "nets the drawn wiring actually makes (KLayout-native extraction "
    "on a saved snapshot), and reconcile against the declared nets "
    "persisted by structdevice.declare_nets. mode='net' (default) = "
    "net-level LVS-lite; mode='device'/'both' ALSO runs device-level "
    "LVS (build reference + extracted netlists, compare with native "
    "NetlistComparer) -> result under device_lvs. Findings are "
    "instructions with terminal-level evidence; report persists to "
    "<cell>.lvs.json. EXAMPLE-DRIVEN, NOT standalone: deriving device "
    "terminals needs a recipe from your project (klink ships none), so "
    "called as-is it returns an instructive error.",
    {
        "type": "object",
        "properties": {
            "cell": {"type": "string"},
            "session": {"type": "string", "description": "KLayout session id/label/alias (default: primary)."},
            "mode": {"type": "string", "enum": ["net", "device", "both", "lvsdb"], "default": "net",
                     "description": "net = net-level reconcile; device/both = also device-level NetlistComparer (pass/fail); lvsdb = device-level LVS that ALSO writes a geometry-linked native .lvsdb (cross-probe in Netlist Browser), path under device_lvs.lvsdb_path."},
            "conductors": {"type": "array", "items": {"type": "string"},
                           "description": "Conductor layers 'L/D' for YOUR process (required; klink ships no default)."},
            "vias": {"type": "array",
                     "items": {"type": "array", "items": {"type": "string"},
                               "minItems": 3, "maxItems": 3},
                     "description": "Via triples [conductor, via_layer, conductor]."},
        },
        "required": ["cell"],
        "additionalProperties": False,
    },
)
def _tool_structdevice_lvs_check(ctx, arguments: dict) -> dict:
    try:
        from ...domains.structdevice.orchestrators import lvs_check

        client, close_after = ctx._session_scoped_client(
            arguments.get("session"))
        try:
            # klink ships no device terminal recipe (purity). lvs_check returns
            # its instructive "pass terminal_provider + placement/device_cells"
            # error; the lab flow is example-driven (your recipes
            # + pdk.py).
            result = lvs_check(
                client,
                str(arguments["cell"]),
                spec_root=ctx._structdevice_spec_root(),
                connectivity=ctx._structdevice_connectivity(arguments),
                mode=str(arguments.get("mode") or "net"),
            )
        finally:
            if close_after:
                try:
                    client.close()
                except Exception:
                    pass
        return _json_result(result)
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "structdevice.connect_nets",
    "Wire every declared-but-unconnected net of a device cell in one "
    "call and verify it: recipe-derived attach points, automatic via "
    "placement/reuse, automatic keepouts (everything not on the net "
    "is an obstacle), damped routing, then LVS — on any mismatch ALL "
    "mutations are undone. Call AFTER structdevice.declare_nets and "
    "BEFORE structdevice.spec_write. Results carry next_action; relay "
    "problems verbatim, never improvise wiring. EXAMPLE-DRIVEN, NOT "
    "standalone: attach points need a recipe from your project (klink ships "
    "none), so called as-is it returns an instructive error.",
    {
        "type": "object",
        "properties": {
            "cell": {"type": "string"},
            "session": {"type": "string", "description": "KLayout session id/label/alias (default: primary)."},
            "route_layer": {"type": "string", "description": "routing layer 'L/D' for YOUR process (required; klink ships no default)."},
            "route_width_um": {"type": "number", "description": "route trace width in um for YOUR process (required)."},
            "via_cell": {"type": "string", "description": "via cell name for YOUR process (required)."},
            "min_spacing_um": {"type": "number", "default": 0.0,
                "description": "Routing-tier min edge-to-edge spacing (hard floor; litho-tier rules belong in DRC)."},
            "min_width_um": {"type": "number", "default": 0.0,
                "description": "Routing-tier min trace width; route_width_um below this is rejected."},
            "conductors": {"type": "array", "items": {"type": "string"}},
            "vias": {"type": "array",
                     "items": {"type": "array", "items": {"type": "string"},
                               "minItems": 3, "maxItems": 3}},
        },
        "required": ["cell"],
        "additionalProperties": False,
    },
)
def _tool_structdevice_connect_nets(ctx, arguments: dict) -> dict:
    try:
        from ...domains.structdevice.orchestrators import connect_nets

        client, close_after = ctx._session_scoped_client(
            arguments.get("session"))
        try:
            # klink ships no device terminal recipe (purity); connect_nets
            # returns its instructive error (the lab flow injects a recipe from
            # your recipes).
            if not arguments.get("route_layer") or not arguments.get("via_cell") \
                    or arguments.get("route_width_um") is None:
                return _error_result(
                    "structdevice.connect_nets needs your process routing params; "
                    "klink ships no default. Pass route_layer, via_cell, and "
                    "route_width_um for YOUR process (from your pdk.py).")
            result = connect_nets(
                client,
                str(arguments["cell"]),
                spec_root=ctx._structdevice_spec_root(),
                connectivity=ctx._structdevice_connectivity(arguments),
                route_layer=str(arguments["route_layer"]),
                route_width_um=float(arguments["route_width_um"]),
                via_cell=str(arguments["via_cell"]),
                min_spacing_um=float(
                    arguments.get("min_spacing_um") or 0.0),
                min_width_um=float(
                    arguments.get("min_width_um") or 0.0),
            )
        finally:
            if close_after:
                try:
                    client.close()
                except Exception:
                    pass
        return _json_result(result)
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "structdevice.build_from_netlist",
    "Build a circuit cell from a device-level netlist, fully algorithmic "
    "and confirmation-gated. TWO calls: (1) call WITHOUT `confirm` -> it "
    "returns `needs_confirmation`, a `proposal` (grid rows x cols, derived "
    "row pitch, routing layers, device mix) and `next_action`; READ the "
    "proposal to the user. (2) If they approve, call again with the SAME "
    "arguments plus the `confirm` token from next_action -> it places "
    "(derived floorplan), single-pass multilayer routes, draws, and "
    "device-LVS-verifies a FRESH cell. Netlist format: {instances: "
    "[{instance_id, device_cell}], nets: [{net_id, terminals: ['X1.D', "
    "...]}], groups: [{instances: [...]}]}. NOTHING is hand-tuned: layers/"
    "vias/spacing come from the process profile, the floorplan from demand. "
    "DO NOT place/route/draw yourself; DO NOT change rows/cols/mode unless "
    "the user asks; relay `problems` to the user VERBATIM. Every result "
    "carries next_action -- follow it.",
    {
        "type": "object",
        "properties": {
            "cell": {"type": "string", "description": "NEW cell name to create (existing names refused)."},
            "netlist": {"type": "object",
                        "description": "{instances:[{instance_id,device_cell}], nets:[{net_id,terminals}], groups:[{instances}]}."},
            "confirm": {"type": "string",
                        "description": "The token returned in the first call's next_action; omit on the first call."},
            "mode": {"type": "string", "enum": ["2L", "3L"], "default": "3L",
                     "description": "'3L' = 3 routing layers (smaller); '2L' = 2 layers."},
            "rows": {"type": "integer", "default": 0, "description": "0 = derive from gate count."},
            "cols": {"type": "integer", "default": 0, "description": "0 = derive from gate count."},
            "session": {"type": "string", "description": "KLayout session id/label (default: primary)."},
        },
        "required": ["cell", "netlist"],
        "additionalProperties": False,
    },
)
def _tool_structdevice_build_from_netlist(ctx, arguments: dict) -> dict:
    try:
        from ...domains.structdevice.netlist_build import build_from_netlist

        client, close_after = ctx._session_scoped_client(
            arguments.get("session"))
        try:
            # klink ships no profile / device library / geom path -- this MCP
            # tool intentionally calls with none, so build_from_netlist returns
            # its instructive "write/run an example that passes a profile +
            # devices from your pdk.py" error. The lab build is
            # example-driven (purity contract), never baked into the tool.
            result = build_from_netlist(
                client,
                str(arguments["cell"]),
                dict(arguments["netlist"]),
                spec_root=ctx._structdevice_spec_root(),
                mode=str(arguments.get("mode") or "3L"),
                rows=int(arguments.get("rows") or 0),
                cols=int(arguments.get("cols") or 0),
                confirm=arguments.get("confirm"),
            )
        finally:
            if close_after:
                try:
                    client.close()
                except Exception:
                    pass
        return _json_result(result)
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "structdevice.register_pcell",
    "Register a fitted-device PCell at runtime from a fit table "
    "(produced by the exemplar fitter). One call, zero plugin "
    "changes, zero reloads: the PCell lands in library "
    "'klink_structdevice' and is immediately usable in the GUI and "
    "via instance.insert_pcell. Call AFTER the fitter produced the "
    "table; the table encodes user geometry and stays local.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "PCell name (unique per KLayout session)."},
            "fit_table": {"type": "string", "description": "Absolute path to pcell_fit.json."},
            "session": {"type": "string", "description": "KLayout session id/label/alias (default: primary)."},
        },
        "required": ["name", "fit_table"],
        "additionalProperties": False,
    },
)
def _tool_structdevice_register_pcell(ctx, arguments: dict) -> dict:
    try:
        client, close_after = ctx._session_scoped_client(
            arguments.get("session"))
        try:
            result = client.call("pcell.register_fitted", {
                "name": str(arguments["name"]),
                "fit_table": str(arguments["fit_table"])})
            result["next_action"] = (
                "instance.insert_pcell {parent: '<cell>', pcell: "
                f"'{arguments['name']}', library: 'klink_structdevice', "
                "params: {w_um: ..., l_um: ..., style: ...}} — or pick "
                "it in the KLayout GUI library browser")
        finally:
            if close_after:
                try:
                    client.close()
                except Exception:
                    pass
        return _json_result(result)
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "structdevice.spec_write",
    "Project a live cell into a klink.spec.json v1 fact file in one "
    "call: devices (recipe terminals), instances, declared nets (from "
    "structdevice.declare_nets), derived nets, and their "
    "reconciliation. layer_roles maps 'L/D' to a role name and is "
    "recorded as user_declared. The spec lands in "
    "<cell>.klink.spec.json next to the net table.",
    {
        "type": "object",
        "properties": {
            "cell": {"type": "string"},
            "layer_roles": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Map 'L/D' -> role, e.g. {'101/0': 'back_gate_metal'}.",
            },
            "session": {"type": "string"},
            "device_class": {"type": "string", "default": "device"},
            "conductors": {"type": "array", "items": {"type": "string"}},
            "vias": {"type": "array",
                     "items": {"type": "array", "items": {"type": "string"},
                               "minItems": 3, "maxItems": 3}},
        },
        "required": ["cell", "layer_roles"],
        "additionalProperties": False,
    },
)
def _tool_structdevice_spec_write(ctx, arguments: dict) -> dict:
    try:
        from ...domains.structdevice.orchestrators import write_spec_file

        client, close_after = ctx._session_scoped_client(
            arguments.get("session"))
        try:
            # klink ships no device terminal recipe (purity); write_spec_file
            # returns its instructive error (the lab flow injects a recipe from
            # your recipes).
            result = write_spec_file(
                client,
                str(arguments["cell"]),
                layer_roles=dict(arguments["layer_roles"]),
                spec_root=ctx._structdevice_spec_root(),
                device_class=str(
                    arguments.get("device_class") or "device"),
                connectivity=ctx._structdevice_connectivity(arguments),
            )
        finally:
            if close_after:
                try:
                    client.close()
                except Exception:
                    pass
        return _json_result(result)
    except Exception as exc:
        return _error_result(str(exc))

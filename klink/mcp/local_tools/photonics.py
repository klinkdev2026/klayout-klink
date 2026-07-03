"""photonics.* + port.harvest_blackbox local MCP tool handlers.

Blackbox port harvesting and SEND-driven optical net connect/reroute (gdsfactory
backend). Handlers are functions (ctx, arguments); the route-style builder, spec
root, and session-scoped client live on the bridge (ctx).
"""

from __future__ import annotations

from ..results import _error_result, _json_result
from . import local_tool
from ._helpers import _gdsfactory_unavailable_message


@local_tool(
    "port.harvest_blackbox",
    "Harvest optical ports from PDK blackbox instances in a cell via the waveguide stub convention (small stub boxes on the waveguide layer) and mark them as klink Ports. Ports are derived from LIVE instance positions: re-run after moving instances in the GUI to refresh them, then route. Net intent keys on identity-stable names {tag}{ordinal}_{stubIndex}.",
    {
        "type": "object",
        "properties": {
            "cell": {"type": "string", "description": "Parent cell holding the blackbox instances."},
            "tags": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Map child cell name -> short tag (children not listed are skipped).",
            },
            "nets": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Map harvested port name -> net. Unlisted ports get an empty net.",
            },
            "wg_layer": {"type": "string", "description": "your PDK waveguide layer 'L/D' (required; klink ships no default)."},
            "stub_size_um": {"type": "number", "description": "your PDK stub-box size in um (required; e.g. 0.5)."},
            "port_layer": {"type": "string", "default": "999/99"},
            "clear": {
                "type": "boolean",
                "default": True,
                "description": "Delete existing Port markers on port_layer in the cell first (idempotent refresh).",
            },
        },
        "required": ["cell", "tags", "wg_layer", "stub_size_um"],
        "additionalProperties": False,
    },
)
def _tool_port_harvest_blackbox(ctx, arguments: dict) -> dict:
    if not ctx.ensure_connected() or ctx._client is None:
        return _error_result("not connected to klink: %s" % (ctx._last_error or "unknown error"))
    try:
        from ...domains.photonics.blackbox import harvest_instance_ports, mark_ports

        cell = str(arguments.get("cell") or "")
        if not arguments.get("wg_layer") or arguments.get("stub_size_um") is None:
            return _error_result(
                "port.harvest_blackbox needs your PDK's stub convention; klink "
                "ships no default. Pass wg_layer (waveguide layer 'L/D') and "
                "stub_size_um (stub box size in um) from your pdk.py.")
        port_layer = str(arguments.get("port_layer") or "999/99")
        if arguments.get("clear", True):
            ctx._client.call("port.delete_all", {"cell": cell, "layer": port_layer})
        marks = harvest_instance_ports(
            ctx._client,
            cell,
            tags=dict(arguments.get("tags") or {}),
            nets=dict(arguments.get("nets") or {}),
            wg_layer=str(arguments["wg_layer"]),
            stub_size_um=float(arguments["stub_size_um"]),
            port_layer=port_layer,
        )
        mark_ports(ctx._client, marks)
        return _json_result({
            "ok": True,
            "cell": cell,
            "marked": len(marks),
            "ports": [
                {"name": m["name"], "net": m["net"], "center_um": m["center_um"],
                 "orientation": m["orientation"]}
                for m in marks
            ],
        })
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "photonics.import_gf",
    "Take over a FINISHED gdsfactory script into klink's interactive loop "
    "in one call: runs the user's .py in this (gdsfactory-capable) "
    "interpreter, takes the Component it builds, imports its DEVICE "
    "instances as real KLayout cells+instances (batch RPC), collapses its "
    "routed/snapped connections to device-level nets, persists per-device "
    "port templates + the net table in the spec, and routes the nets with "
    "klink (the script's own routes are replaced by klink-owned ones). "
    "Afterwards the user can DRAG components in KLayout and "
    "photonics.reroute (just the cell name) re-routes from live positions. "
    "The script is executed — only run files the user asked you to import.",
    {
        "type": "object",
        "properties": {
            "script_path": {"type": "string", "description": "Path to the user's gdsfactory .py file."},
            "component": {"type": "string", "description": "Name of a module-level Component variable OR zero-arg factory function in the script. Default: auto-detect (single Component in module globals, or a main()/build() factory)."},
            "cell": {"type": "string", "description": "Target KLayout cell name (default: GF_<component name>)."},
            "route_layer": {"type": "string", "description": "Layer to route on 'L/D'. Default: derived from the component's port layers when unambiguous."},
            "port_layer": {"type": "string", "default": "999/99"},
            "route": {"type": "boolean", "default": True, "description": "Route the collapsed nets immediately."},
            "session": {"type": "string", "description": "KLayout session id/label/alias to import into (default: primary connection)."},
        },
        "required": ["script_path"],
        "additionalProperties": False,
    },
)
def _tool_photonics_import_gf(ctx, arguments: dict) -> dict:
    try:
        import importlib.util
        import pathlib

        from ...domains.photonics.gf_import import import_gf_component

        script = pathlib.Path(str(arguments.get("script_path") or ""))
        if not script.is_file():
            return _error_result(
                "script_path %r is not a file; pass the user's gdsfactory "
                ".py script" % str(script))
        spec = importlib.util.spec_from_file_location(
            "klink_gf_user_script_%s" % script.stem, script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        target = None
        wanted = arguments.get("component")
        candidates = {}
        for name, value in vars(module).items():
            if name.startswith("_"):
                continue
            if hasattr(value, "get_netlist") and hasattr(value, "insts"):
                candidates[name] = value
        if wanted:
            target = getattr(module, str(wanted), None)
            if callable(target) and not hasattr(target, "get_netlist"):
                target = target()
            if target is None or not hasattr(target, "get_netlist"):
                return _error_result(
                    "%r in %s is not a gdsfactory Component or a factory "
                    "returning one; module-level Components found: %s"
                    % (wanted, script.name, sorted(candidates) or "none"))
        elif len(candidates) == 1:
            target = next(iter(candidates.values()))
        else:
            for fn_name in ("main", "build"):
                fn = getattr(module, fn_name, None)
                if callable(fn):
                    maybe = fn()
                    if hasattr(maybe, "get_netlist"):
                        target = maybe
                        break
            if target is None:
                return _error_result(
                    "cannot auto-pick the Component in %s (found: %s); pass "
                    "component=<variable or factory name>"
                    % (script.name, sorted(candidates) or "none"))

        client = ctx._client
        close_after = False
        if arguments.get("session"):
            client = ctx._connect_session_client(str(arguments["session"]))
            close_after = True
        elif not ctx.ensure_connected() or ctx._client is None:
            return _error_result("not connected to klink: %s" % (ctx._last_error or "unknown error"))
        else:
            client = ctx._client
        try:
            result = import_gf_component(
                client, target,
                cell=arguments.get("cell"),
                port_layer=str(arguments.get("port_layer") or "999/99"),
                route_layer=arguments.get("route_layer"),
                spec_root=ctx._photonics_spec_root(),
                route=bool(arguments.get("route", True)),
            )
        finally:
            if close_after:
                try:
                    client.close()
                except Exception:
                    pass
        return _json_result(result)
    except RuntimeError as exc:
        return _error_result(_gdsfactory_unavailable_message(str(exc)))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "photonics.connect",
    "Connect ports the user just SENT, in one call: reads the latest N "
    "explicit SEND selections, turns them into port pairs (one SEND "
    "framing two klink Port markers = one pair; single-marker SENDs pair "
    "consecutively), auto-names nets, persists the net table, re-harvests "
    "ports from live instance positions, and routes with gdsfactory. "
    "Workflow: user presses SEND for each pair -> call this with "
    "recent_sends. The KLayout session is derived from the SENDs "
    "automatically. On problems it returns instructions, never guesses.",
    {
        "type": "object",
        "properties": {
            "recent_sends": {
                "type": "integer", "minimum": 1, "maximum": 20,
                "description": "How many of the latest SEND selections to consume.",
            },
            "cell": {"type": "string", "description": "Override target cell (default: from the SENDs)."},
            "width_um": {"type": "number", "description": "Waveguide width override."},
            "radius_um": {"type": "number", "description": "Bend radius override."},
            "separation_um": {"type": "number", "default": 3.0},
            "wg_layer": {"type": "string", "description": "your PDK waveguide/stub-marker layer 'L/D'. Required for stub-convention cells; NOT needed for cells imported via photonics.import_gf (templates persisted)."},
            "stub_size_um": {"type": "number", "description": "your PDK stub-box size in um (stub convention only; e.g. 0.5)."},
            "route_layer": {"type": "string", "description": "layer to route on 'L/D'. Required unless the cell was imported via photonics.import_gf (spec carries a default)."},
        },
        "required": ["recent_sends"],
        "additionalProperties": False,
    },
)
def _tool_photonics_connect(ctx, arguments: dict) -> dict:
    try:
        from ...domains.photonics.net_intent import connect_and_route

        sends = [r for r in ctx._context.recent(int(arguments["recent_sends"]))]
        if not sends:
            return _error_result(
                "no SEND selections recorded; ask the user to select port "
                "markers in KLayout and press the SEND toolbar action first"
            )
        session_ids = {str(s.get("klayout_session_id") or "") for s in sends}
        session_ids.discard("")
        if len(session_ids) > 1:
            return _error_result(
                f"the SENDs come from multiple KLayout sessions {sorted(session_ids)}; "
                "lower recent_sends so only one session's SENDs are consumed"
            )
        client = ctx._client
        close_after = False
        if session_ids:
            client = ctx._connect_session_client(next(iter(session_ids)))
            close_after = True
        elif not ctx.ensure_connected() or ctx._client is None:
            return _error_result("not connected to klink: %s" % (ctx._last_error or "unknown error"))
        else:
            client = ctx._client
        try:
            result = connect_and_route(
                client,
                sends=sends,
                cell=arguments.get("cell"),
                style=ctx._photonics_style(arguments),
                wg_layer=arguments.get("wg_layer"),
                stub_size_um=(float(arguments["stub_size_um"])
                              if arguments.get("stub_size_um") is not None else None),
                route_layer=arguments.get("route_layer"),
                spec_root=ctx._photonics_spec_root(),
            )
        finally:
            if close_after:
                try:
                    client.close()
                except Exception:
                    pass
        return _json_result(result)
    except RuntimeError as exc:
        return _error_result(_gdsfactory_unavailable_message(str(exc)))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "photonics.reroute",
    "Re-route a cell whose connections were made with photonics.connect, "
    "after the user moved components. Reads the persisted net table, "
    "re-harvests ports from live instance positions, routes, writes back. "
    "Needs only the cell name (plus session when not the primary one).",
    {
        "type": "object",
        "properties": {
            "cell": {"type": "string"},
            "session": {"type": "string", "description": "KLayout session id/label/alias holding the cell (default: primary connection)."},
            "wg_layer": {"type": "string", "description": "your PDK waveguide/stub-marker layer 'L/D'. Required for stub-convention cells; NOT needed for cells imported via photonics.import_gf (templates persisted)."},
            "stub_size_um": {"type": "number", "description": "your PDK stub-box size in um (stub convention only; e.g. 0.5)."},
            "route_layer": {"type": "string", "description": "layer to route on 'L/D'. Required unless the cell was imported via photonics.import_gf (spec carries a default)."},
        },
        "required": ["cell"],
        "additionalProperties": False,
    },
)
def _tool_photonics_reroute(ctx, arguments: dict) -> dict:
    try:
        from ...domains.photonics.net_intent import reroute

        client = ctx._client
        close_after = False
        if arguments.get("session"):
            client = ctx._connect_session_client(str(arguments["session"]))
            close_after = True
        elif not ctx.ensure_connected() or ctx._client is None:
            return _error_result("not connected to klink: %s" % (ctx._last_error or "unknown error"))
        else:
            client = ctx._client
        try:
            result = reroute(
                client,
                cell=str(arguments.get("cell") or ""),
                wg_layer=arguments.get("wg_layer"),
                stub_size_um=(float(arguments["stub_size_um"])
                              if arguments.get("stub_size_um") is not None else None),
                route_layer=arguments.get("route_layer"),
                spec_root=ctx._photonics_spec_root(),
            )
        finally:
            if close_after:
                try:
                    client.close()
                except Exception:
                    pass
        return _json_result(result)
    except RuntimeError as exc:
        return _error_result(_gdsfactory_unavailable_message(str(exc)))
    except Exception as exc:
        return _error_result(str(exc))

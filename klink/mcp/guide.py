"""klink.guide — the orientation entry point (AGENT_TOOL_DESIGN 7b).

An agent that knows nothing about this stack calls klink.guide first
(or is pointed at it by the harness) and receives: what is open, what
intent state already exists on disk, and the LITERAL next call for
each available user intention.  The workflow must never live in the
agent's head; this tool is where it lives instead.

Pure functions here; the bridge handler supplies live connection facts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# one row per user intention: trigger phrases first, literal call second.
# Weak agents copy the call verbatim and substitute <cell>.
INTENTIONS: List[Dict[str, str]] = [
    {"when": "the user framed wiring/terminals in KLayout, pressed SEND, "
             "and says these belong together / connect these",
     "call": "structdevice.declare_nets {recent_sends: 1, cell: '<cell>', "
             "conductors: ['<L/D>', ...], vias: [['<lo>','<cut>','<hi>']]}  "
             "# conductors/vias from your pdk.py; klink ships no process"},
    {"when": "declared nets exist and the user wants them wired",
     "call": "structdevice.connect_nets {cell: '<cell>', conductors: [...], "
             "vias: [...], route_layer: '<L/D>', via_cell: '<cell>', "
             "route_width_um: <um>}  # process args from your pdk.py"},
    {"when": "the user asks whether the drawn wiring matches the intent "
             "(pre-tapeout check, LVS)",
     "call": "structdevice.lvs_check {cell: '<cell>', conductors: [...], "
             "vias: [...]}  # conductors/vias from your pdk.py"},
    {"when": "the user wants the machine-readable fact file for a cell",
     "call": "structdevice.spec_write {cell: '<cell>', layer_roles: {...}}"},
    {"when": "the user provides a device-level netlist and wants a "
             "placed+wired+verified circuit cell built from it",
     "call": "structdevice.build_from_netlist {cell: '<new cell>', "
             "netlist: {instances: [...], nets: [...]}}"},
    {"when": "the user wants a hand-drawn device family available as a "
             "parametric PCell (after the fitter produced a table)",
     "call": "structdevice.register_pcell {name: '<DeviceName>', "
             "fit_table: '<path to pcell_fit.json>'}"},
    {"when": "the user SENT photonic Port markers and says connect them",
     "call": "photonics.connect {recent_sends: <n>, wg_layer: '<L/D>', "
             "stub_size_um: <um>, route_layer: '<L/D>'}  # PDK args from your pdk.py"},
    {"when": "the user moved photonic components and wants routes redone",
     "call": "photonics.reroute {cell: '<cell>', wg_layer: '<L/D>', "
             "stub_size_um: <um>, route_layer: '<L/D>'}  # PDK args from your pdk.py"},
    {"when": "you need to see what is open before anything else",
     "call": "layout.info {} then cell.list {}"},
    {"when": "the user refers to something they just SENT",
     "call": "interaction.selection.latest {}"},
]

RULES: List[str] = [
    "Every structdevice result carries next_action: do that, verbatim.",
    "Relay `problems` to the user word for word; never improvise wiring, "
    "pairing, or coordinates yourself.",
    "Never call view.screenshot unless the user explicitly asks for an "
    "image.",
]


def scan_spec_root(spec_root: str) -> List[Dict[str, Any]]:
    """Inventory the on-disk intent state, one entry per cell."""
    root = Path(spec_root)
    if not root.exists():
        return []
    cells: Dict[str, Dict[str, Any]] = {}

    def entry(cell: str) -> Dict[str, Any]:
        return cells.setdefault(cell, {"cell": cell})

    for p in sorted(root.glob("*.elec_nets.json")):
        cell = p.name[: -len(".elec_nets.json")]
        try:
            nets = json.loads(p.read_text(encoding="utf-8")).get("nets", [])
            entry(cell)["declared_nets"] = len(nets)
        except Exception:
            entry(cell)["declared_nets"] = "unreadable"
    for p in sorted(root.glob("*.lvs.json")):
        cell = p.name[: -len(".lvs.json")]
        try:
            entry(cell)["lvs_ok"] = bool(
                json.loads(p.read_text(encoding="utf-8")).get("ok"))
        except Exception:
            entry(cell)["lvs_ok"] = "unreadable"
    for p in sorted(root.glob("*.klink.spec.json")):
        cell = p.name[: -len(".klink.spec.json")]
        entry(cell)["spec_path"] = str(p)
    for p in sorted(root.glob("*.netlist.json")):
        cell = p.name[: -len(".netlist.json")]
        entry(cell)["extracted_netlist_path"] = str(p)
    return [cells[k] for k in sorted(cells)]


def suggest_next(state: List[Dict[str, Any]]) -> Optional[str]:
    """One concrete suggestion derived from the on-disk state."""
    for s in state:
        if s.get("declared_nets") and s.get("lvs_ok") is None \
                and isinstance(s.get("declared_nets"), int):
            return (f"cell {s['cell']!r} has {s['declared_nets']} declared "
                    "net(s) but no LVS report yet -> structdevice.connect_nets "
                    f"{{cell: '{s['cell']}', conductors: [...], vias: [...], "
                    "route_layer: '<L/D>', via_cell: '<cell>', route_width_um: <um>}"
                    " (process args from your pdk.py)")
        if s.get("lvs_ok") is False:
            return (f"cell {s['cell']!r} has a FAILING LVS report -> read "
                    f".klink/specs/{s['cell']}.lvs.json problems and relay "
                    "them to the user")
        if s.get("lvs_ok") is True and "spec_path" not in s:
            return (f"cell {s['cell']!r} is LVS-clean but has no spec file "
                    f"-> structdevice.spec_write {{cell: '{s['cell']}', "
                    "layer_roles: {...}}")
    return None


def guide_payload(
    spec_root: str,
    *,
    connection: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state = scan_spec_root(spec_root)
    payload: Dict[str, Any] = {
        "you_are_here": (
            "klink controls a live KLayout session over MCP. Workflows are "
            "walked through tool RESULTS: every orchestrated call returns "
            "next_action. Start from the intentions below; state persists "
            f"under {spec_root}."),
        "connection": connection or {"connected": False,
                                     "next_action": "klink.reconnect {}"},
        "state_on_disk": state,
        "intentions": INTENTIONS,
        "rules": RULES,
    }
    suggestion = suggest_next(state)
    if suggestion:
        payload["suggested_next_action"] = suggestion
    return payload

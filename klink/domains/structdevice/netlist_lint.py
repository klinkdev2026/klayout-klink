"""Netlist lint -- validate a device netlist (devnet) BEFORE placement/route/LVS.

The devnet contract (what yosys export produces and what a HAND-WRITTEN netlist
must also satisfy):

    {"instances":      [{"instance_id": "X1", "device_cell": "<device key>"}],
     "nets":           [{"net_id": "A[0]", "terminals": ["X1.G", "X2.D", ...]}],
     "groups":         [{"group": "<name>", "gate_type": "<label>",
                         "instances": ["X1", "X2"]}],      # one placement column
     "required_cells": ["<device key>", ...]}              # optional

Placement packs each GROUP as one column of stacked devices, so every instance
must belong to exactly one group; a terminal ref is "<instance_id>.<terminal>"
where <terminal> is a terminal NAME of that instance's device (arbitrary N-ary
names -- nothing assumes S/G/D). This linter checks exactly the assumptions the
engine makes and reports them as instructions, so a hand-written netlist fails
HERE with a fix, never deep inside placement or routing.

Pure mechanism: no process data, no device library -- pass ``device_terms``
(the ``terms`` table from ``layout_engine._geom_tables``) to also check
terminal names against the device geometry.

Usage:
    report = lint_netlist(netlist, device_terms=terms)
    if not report["ok"]:
        for e in report["errors"]: print(e["message"], "->", e["next_action"])
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping, Optional

__all__ = ["lint_netlist"]


def _item(kind: str, message: str, next_action: str) -> dict:
    return {"type": kind, "message": message, "next_action": next_action}


def lint_netlist(netlist: Mapping[str, Any], *,
                 device_terms: Optional[Mapping[str, Any]] = None,
                 power_nets=("VDD", "GND")) -> dict:
    """Validate ``netlist`` against the engine's real assumptions. Returns
    {"ok": bool, "errors": [...], "warnings": [...], "stats": {...}} where every
    entry carries type/message/next_action (errors are instructions). ``ok`` is
    False iff there is at least one error; warnings never block."""
    errors, warnings = [], []

    # E0: structure -------------------------------------------------------
    for key, want in (("instances", list), ("nets", list), ("groups", list)):
        if not isinstance(netlist.get(key), want):
            errors.append(_item(
                "bad_structure", f"netlist[{key!r}] missing or not a list",
                f"provide netlist[{key!r}] as a list (see the devnet contract "
                "in klink.domains.structdevice.netlist_lint's docstring)"))
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings, "stats": {}}

    insts = netlist["instances"]
    nets = netlist["nets"]
    groups = netlist["groups"]

    # E1: instances -------------------------------------------------------
    ids = [i.get("instance_id") for i in insts]
    for i, rec in enumerate(insts):
        if not rec.get("instance_id") or not isinstance(rec.get("instance_id"), str):
            errors.append(_item("bad_instance", f"instances[{i}] has no instance_id",
                                "give every instance a unique non-empty string id"))
        if not rec.get("device_cell") or not isinstance(rec.get("device_cell"), str):
            errors.append(_item("bad_instance",
                                f"instances[{i}] ({rec.get('instance_id')!r}) has no device_cell",
                                "set device_cell to a device key of your device library"))
    for xid, n in Counter(x for x in ids if x).items():
        if n > 1:
            errors.append(_item("duplicate_instance", f"instance_id {xid!r} appears {n} times",
                                "instance ids must be unique"))
    known = {x for x in ids if x}
    cell_of = {i.get("instance_id"): i.get("device_cell") for i in insts}

    # E2: device cells vs geometry/library --------------------------------
    if device_terms is not None:
        for xid, cellk in cell_of.items():
            if cellk and cellk not in device_terms:
                errors.append(_item(
                    "unknown_device", f"instance {xid!r} uses device_cell {cellk!r} "
                    f"which is not in the device geometry/library "
                    f"(known: {sorted(device_terms)[:8]}...)" if len(device_terms) > 8 else
                    f"instance {xid!r} uses device_cell {cellk!r} which is not in the "
                    f"device geometry/library (known: {sorted(device_terms)})",
                    "add the device to your example's device library + geometry, "
                    "or fix the device_cell spelling"))

    # E3: groups = placement columns --------------------------------------
    seen_in_group: dict = {}
    for gi, grp in enumerate(groups):
        gname = grp.get("group", f"groups[{gi}]")
        members = grp.get("instances")
        if not isinstance(members, list) or not members:
            errors.append(_item("bad_group", f"group {gname!r} has no instances list",
                                "every group needs a non-empty instances list "
                                "(a group = one placed column of stacked devices)"))
            continue
        for xi in members:
            if xi not in known:
                errors.append(_item("unknown_instance_in_group",
                                    f"group {gname!r} references unknown instance {xi!r}",
                                    "every group member must exist in netlist['instances']"))
            elif xi in seen_in_group:
                errors.append(_item("instance_in_two_groups",
                                    f"instance {xi!r} is in groups {seen_in_group[xi]!r} "
                                    f"and {gname!r}",
                                    "an instance is placed exactly once; keep it in ONE group"))
            else:
                seen_in_group[xi] = gname
    ungrouped = known - set(seen_in_group)
    for xi in sorted(ungrouped):
        errors.append(_item("ungrouped_instance",
                            f"instance {xi!r} is in no group -- placement packs groups, "
                            "so it would never be placed",
                            "add it to a group (a single-instance group is fine)"))

    # E4/E5: nets + terminal refs ------------------------------------------
    for nid, n in Counter(n.get("net_id") for n in nets if n.get("net_id")).items():
        if n > 1:
            errors.append(_item("duplicate_net", f"net_id {nid!r} appears {n} times",
                                "merge the terminal lists into one net entry"))
    term_use: dict = defaultdict(list)
    for ni, net in enumerate(nets):
        nid = net.get("net_id")
        if not nid or not isinstance(nid, str):
            errors.append(_item("bad_net", f"nets[{ni}] has no net_id",
                                "give every net a unique non-empty string net_id"))
            continue
        trefs = net.get("terminals")
        if not isinstance(trefs, list):
            errors.append(_item("bad_net", f"net {nid!r} has no terminals list",
                                "provide terminals as a list of '<instance>.<terminal>'"))
            continue
        if len(trefs) < 2:
            warnings.append(_item("dangling_net",
                                  f"net {nid!r} has {len(trefs)} terminal(s) -- nothing to route",
                                  "connect at least 2 terminals or drop the net"))
        for ref in trefs:
            if not isinstance(ref, str) or "." not in ref:
                errors.append(_item("bad_terminal_ref",
                                    f"net {nid!r} terminal {ref!r} is not '<instance>.<terminal>'",
                                    "write terminal refs as e.g. 'X7.G'"))
                continue
            xi, t = ref.rsplit(".", 1)
            if xi not in known:
                errors.append(_item("unknown_instance",
                                    f"net {nid!r} references unknown instance {xi!r} ({ref})",
                                    "every terminal's instance must exist in netlist['instances']"))
                continue
            if device_terms is not None:
                cellk = cell_of.get(xi)
                tt = device_terms.get(cellk, {})
                if tt and t not in tt:
                    errors.append(_item(
                        "unknown_terminal",
                        f"net {nid!r}: {ref} names terminal {t!r} but device "
                        f"{cellk!r} has terminals {sorted(tt)}",
                        "use one of the device's terminal names (N-ary, any labels)"))
            term_use[ref].append(nid)
    for ref, on in term_use.items():
        if len(on) > 1:
            errors.append(_item("terminal_on_two_nets",
                                f"terminal {ref} is on nets {on} -- a short by construction",
                                "a terminal belongs to exactly one net; fix the netlist"))

    # W: soft checks --------------------------------------------------------
    present = {n.get("net_id") for n in nets}
    for pn in power_nets:
        if pn not in present:
            warnings.append(_item("no_power_net",
                                  f"power net {pn!r} not in the netlist -- the PDN pass "
                                  "will have no taps for it",
                                  "fine for a pure-signal experiment; otherwise add the "
                                  "power net with its device terminals"))
    if device_terms is not None:
        for xi in sorted(known):
            tt = device_terms.get(cell_of.get(xi), {})
            used = {ref.rsplit(".", 1)[1] for ref in term_use if ref.startswith(xi + ".")}
            for t in sorted(set(tt) - used):
                warnings.append(_item("floating_terminal",
                                      f"{xi}.{t} is connected to no net",
                                      "intentional for unused terminals; otherwise wire it"))
    req = netlist.get("required_cells")
    if isinstance(req, list):
        actual = sorted({c for c in cell_of.values() if c})
        if sorted(req) != actual:
            warnings.append(_item("required_cells_mismatch",
                                  f"required_cells {sorted(req)} != device cells used {actual}",
                                  "required_cells is derived metadata; update or drop it"))

    stats = {"instances": len(insts), "nets": len(nets), "groups": len(groups),
             "terminal_refs": sum(len(v) for v in term_use.values()),
             "errors": len(errors), "warnings": len(warnings)}
    return {"ok": not errors, "errors": errors, "warnings": warnings, "stats": stats}

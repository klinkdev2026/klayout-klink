"""LVS-lite: reconcile declared nets against geometry-derived nets.

Structure-as-Device M3 second half (docs/STRUCTURE_AS_DEVICE_IR.md
§3.3).  ``declared`` is the design intent (which terminals SHOULD be
connected); ``derived`` is what the drawn metal actually connects
(connectivity.terminal_net_table).  The reconciler reports every
disagreement as an instruction-grade finding — catching a mis-drawn
wire before tapeout is the entire point of this module.

Findings vocabulary:

- ``open``     a declared net's terminals land on different derived
               nets (the drawn wiring does not connect them)
- ``short``    two declared nets land on the same derived net
- ``floating`` a declared terminal hits no conducting geometry
- ``unknown``  a declared terminal ref is absent from the derived table
- info ``undeclared``: terminals or extra derived co-members that no
  declared net covers (reported, not failed — probing pads and rails
  often legitimately exceed the declaration)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence, Tuple


class DeclarationError(ValueError):
    """Bad declared-net input.  Messages instruct."""


@dataclass(frozen=True)
class DeclaredNet:
    """One declared net: a name plus ``instance.terminal`` refs."""

    net_id: str
    terminals: Tuple[str, ...]

    def validated(self) -> "DeclaredNet":
        if not self.net_id:
            raise DeclarationError("declared net needs a non-empty net_id")
        if not self.terminals:
            raise DeclarationError(
                f"declared net {self.net_id!r} has no terminals"
            )
        for ref in self.terminals:
            if "." not in ref:
                raise DeclarationError(
                    f"net {self.net_id!r}: terminal ref {ref!r} must be "
                    "'instance.terminal'"
                )
        return self


def declared_nets_from_dicts(items: Sequence[Mapping[str, Any]]) -> List[DeclaredNet]:
    """Build declarations from JSON-shaped dicts:
    ``[{"net": "OUT", "terminals": ["X1.D", "X2.S"]}, ...]``."""
    nets = [
        DeclaredNet(
            net_id=str(item.get("net", "")),
            terminals=tuple(item.get("terminals", ())),
        ).validated()
        for item in items
    ]
    seen_net = set()
    seen_ref: Dict[str, str] = {}
    for net in nets:
        if net.net_id in seen_net:
            raise DeclarationError(f"duplicate declared net {net.net_id!r}")
        seen_net.add(net.net_id)
        for ref in net.terminals:
            if ref in seen_ref:
                raise DeclarationError(
                    f"terminal {ref!r} is declared in both "
                    f"{seen_ref[ref]!r} and {net.net_id!r}; a terminal "
                    "belongs to exactly one net."
                )
            seen_ref[ref] = net.net_id
    return nets


def align_declared_by_position(
    declared: Sequence[Mapping[str, Any]],
    device_terms: Mapping[str, Mapping[str, Sequence[float]]],
    layout_term_pos: Mapping[str, Mapping[str, Sequence[float]]],
    *,
    tol: float = 0.1,
) -> Tuple[List[Dict[str, Any]], List[str], int]:
    """Rename declared net terminal refs to the LAYOUT's device names, matching
    each device by its terminal-POSITION set (not by name/order).

    KLayout does not return many instances in `instance_query`/insertion order,
    so the extractor numbers layout devices X1..Xn differently than the declared
    netlist -> a name-based reconcile compares the wrong physical devices (false
    opens/shorts at scale; see LESSONS #85). Position is the stable identity.

    - ``declared``: ``[{"net": .., "terminals": ["X1.D", ...]}, ...]``
    - ``device_terms``: ``{declared_instance_id: {terminal: [x, y]}}`` -- where
      the declared devices were placed (persisted by the build step).
    - ``layout_term_pos``: ``{layout_instance_id: {terminal: [x, y]}}`` -- from
      the live layout (collect_placed_terminals).

    Returns ``(remapped_declared, problems, n_aligned)``. Pure: no I/O, no
    mutation -- the caller stays validate-before-mutate.
    """

    def key(term_pos):
        return frozenset(
            (str(t), (round(float(p[0]) / tol) * tol, round(float(p[1]) / tol) * tol))
            for t, p in term_pos.items()
        )

    lay_key2id: Dict[Any, str] = {key(tp): inst for inst, tp in layout_term_pos.items()}
    decl2lay: Dict[str, str] = {}
    problems: List[str] = []
    for inst, tp in sorted(device_terms.items()):
        k = key(tp)
        if k in lay_key2id:
            decl2lay[inst] = lay_key2id[k]
        else:
            where = sorted((t, [round(float(p[0]), 1), round(float(p[1]), 1)]) for t, p in tp.items())
            problems.append(
                f"device-align: declared device {inst!r} (terminals at {where}) has no "
                "layout device at those positions; the drawn devices do not match the "
                "declared placement -- redraw/place the devices, then call lvs_check again."
            )
    remapped: List[Dict[str, Any]] = []
    for net in declared:
        refs = []
        for ref in net.get("terminals", ()):
            inst, _, term = str(ref).partition(".")
            refs.append(f"{decl2lay.get(inst, inst)}.{term}")
        remapped.append({"net": net.get("net", net.get("net_id")), "terminals": refs})
    return remapped, problems, len(decl2lay)


def reconcile(
    declared: Sequence[DeclaredNet],
    derived_table: Mapping[str, Any],
) -> Dict[str, Any]:
    """Compare declarations against a connectivity terminal_net_table.

    Returns ``{ok, matches, problems, infos}``; ``ok`` is True iff
    there are zero problems.  ``matches`` maps declared net_id to the
    derived net id it landed on.
    """

    derived_by_ref: Dict[str, Any] = {}
    for row in derived_table.get("rows", ()):
        derived_by_ref[f"{row['instance']}.{row['terminal']}"] = row["net_id"]

    problems: List[str] = []
    infos: List[str] = []
    matches: Dict[str, str] = {}
    declared_refs = set()
    derived_owner: Dict[str, str] = {}  # derived net -> declared net

    for net in declared:
        net.validated()
        groups: Dict[Any, List[str]] = {}
        for ref in net.terminals:
            declared_refs.add(ref)
            if ref not in derived_by_ref:
                problems.append(
                    f"unknown: {net.net_id!r} references {ref!r} which is "
                    "not in the derived table; check instance/terminal "
                    "names against the recipe output."
                )
                continue
            groups.setdefault(derived_by_ref[ref], []).append(ref)
        if None in groups:
            problems.append(
                f"floating: {net.net_id!r} terminal(s) "
                f"{sorted(groups[None])} hit no conducting geometry."
            )
            del groups[None]
        if len(groups) > 1:
            split = {k: sorted(v) for k, v in sorted(groups.items())}
            problems.append(
                f"open: {net.net_id!r} is split across derived nets "
                f"{split}; the drawn wiring does not connect them."
            )
            continue
        if len(groups) == 1:
            derived_net = next(iter(groups))
            if derived_net in derived_owner:
                problems.append(
                    f"short: {derived_owner[derived_net]!r} and "
                    f"{net.net_id!r} land on the same derived net "
                    f"{derived_net}; the drawn wiring connects nets that "
                    "were declared separate."
                )
                continue
            derived_owner[derived_net] = net.net_id
            matches[net.net_id] = derived_net

    for ref, derived_net in sorted(derived_by_ref.items()):
        if ref in declared_refs:
            continue
        if derived_net in derived_owner:
            infos.append(
                f"undeclared: {ref} shares derived net {derived_net} with "
                f"declared net {derived_owner[derived_net]!r} but is not "
                "part of its declaration."
            )
        else:
            infos.append(f"undeclared: {ref} is not covered by any declared net.")

    return {
        "ok": not problems,
        "matches": matches,
        "problems": problems,
        "infos": infos,
    }

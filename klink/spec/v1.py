"""klink.spec.json v1 — the engineering-fact projection of a layout.

Contract designed by the main lane (docs/STRUCTURE_AS_DEVICE_IR.md §4;
the parked Codex prototype was reference only, this is a fresh design).

Principles, enforced structurally where possible:

- The spec is a PROJECTION of layout facts, not a hand-maintained
  document: derived entries are generated from geometry (recipes,
  connectivity) and say so in their ``source``; everything hand-stated
  carries ``source: "user_declared"``.
- Every devices/process/nets entry MUST carry a ``source``.
- The spec records parameters, assumptions, and sources — never
  validity claims.  Reconciliation results are facts about agreement
  between declaration and geometry, not endorsements of the design.
- v1 scope is deliberately cut to: layout / process.layers / devices /
  instances / nets (declared + derived + reconciliation) / sites /
  marks / maps / assumptions.  Nothing else.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

SCHEMA_VERSION = "klink.spec/1"

SOURCE_USER = "user_declared"


class SpecError(ValueError):
    """Spec construction/validation failure.  Messages instruct."""


def build_spec(
    *,
    layout: Mapping[str, Any],
    process_layers: Sequence[Mapping[str, Any]],
    devices: Sequence[Mapping[str, Any]],
    instances: Sequence[Mapping[str, Any]],
    nets: Optional[Mapping[str, Any]] = None,
    sites: Sequence[Mapping[str, Any]] = (),
    marks: Sequence[Mapping[str, Any]] = (),
    maps: Optional[Mapping[str, Any]] = None,
    assumptions: Sequence[Mapping[str, Any]] = (),
    stack: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble and validate a v1 spec dict.  Raises SpecError on the
    first structural violation (build time is the cheapest time to
    catch a dirty fact).

    ``stack`` (optional) is the process-stack declaration
    (process.stack): conductors + via cells, the single source of
    layer relations consumed by both routing and LVS
    (docs/FEATURE_GRID_ROUTER_DESIGN F0). It must reference only layers
    that appear in process.layers."""

    process: Dict[str, Any] = {"layers": [dict(e) for e in process_layers]}
    if stack is not None:
        process["stack"] = dict(stack)
    spec = {
        "schema_version": SCHEMA_VERSION,
        "layout": dict(layout),
        "process": process,
        "devices": [dict(e) for e in devices],
        "instances": [dict(e) for e in instances],
        "nets": dict(nets) if nets else {"declared": [], "derived": []},
        "sites": [dict(e) for e in sites],
        "marks": [dict(e) for e in marks],
        "maps": dict(maps) if maps else {},
        "assumptions": [dict(e) for e in assumptions],
    }
    problems = validate_spec(spec)
    if problems:
        raise SpecError(
            "spec is structurally invalid:\n- " + "\n- ".join(problems)
        )
    return spec


def _require(entry: Mapping[str, Any], key: str, where: str,
             problems: List[str]) -> Any:
    value = entry.get(key)
    if value in (None, "", [], {}):
        problems.append(f"{where}: missing required field {key!r}")
    return value


def validate_spec(spec: Mapping[str, Any]) -> List[str]:
    """Structural validation.  Returns a list of instruction-grade
    problems; empty list means the spec is well-formed."""

    problems: List[str] = []

    if spec.get("schema_version") != SCHEMA_VERSION:
        problems.append(
            f"schema_version must be {SCHEMA_VERSION!r}, got "
            f"{spec.get('schema_version')!r}"
        )

    layout = spec.get("layout") or {}
    _require(layout, "top_cell", "layout", problems)
    dbu = layout.get("dbu")
    if not isinstance(dbu, (int, float)) or dbu <= 0:
        problems.append("layout.dbu must be a positive number")

    seen_ld = set()
    role_by_ld: Dict[str, str] = {}
    for i, entry in enumerate((spec.get("process") or {}).get("layers", [])):
        where = f"process.layers[{i}]"
        layer = entry.get("layer")
        datatype = entry.get("datatype")
        if not isinstance(layer, int) or not isinstance(datatype, int):
            problems.append(f"{where}: layer/datatype must be integers")
            continue
        key = f"{layer}/{datatype}"
        if key in seen_ld:
            problems.append(f"{where}: duplicate layer {key}")
        seen_ld.add(key)
        _require(entry, "role", where, problems)
        _require(entry, "source", where, problems)
        role_by_ld[key] = entry.get("role", "")

    # process.stack (optional): conductors + via cells, every layer it
    # names must be a declared process layer (single source of truth —
    # the stack cannot invent layers the fact file does not record)
    stack = (spec.get("process") or {}).get("stack")
    if stack is not None:
        if not isinstance(stack, Mapping):
            problems.append("process.stack must be a mapping")
        else:
            conds = stack.get("conductors") or []
            if not conds:
                problems.append("process.stack.conductors is empty")
            for ci, c in enumerate(conds):
                cl = c.get("layer") if isinstance(c, Mapping) else c
                if isinstance(cl, str) and cl not in seen_ld:
                    problems.append(
                        f"process.stack.conductors[{ci}] layer {cl!r} is "
                        "not a declared process.layers entry")
            for vi, v in enumerate(stack.get("vias") or []):
                if not isinstance(v, Mapping):
                    problems.append(f"process.stack.vias[{vi}] must be a mapping")
                    continue
                if not v.get("via_cell"):
                    problems.append(
                        f"process.stack.vias[{vi}] missing via_cell")
                for fld in ("from", "via_layer", "to"):
                    lv = v.get(fld)
                    if isinstance(lv, str) and lv not in seen_ld:
                        problems.append(
                            f"process.stack.vias[{vi}].{fld} layer {lv!r} "
                            "is not a declared process.layers entry")

    device_terms: Dict[str, set] = {}
    for i, dev in enumerate(spec.get("devices", [])):
        where = f"devices[{i}]"
        dev_id = _require(dev, "device_id", where, problems)
        _require(dev, "device_class", where, problems)
        _require(dev, "cell", where, problems)
        _require(dev, "source", where, problems)
        if dev_id in device_terms:
            problems.append(f"{where}: duplicate device_id {dev_id!r}")
            continue
        names = set()
        for j, term in enumerate(dev.get("terminals", [])):
            twhere = f"{where}.terminals[{j}]"
            name = _require(term, "name", twhere, problems)
            _require(term, "layer", twhere, problems)
            if term.get("center_um") is None:
                problems.append(f"{twhere}: missing center_um")
            if name in names:
                problems.append(f"{twhere}: duplicate terminal {name!r}")
            names.add(name)
        if dev_id:
            device_terms[dev_id] = names

    inst_device: Dict[str, str] = {}
    for i, inst in enumerate(spec.get("instances", [])):
        where = f"instances[{i}]"
        inst_id = _require(inst, "instance_id", where, problems)
        dev_id = _require(inst, "device_id", where, problems)
        if dev_id and dev_id not in device_terms:
            problems.append(
                f"{where}: device_id {dev_id!r} is not in devices[]"
            )
        if inst_id in inst_device:
            problems.append(f"{where}: duplicate instance_id {inst_id!r}")
        trans = inst.get("transform") or {}
        for k in ("dx_um", "dy_um"):
            if not isinstance(trans.get(k), (int, float)):
                problems.append(f"{where}.transform: missing numeric {k}")
        if inst_id and dev_id:
            inst_device[inst_id] = dev_id

    def _check_ref(ref: str, where: str) -> None:
        if "." not in ref:
            problems.append(
                f"{where}: terminal ref {ref!r} must be "
                "'instance_id.terminal'"
            )
            return
        inst_id, term = ref.split(".", 1)
        dev_id = inst_device.get(inst_id)
        if dev_id is None:
            problems.append(f"{where}: unknown instance {inst_id!r}")
        elif term not in device_terms.get(dev_id, set()):
            problems.append(
                f"{where}: device {dev_id!r} has no terminal {term!r}"
            )

    nets = spec.get("nets") or {}
    seen_net = set()
    seen_ref: Dict[str, str] = {}
    for i, net in enumerate(nets.get("declared", [])):
        where = f"nets.declared[{i}]"
        net_id = _require(net, "net", where, problems)
        _require(net, "source", where, problems)
        if net_id in seen_net:
            problems.append(f"{where}: duplicate declared net {net_id!r}")
        seen_net.add(net_id)
        for ref in net.get("terminals", []):
            _check_ref(ref, where)
            if ref in seen_ref:
                problems.append(
                    f"{where}: terminal {ref!r} already belongs to "
                    f"{seen_ref[ref]!r}; one net per terminal."
                )
            seen_ref[ref] = net_id or "?"
    for i, net in enumerate(nets.get("derived", [])):
        where = f"nets.derived[{i}]"
        _require(net, "net_id", where, problems)
        _require(net, "source", where, problems)
        for ref in net.get("terminals", []):
            _check_ref(ref, where)

    for i, entry in enumerate(spec.get("assumptions", [])):
        where = f"assumptions[{i}]"
        _require(entry, "statement", where, problems)
        _require(entry, "source", where, problems)

    return problems


def write_spec(spec: Mapping[str, Any], path: str) -> None:
    """Deterministic write: sorted keys, no timestamps in the payload.
    Validates first — an invalid spec never reaches disk."""
    problems = validate_spec(spec)
    if problems:
        raise SpecError(
            "refusing to write an invalid spec:\n- " + "\n- ".join(problems)
        )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(spec, indent=1, sort_keys=True, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )


def read_spec(path: str) -> Dict[str, Any]:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = validate_spec(spec)
    if problems:
        raise SpecError(
            f"{path} is not a valid {SCHEMA_VERSION} spec:\n- "
            + "\n- ".join(problems)
        )
    return spec

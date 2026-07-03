"""Device sizing policies for logic gates (pluggable).

A device netlist from :func:`map_logic_to_devices` assigns one unit driver cell
to every driver, which is electrically wrong: a series pull-down stack (NAND)
needs wider drivers than a parallel one (NOR) to hold the pull-down resistance
constant. This module decides, per (gate, driver), which transistor cell to
use, behind a small policy interface so the rule can come from:

  * an AUTO ratio (default): series drivers scaled by the stack depth
    (user-confirmed: 2x for a 2-high NAND stack), parallel/single
    drivers at 1x. The series-ness is DERIVED from the gate library (a gate
    with internal stacking nets is a series stack), not hardcoded per name.
  * an EXPLICIT user map: exact cell per (gate_type, role).
  * a SIMULATION backend (reserved interface; not built yet): size from device
    test data such as transfer curves. Raises until a backend is wired.

The chosen sizing is NEVER applied silently -- the agent flow proposes the auto
ratio and must get user confirmation before building (user ruling).
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence


class SizingError(ValueError):
    """Bad sizing request; the message says what to fix."""


def series_depth(rule: Mapping[str, Any]) -> int:
    """How many drivers are in series in this gate's pull-down network.

    Derived from the library: a gate whose drivers chain through internal
    stacking nets (e.g. NAND's MID) is a series stack of `#drivers`; a parallel
    gate (NOR) or single (INV) has depth 1."""
    drivers = [r for r in rule.get("devices", {}) if r != "load"]
    return len(drivers) if rule.get("internal_nets") else 1


class AutoRatioSizing:
    """Series drivers scaled by stack depth; parallel/single at 1x.

    PROCESS-AGNOSTIC: no naming convention. The policy SELECTS an existing
    device key from the supplied ``devices`` library by scaling ONE parameter
    (``scale_param``) of the unit driver. For a depth-``mult`` series stack it
    picks the device whose params equal the unit driver's params with
    ``scale_param`` multiplied by ``mult`` (every other parameter unchanged);
    if no such device exists it raises with the missing target so the author
    adds it. ``scale_param`` is named, not assumed -- any parameter (``w_um``,
    ``w_nm``, ``fingers`` ...), any number of parameters. The load device is
    selected directly by ``load_key``."""

    def __init__(self, devices: Mapping[str, Any], *, unit_key: str,
                 load_key: str, scale_param: str, max_mult: int = 3):
        self.devices = dict(devices)
        self.unit_key = unit_key
        self.load_key = load_key
        self.scale_param = scale_param
        self.max_mult = max_mult
        for key in (unit_key, load_key):
            if key not in self.devices:
                raise SizingError(
                    f"sizing device key {key!r} is not in the device library "
                    f"(have: {sorted(self.devices)})")
        self._unit_params = dict(self.devices[unit_key].get("params") or {})
        if scale_param not in self._unit_params:
            raise SizingError(
                f"scale_param {scale_param!r} is not a parameter of the unit "
                f"device {unit_key!r} (params: {sorted(self._unit_params)})")

    def cell_for(self, gate_type: str, role: str, rule: Mapping[str, Any]) -> str:
        if role == "load":
            return self.load_key
        mult = series_depth(rule)
        if mult > self.max_mult:
            raise SizingError(
                f"gate {gate_type!r} needs a {mult}x driver, beyond max_mult="
                f"{self.max_mult}; add a wider device to the library or raise max_mult."
            )
        if mult == 1:
            return self.unit_key
        target = dict(self._unit_params)
        target[self.scale_param] = self._unit_params[self.scale_param] * mult
        for key, dev in self.devices.items():
            if dict(dev.get("params") or {}) == target:
                return key
        raise SizingError(
            f"gate {gate_type!r} needs a {mult}x driver ({self.scale_param}="
            f"{target[self.scale_param]}), but no device in the library has "
            f"params {target}; add that device (or raise max_mult).")


class ExplicitSizing:
    """Exact cell per (gate_type, role) from a user-supplied map; falls back to
    a base map for roles the user did not override."""

    def __init__(self, role_cell: Mapping[tuple, str], base: "SizingPolicy"):
        self.role_cell = dict(role_cell)
        self.base = base

    def cell_for(self, gate_type: str, role: str, rule: Mapping[str, Any]) -> str:
        return self.role_cell.get((gate_type, role)) or self.base.cell_for(gate_type, role, rule)


class SimulationSizing:
    """Reserved interface: size from device test data (transfer curves, etc.)
    via a simulation backend. Not implemented -- kept so the flow has a
    general hook (user ruling: add sim later, leave the interface now)."""

    def __init__(self, device_data: Any = None, backend: Callable | None = None):
        self.device_data = device_data
        self.backend = backend

    def cell_for(self, gate_type: str, role: str, rule: Mapping[str, Any]) -> str:
        raise SizingError(
            "SimulationSizing is a reserved interface: wire a simulation backend "
            "that maps device test data (e.g. transfer curves) to W/L before use."
        )


# structural protocol: anything with cell_for(gate_type, role, rule) -> str
SizingPolicy = Any


def apply_sizing(
    netlist: Mapping[str, Any],
    library: Mapping[str, Any],
    policy: SizingPolicy,
) -> dict[str, Any]:
    """Return a copy of `netlist` with each instance's device_cell chosen by
    `policy`, plus `required_cells` (the set of cells the layout must provide).

    Uses the gate groups (load + drivers in role order) and the library rule to
    know each instance's (gate_type, role)."""
    gates = library["gates"]
    cell_of: dict[str, str] = {}
    required: set[str] = set()
    for grp in netlist["groups"]:
        gtype = grp["gate_type"]
        rule = gates.get(gtype)
        if rule is None:
            raise SizingError(f"gate type {gtype!r} not in library; cannot size it.")
        roles = list(rule["devices"].keys())  # role order matches grp['instances']
        for role, inst_id in zip(roles, grp["instances"]):
            c = policy.cell_for(gtype, role, rule)
            cell_of[inst_id] = c
            required.add(c)
    out = {
        "groups": netlist["groups"],
        "nets": netlist["nets"],
        "instances": [{"instance_id": i["instance_id"],
                       "device_cell": cell_of[i["instance_id"]]}
                      for i in netlist["instances"]],
    }
    out["required_cells"] = sorted(required)
    return out

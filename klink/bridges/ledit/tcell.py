"""T-Cell toolkit: parameter discovery, variant generation, byte-exact
differential verification. MECHANISM ONLY — device transpilations (the
actual generator ports) are agent/example work; this module supplies the
harness that accepts or rejects them.

Byte-exactness standard: a parametric
abstraction is accepted ONLY when its rendered boxes equal ground truth
element-for-element as sorted integer coordinates, at every grid point.
"""
from __future__ import annotations

import re
from typing import Any, Dict

from ...domains.structdevice.pcell_diff import (   # noqa: F401  (re-export)
    Boxes, DiffPoint, DiffReport, GeomFn, verify_differential)

#: L-Edit's generated DO-NOT-EDIT section reads parameters through these
#: typed getters — present in every generated T-Cell, so parsing them is
#: version-stable parameter discovery, not heuristics.
PARAM_RE = re.compile(
    r'LCell_GetParameterAs(Double|Int|Coord)\s*\(\s*\w+\s*,\s*"([^"]+)"')

#: property key where L-Edit stores the generator source (empirically
#: verified on v16.3; readable AND writable through the bridge)
TCELL_CODE_PROPERTY = "System.TCell Code"


def parse_tcell_params(code: str) -> Dict[str, str]:
    """Generator source -> {param_name: "double"|"int"}. A parameter read
    both AsDouble and AsCoord is one double parameter."""
    params: Dict[str, str] = {}
    for typ, name in PARAM_RE.findall(code):
        prev = params.get(name)
        params[name] = "int" if typ == "Int" and prev is None else \
            ("double" if prev != "int" else prev)
    return params


class VariantFactory:
    """Generate T-Cell variants through the bridge, cached by param tuple.

    ``bridge`` needs ``call(cmd, params)`` (LEditBridgeClient works);
    variants are instanced into ``probe_cell`` on a slot grid so they
    never overlap."""

    def __init__(self, bridge: Any, tcell: str,
                 probe_cell: str = "klink_tcell_probe",
                 slot_pitch_um: float = 70.0) -> None:
        self.bridge = bridge
        self.tcell = tcell
        self.probe_cell = probe_cell
        self.slot_pitch_um = slot_pitch_um
        self._cache: Dict[tuple, str] = {}
        self._slot = 0
        bridge.call("create_cell", {"name": probe_cell})

    def clear(self) -> None:
        self.bridge.call("clear_cell", {"cell": self.probe_cell})
        self._cache.clear()
        self._slot = 0

    def variant(self, params: Dict[str, Any]) -> str:
        """Instance the T-Cell at ``params``; returns the auto-generated
        variant cell name (harvest it with get_cell)."""
        key = tuple(sorted(params.items()))
        if key in self._cache:
            return self._cache[key]
        inst = self.bridge.call("instance_tcell", {
            "cell": self.probe_cell, "tcell": self.tcell, "params": params,
            "x_um": (self._slot % 5) * self.slot_pitch_um,
            "y_um": (self._slot // 5) * self.slot_pitch_um})
        self._slot += 1
        self._cache[key] = inst["cell"]
        return inst["cell"]


# --------------------------------------------------------------------------
# byte-exact differential verification: canonical home is
# klink.domains.structdevice.pcell_diff (harness is PCell mechanism, not
# L-Edit specific); re-exported above for backward compatibility.
# --------------------------------------------------------------------------

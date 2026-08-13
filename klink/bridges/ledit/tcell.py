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
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Sequence, Tuple

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
# byte-exact differential verification
# --------------------------------------------------------------------------

Boxes = Dict[str, List[List[int]]]        # layer -> sorted int boxes
GeomFn = Callable[[Dict[str, Any]], Boxes]


@dataclass
class DiffPoint:
    params: Dict[str, Any]
    ok: bool
    truth_boxes: int
    diffs: List[str] = field(default_factory=list)


@dataclass
class DiffReport:
    points: List[DiffPoint]

    @property
    def all_ok(self) -> bool:
        return all(p.ok for p in self.points)

    def summary(self) -> str:
        lines = []
        for p in self.points:
            lines.append(f"{p.params}: {p.truth_boxes} boxes -> "
                         f"{'BYTE-EXACT' if p.ok else 'DIFF'}")
            lines.extend(f"  {d}" for d in p.diffs)
        lines.append("VERDICT: " + ("ALL BYTE-EXACT" if self.all_ok
                                    else "MISMATCH - iterate"))
        return "\n".join(lines)


def verify_differential(render_fn: GeomFn, truth_fn: GeomFn,
                        grid: Sequence[Dict[str, Any]]) -> DiffReport:
    """Byte-exact acceptance harness: for every param point in ``grid``,
    ``render_fn`` (candidate) must reproduce ``truth_fn`` (authoritative
    generator) exactly — same layers, same sorted integer box lists.

    Both callables return ``{layer: sorted [[x0,y0,x1,y1] int, ...]}``;
    any deviation is reported with the first differing element."""
    points: List[DiffPoint] = []
    for params in grid:
        truth = truth_fn(dict(params))
        got = render_fn(dict(params))
        diffs: List[str] = []
        for layer in sorted(set(truth) | set(got)):
            t = truth.get(layer, [])
            o = got.get(layer, [])
            if t != o:
                first = next(((a, b) for a, b in zip(t, o) if a != b),
                             (t or [None])[:1])
                diffs.append(f"{layer}: truth {len(t)} vs candidate "
                             f"{len(o)} boxes; first diff {first}")
        points.append(DiffPoint(dict(params), not diffs,
                                sum(len(v) for v in truth.values()), diffs))
    return DiffReport(points)

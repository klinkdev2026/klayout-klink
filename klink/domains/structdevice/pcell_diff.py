"""Byte-exact differential acceptance harness for parametric PCells.

MECHANISM ONLY — this is the gate every parametric abstraction (fitted
table, transpiled generator, hand-written PCell) must pass before use:
at every parameter point of a grid, the candidate's rendered boxes must
equal ground truth element-for-element as sorted integer-dbu coordinates.
No tolerance, no "close enough": any deviation is a reject.

Both sides are plain callables ``(params) -> {layer: sorted [[x0, y0,
x1, y1] int-dbu, ...]}`` so ANY source pair works: an L-Edit T-Cell
harvest vs a KLayout render, a Python reference generator vs a fit-table
evaluation, two independent transpilations of the same source. klink
ships zero device data; the callables are agent/example work.

(Battle-tested: this harness accepted the L-Edit T-Cell round-trip
transpilations 6/6 byte-exact. It moved here from
``klink.bridges.ledit.tcell``, which still re-exports it.)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Sequence

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

"""Repeat-group PCell fitter — the ``klink_fitted_device_pcell_v3`` format.

The v2 fitter (:mod:`.pcell_fitter`) models every box edge as one linear law,
so it structurally cannot express COUNT-varying geometry (contact arrays,
finger repeats): when no exemplar crosses a count threshold the fit is
silently wrong outside the sampled bin.  v3 adds repeat groups:

    count(axis) = floor((num_base + Σ num_coef[p]·p) / den) + plus
    element (i, j) box = origin + (i·pitch_x, j·pitch_y) + unit box

with EVERY quantity (unit size, pitch, origin, count numerator) an exact
integer-dbu law of the parameters.  Each group is a 2-D grid with two
orthogonal axes (a 1-D row/column is simply count_y == 1); this is the one
locked representation — no nested groups, no recursion, so the KLayout-side
renderer stays a direct loop and byte-parity is auditable.

HONESTY CONTRACT (owner ruling): v3 promises floor_linear
counts + arithmetic pitch ONLY.  Anything the model cannot express EXACTLY
at every exemplar — alternating/parity patterns, non-grid positions,
bilinear extents — is REFUSED with an instructive error naming the box
family, never fitted approximately.  There is no tolerance anywhere:
detection uses exact integer arithmetic (:mod:`fractions`), and every
emitted law is re-verified through the same float pipeline the renderers
use, at every exemplar, before a table may be produced.

Exemplar input is harvest-native — the same shape the differential
harness uses: ``{"params": {name: value}, "boxes": {layer: sorted
[[x1, y1, x2, y2] int-dbu, ...]}}``.

klink ships ZERO device data; exemplars are example/agent input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .pcell_fitter import FitterError, eval_edge

FIT_FORMAT_V3 = "klink_fitted_device_pcell_v3"

_INT_TOL = 1e-6


# --------------------------------------------------------------------------- #
# exact linear-law fitting (rational arithmetic; no tolerance)
# --------------------------------------------------------------------------- #
def _solve_fractions(a: List[List[Fraction]], b: List[Fraction]
                     ) -> Optional[List[Fraction]]:
    """Exact Gaussian elimination; None if singular."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = next((r for r in range(col, n) if m[r][col] != 0), None)
        if piv is None:
            return None
        m[col], m[piv] = m[piv], m[col]
        pv = m[col][col]
        m[col] = [v / pv for v in m[col]]
        for r in range(n):
            if r != col and m[r][col] != 0:
                f = m[r][col]
                m[r] = [v - f * m[col][i] for i, v in enumerate(m[r])]
    return [m[i][n] for i in range(n)]


def _num(f: Fraction):
    """Fraction -> JSON number (int when integral, float otherwise)."""
    return int(f) if f.denominator == 1 else float(f)


def _fit_law(points: Sequence[Mapping[str, float]], param_names: Sequence[str],
             values: Sequence[int]) -> Optional[Dict[str, Any]]:
    """Fit ``values ~ base + Σ coef·param`` EXACTLY over ``points``.

    Returns a parametric edge dict (evaluable by ``eval_edge``) or None when
    no exact law exists.  Exactness is two-staged: the rational solve must
    reproduce every value exactly, and the STORED (JSON-number) model must
    reproduce every value through the float pipeline the renderers use —
    a law that only holds in exact arithmetic is refused rather than
    shipped subtly wrong."""
    if not points:
        return None
    vals = [Fraction(v) for v in values]
    if all(v == vals[0] for v in vals):
        return {"kind": "parametric", "base": _num(vals[0]),
                "coef": {p: 0 for p in param_names}}
    cols = {p: [Fraction(pt[p]).limit_denominator(10**9) for pt in points]
            for p in param_names}
    varying = [p for p in param_names
               if any(c != cols[p][0] for c in cols[p])]
    if not varying:
        return None                       # values move, parameters do not
    rows = [[Fraction(1)] + [cols[p][k] for p in varying]
            for k in range(len(points))]
    n = len(varying) + 1
    # exact least squares via normal equations (== exact solve when the
    # overdetermined system is consistent, which is what we verify)
    ata = [[sum(rows[k][i] * rows[k][j] for k in range(len(rows)))
            for j in range(n)] for i in range(n)]
    atb = [sum(rows[k][i] * vals[k] for k in range(len(rows)))
           for i in range(n)]
    sol = _solve_fractions(ata, atb)
    if sol is None:
        return None
    preds = [sum(c * x for c, x in zip(sol, row)) for row in rows]
    if any(p != v for p, v in zip(preds, vals)):
        return None
    coef = {p: 0 for p in param_names}
    coef.update({p: _num(sol[i + 1]) for i, p in enumerate(varying)})
    law = {"kind": "parametric", "base": _num(sol[0]), "coef": coef}
    for pt, v in zip(points, values):     # float-pipeline re-verification
        try:
            if eval_edge(law, pt, list(param_names), []) != v:
                return None
        except ValueError:
            return None
    return law


def _fit_count_law(points: Sequence[Mapping[str, float]],
                   param_names: Sequence[str], counts: Sequence[int],
                   den_candidates: Sequence[int]) -> Optional[Dict[str, Any]]:
    """Fit ``count = floor((num_base + Σ num_coef·p)/den) + plus`` with
    INTEGER num coefficients, verified exactly at every exemplar.

    den=1 (a plainly linear integer count) is tried first; then each den
    candidate (the family pitch — a count is almost always extent/pitch).

    UNIQUENESS RULE (the honesty core of floor laws): for a den>1
    candidate the numerator base is only known to lie in an interval; a
    law is emitted ONLY when the exemplars pin that interval to a single
    integer (sample both sides of a count step to pin it).  An
    under-determined law would verify at every exemplar and still be
    silently wrong off-sample — the exact failure mode v3 exists to kill.
    This is also what refuses parity/alternating counts (ceil(M/2)-style):
    their count steps once per TWO parameter units, so the base interval
    stays ~den/2 wide no matter how many exemplars are added — such a
    family is permanently under-determined and lands in REFUSE by
    arithmetic, not by special-casing."""
    lin = _fit_law(points, param_names, list(counts))
    if lin is not None and isinstance(lin["base"], int) and \
            all(isinstance(c, int) for c in lin["coef"].values()):
        return {"kind": "floor_linear", "num_base": lin["base"],
                "num_coef": dict(lin["coef"]), "den": 1, "plus": 0}
    cols = {p: [float(pt[p]) for pt in points] for p in param_names}
    varying = [p for p in param_names
               if any(c != cols[p][0] for c in cols[p])]
    for den in den_candidates:
        if den < 2:
            continue
        for plus in (0, 1):
            # estimate the numerator on bin midpoints, snap to integers,
            # then pick num_base from the exact feasibility interval
            mids = [den * (c - plus) + Fraction(den - 1, 2) for c in counts]
            coef_int: Dict[str, int] = {}
            if varying:
                frows = [[Fraction(1)] + [Fraction(cols[p][k]).
                                          limit_denominator(10**9)
                                          for p in varying]
                         for k in range(len(points))]
                n = len(varying) + 1
                ata = [[sum(frows[k][i] * frows[k][j]
                            for k in range(len(frows)))
                        for j in range(n)] for i in range(n)]
                atb = [sum(frows[k][i] * mids[k] for k in range(len(frows)))
                       for i in range(n)]
                sol = _solve_fractions(ata, atb)
                if sol is None:
                    continue
                coef_int = {p: int(round(float(sol[i + 1])))
                            for i, p in enumerate(varying)}
            lo, hi = None, None
            ok = True
            for pt, c in zip(points, counts):
                s = sum(coef_int.get(p, 0) * pt[p] for p in varying)
                if abs(s - round(s)) > _INT_TOL:
                    ok = False
                    break
                s = int(round(s))
                l = den * (c - plus) - s
                h = den * (c - plus + 1) - 1 - s
                lo = l if lo is None else max(lo, l)
                hi = h if hi is None else min(hi, h)
            if not ok or lo is None or lo != hi:
                continue          # infeasible OR under-determined: no law
            law = {"kind": "floor_linear", "num_base": int(lo),
                   "num_coef": {p: coef_int.get(p, 0) for p in param_names},
                   "den": int(den), "plus": int(plus)}
            if all(eval_count(law, pt, list(param_names)) == c
                   for pt, c in zip(points, counts)):
                return law
    return None


# --------------------------------------------------------------------------- #
# evaluation (Python side; the KLayout plugin mirrors this byte-identically)
# --------------------------------------------------------------------------- #
def eval_count(law: Mapping[str, Any], params: Mapping[str, float],
               param_order: Sequence[str]) -> int:
    """Evaluate a floor_linear count law to a non-negative int."""
    if law.get("kind") != "floor_linear":
        raise ValueError("unknown count-law kind %r" % law.get("kind"))
    num = float(law.get("num_base", 0))
    coef = law.get("num_coef", {})
    for name in param_order:
        num += float(coef.get(name, 0)) * float(params[name])
    if abs(num - round(num)) > _INT_TOL:
        shown = " ".join("%s=%g" % (n, params[n]) for n in param_order)
        raise ValueError(
            "count law gives non-integer numerator %r for %s" % (num, shown))
    den = int(law.get("den", 1))
    count = int(round(num)) // den + int(law.get("plus", 0))
    if count < 0:
        raise ValueError("count law gives negative count %d" % count)
    return count


def eval_repeat_group(group: Mapping[str, Any], params: Mapping[str, float],
                      param_order: Sequence[str],
                      sample_order: Sequence[Mapping[str, Any]]
                      ) -> List[Tuple[str, Tuple[int, int, int, int]]]:
    """Expand one repeat group to concrete (layer, box) elements.

    Geometry: for axis a in {x, y}: count_a = eval_count, pitch_a and
    origin_a resolved via the v2 edge pipeline; a "centered" origin stores
    ``expr = first + last`` element origin (their sum), so the first
    element origin is ``(expr - (count-1)*pitch) / 2`` — refused if that
    is not an integer (no silent half-dbu shifts)."""
    po = list(param_order)
    counts, pitches, origins = {}, {}, {}
    for axis in ("x", "y"):
        counts[axis] = eval_count(group["count"][axis], params, po)
        pitches[axis] = eval_edge(group["pitch_dbu"][axis], params, po,
                                  sample_order)
        org = group["origin"][axis]
        expr = eval_edge(org["expr"], params, po, sample_order)
        if org.get("kind") == "centered":
            span = (counts[axis] - 1) * pitches[axis]
            if (expr - span) % 2 != 0:
                raise ValueError(
                    "centered origin (%s axis) gives non-integer first "
                    "position: (%d - %d)/2" % (axis, expr, span))
            origins[axis] = (expr - span) // 2
        elif org.get("kind") == "fixed":
            origins[axis] = expr
        else:
            raise ValueError("unknown origin kind %r" % org.get("kind"))
    out: List[Tuple[str, Tuple[int, int, int, int]]] = []
    for role_name in sorted(group.get("unit_roles", {})):
        role = group["unit_roles"][role_name]
        e = role["edges"]
        x1 = eval_edge(e["x1"], params, po, sample_order)
        y1 = eval_edge(e["y1"], params, po, sample_order)
        x2 = eval_edge(e["x2"], params, po, sample_order)
        y2 = eval_edge(e["y2"], params, po, sample_order)
        for j in range(counts["y"]):
            for i in range(counts["x"]):
                ox = origins["x"] + i * pitches["x"]
                oy = origins["y"] + j * pitches["y"]
                out.append((str(role["layer"]),
                            (ox + x1, oy + y1, ox + x2, oy + y2)))
    return out


def render_table(table: Mapping[str, Any], params: Mapping[str, float],
                 style: str = "default") -> Dict[str, List[List[int]]]:
    """Render a v2 or v3 fit table to ``{layer: sorted [[x1,y1,x2,y2]]}`` —
    exactly the shape :func:`.pcell_diff.verify_differential` consumes, so a
    fitted table can be differentially gated against ground truth offline."""
    styles = table.get("styles", {})
    if style not in styles:
        raise ValueError("style %r not in table (has: %s)"
                         % (style, ", ".join(sorted(styles))))
    po = list(table.get("param_order", []))
    so = table.get("sample_order", [])
    st = styles[style]
    out: Dict[str, List[List[int]]] = {}
    for role_name in sorted(st.get("roles", {})):
        role = st["roles"][role_name]
        e = role["edges"]
        box = [eval_edge(e[k], params, po, so)
               for k in ("x1", "y1", "x2", "y2")]
        out.setdefault(str(role["layer"]), []).append(box)
    for group_name in sorted(st.get("repeat_groups", {})):
        for layer, box in eval_repeat_group(st["repeat_groups"][group_name],
                                            params, po, so):
            out.setdefault(layer, []).append(list(box))
    for layer in out:
        out[layer].sort()
    return out


# --------------------------------------------------------------------------- #
# detection: harvested boxes -> families -> exact laws
# --------------------------------------------------------------------------- #
@dataclass
class RepeatReport:
    """Result of :func:`analyze_boxes`.  ``refusals`` non-empty means the
    geometry contains structure v3 cannot express — :func:`fit_table_v3`
    will refuse to emit a table (never fit it wrong)."""
    param_names: List[str]
    roles: Dict[str, Any]
    repeat_groups: Dict[str, Any]
    refusals: List[str] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)
    sampled: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [f"parameters: {self.param_names}",
                 f"static roles: {len(self.roles)}, repeat groups: "
                 f"{len(self.repeat_groups)}, refused families: "
                 f"{len(self.refusals)}",
                 "validity: interpolation within sampled envelope; "
                 "extrapolation UNVERIFIED"]
        if self.roles:
            lines.append("  learned static: " + ", ".join(sorted(self.roles)))
        if self.repeat_groups:
            lines.append("  learned groups: "
                         + ", ".join(sorted(self.repeat_groups)))
        for msg in self.refusals:
            lines.append("  REFUSED: " + msg)
        for msg in self.decisions:
            lines.append("  DECIDE: " + msg)
        return "\n".join(lines)


def _families_of(boxes: Sequence[Sequence[int]]
                 ) -> List[Dict[str, Any]]:
    """Cluster one exemplar's boxes on one layer by exact (w, h) shape."""
    fams: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    for b in boxes:
        x1, y1, x2, y2 = (int(v) for v in b)
        fams.setdefault((x2 - x1, y2 - y1), []).append((x1, y1))
    out = []
    for (w, h) in sorted(fams):
        out.append({"w": w, "h": h, "positions": sorted(fams[(w, h)])})
    return out


def _grid_of(positions: Sequence[Tuple[int, int]]) -> Optional[Dict[str, int]]:
    """Positions -> exact arithmetic grid, or None.

    The full cartesian product must be present and each axis strictly
    arithmetic (exact integer equality — harvested dbu is integer, any
    'almost' grid is a different structure, not noise)."""
    xs = sorted({p[0] for p in positions})
    ys = sorted({p[1] for p in positions})
    if len(xs) * len(ys) != len(positions):
        return None
    if set(positions) != {(x, y) for x in xs for y in ys}:
        return None
    def _pitch(vals: List[int]) -> Optional[int]:
        if len(vals) < 2:
            return 0
        d = vals[1] - vals[0]
        if any(b - a != d for a, b in zip(vals, vals[1:])):
            return None
        return d
    px, py = _pitch(xs), _pitch(ys)
    if px is None or py is None:
        return None
    return {"first_x": xs[0], "first_y": ys[0], "px": px, "py": py,
            "cx": len(xs), "cy": len(ys)}


def _fit_axis(points: Sequence[Mapping[str, float]], param_names: List[str],
              grids: List[Dict[str, int]], axis: str
              ) -> Optional[Dict[str, Any]]:
    """Fit one axis of an aligned family: count law + pitch law + origin law.

    Pitch is only observable where count >= 2 on that axis; its law is
    fitted on those exemplars (constant 0 when never observed)."""
    ck, pk, fk = ("cx", "px", "first_x") if axis == "x" else \
                 ("cy", "py", "first_y")
    counts = [g[ck] for g in grids]
    obs = [(pt, g[pk]) for pt, g in zip(points, grids) if g[ck] >= 2]
    if obs:
        pitch = _fit_law([o[0] for o in obs], param_names,
                         [o[1] for o in obs])
    else:
        pitch = {"kind": "parametric", "base": 0,
                 "coef": {p: 0 for p in param_names}}
    if pitch is None:
        return None
    den_candidates = []
    if isinstance(pitch["base"], int) and pitch["base"] > 1 and \
            all(c == 0 for c in pitch["coef"].values()):
        den_candidates.append(pitch["base"])
    count = _fit_count_law(points, param_names, counts, den_candidates)
    if count is None:
        return None
    # origin: fixed (first position is an exact law) else centered
    # (first+last is an exact law) else unexplainable
    first = [g[fk] for g in grids]
    origin = None
    law = _fit_law(points, param_names, first)
    if law is not None:
        origin = {"kind": "fixed", "expr": law}
    else:
        sums = []
        for pt, g in zip(points, grids):
            pv = eval_edge(pitch, pt, param_names, [])
            sums.append(2 * g[fk] + (g[ck] - 1) * pv)
        law = _fit_law(points, param_names, sums)
        if law is not None:
            origin = {"kind": "centered", "expr": law}
    if origin is None:
        return None
    return {"count": count, "pitch": pitch, "origin": origin}


def _static_split(points: Sequence[Mapping[str, float]],
                  param_names: List[str], fams: List[Dict[str, Any]]
                  ) -> Optional[List[Dict[str, Any]]]:
    """Constant-count family -> one exact-law edge set PER box (matched by
    sorted position order across exemplars). This is the 'count-invariant
    families are plain v2 linear edges' rule: a source/drain pair at
    diagonal offsets is not a grid, but each box individually follows
    linear laws. Any box that does not fit exactly kills the whole split
    (None) — correspondence errors end in refusal, never a wrong fit."""
    k = len(fams[0]["positions"])
    out: List[Dict[str, Any]] = []
    for idx in range(k):
        xs = [f["positions"][idx][0] for f in fams]
        ys = [f["positions"][idx][1] for f in fams]
        edges = {
            "x1": _fit_law(points, param_names, xs),
            "y1": _fit_law(points, param_names, ys),
            "x2": _fit_law(points, param_names,
                           [x + f["w"] for x, f in zip(xs, fams)]),
            "y2": _fit_law(points, param_names,
                           [y + f["h"] for y, f in zip(ys, fams)]),
        }
        if any(v is None for v in edges.values()):
            return None
        out.append(edges)
    return out


def analyze_boxes(
    exemplars: Sequence[Mapping[str, Any]],
    param_names: Sequence[str],
) -> RepeatReport:
    """Screen harvested exemplar geometry for repeat structure.

    ``exemplars``: ``[{"params": {name: value}, "boxes": {layer:
    [[x1, y1, x2, y2] int-dbu, ...]}}, ...]`` — at least 2, integer dbu.
    Per layer, boxes are clustered by exact shape into families, families
    are aligned across exemplars by shape rank, and each family either
    resolves to exact laws (a static role or a repeat group) or lands in
    ``refusals``.  A wrong family alignment can only ever end in a
    refusal — the exact-law verification fails — never in a wrong fit."""
    param_names = list(param_names)
    if len(exemplars) < 2:
        raise FitterError("need >= 2 exemplars at different parameter values")
    points = [{p: float(ex["params"][p]) for p in param_names}
              for ex in exemplars]
    layers: set = set()
    for ex in exemplars:
        layers |= set(ex.get("boxes", {}))
    if not layers:
        raise FitterError("exemplars carry no boxes; expected "
                          "{'params': .., 'boxes': {layer: [[x1,y1,x2,y2]..]}}")
    decisions: List[str] = []
    for p in param_names:
        col = [pt[p] for pt in points]
        if max(col) - min(col) < 1e-12:
            decisions.append(
                f"param {p} has a single sampled value ({col[0]:g}); its "
                f"effect is NOT captured. Add exemplars at other {p} values "
                f"and re-run.")
    roles: Dict[str, Any] = {}
    groups: Dict[str, Any] = {}
    refusals: List[str] = []
    refuse_hint = (
        "v3 refuses to fit what it cannot express EXACTLY and UNIQUELY "
        "(floor_linear counts + arithmetic pitch, law pinned to a single "
        "candidate). Options: (a) PIN the parameter that drives this "
        "structure to a single value and re-fit — the table is then valid "
        "at that envelope (the single-value warning documents it); (b) if "
        "the cause is an unpinned count law, add exemplars on BOTH sides "
        "of a count step (bin edges pin it uniquely); (c) transpile the "
        "generator source and gate it with "
        "pcell_diff.verify_differential (code expresses anything).")
    for layer in sorted(layers):
        fam_sets = [_families_of(ex.get("boxes", {}).get(layer, []))
                    for ex in exemplars]
        n_fams = {len(fs) for fs in fam_sets}
        if len(n_fams) != 1:
            refusals.append(
                f"layer {layer}: family count differs across exemplars "
                f"({sorted(len(fs) for fs in fam_sets)}); box families "
                f"cannot be aligned. " + refuse_hint)
            continue
        for fi in range(n_fams.pop()):
            fams = [fs[fi] for fs in fam_sets]
            tag = (f"layer {layer} family #{fi} "
                   f"(shape {fams[0]['w']}x{fams[0]['h']} dbu, counts "
                   f"{[len(f['positions']) for f in fams]} across exemplars)")
            w_law = _fit_law(points, param_names, [f["w"] for f in fams])
            h_law = _fit_law(points, param_names, [f["h"] for f in fams])
            if w_law is None or h_law is None:
                refusals.append(
                    f"{tag}: unit box size is not an exact linear law of "
                    f"{param_names}. " + refuse_hint)
                continue
            name = "L%s_f%d" % (layer.replace("/", "_"), fi)
            const_count = len({len(f["positions"]) for f in fams}) == 1
            grids = [_grid_of(f["positions"]) for f in fams]
            ax = ay = None
            if not any(g is None for g in grids):
                ax = _fit_axis(points, param_names, grids, "x")
                ay = _fit_axis(points, param_names, grids, "y")
            if ax is None or ay is None:
                # grid/axis laws failed. A count-INVARIANT family still has
                # a fully honest fallback: per-box v2 linear edges (the
                # 'count-invariant families are plain linear edges' rule) —
                # e.g. a source/drain pair at diagonal offsets is no grid,
                # but each box follows exact laws individually.
                if const_count:
                    split = _static_split(points, param_names, fams)
                    if split is not None:
                        for idx, edges in enumerate(split):
                            roles[f"{name}_b{idx}"] = {"layer": layer,
                                                       "edges": edges}
                        continue
                if any(g is None for g in grids):
                    refusals.append(
                        f"{tag}: positions do not form an exact arithmetic "
                        f"grid, and no exact per-box linear laws exist — "
                        f"typical when same-shape elements interleave with "
                        f"different offsets, e.g. odd/even fingers routed "
                        f"differently (outside the v3 model BY DESIGN). "
                        + refuse_hint)
                else:
                    bad = "x" if ax is None else "y"
                    refusals.append(
                        f"{tag}: the {bad}-axis count/pitch/origin is not "
                        f"an exact floor_linear/linear law of "
                        f"{param_names} (alternating or parity-dependent "
                        f"counts land here by design). " + refuse_hint)
                continue
            if all(g["cx"] == 1 and g["cy"] == 1 for g in grids):
                # a single static box: emit a plain v2-style role
                ex1 = ax["origin"]["expr"]
                ey1 = ay["origin"]["expr"]
                x2 = _fit_law(points, param_names,
                              [g["first_x"] + f["w"]
                               for g, f in zip(grids, fams)])
                y2 = _fit_law(points, param_names,
                              [g["first_y"] + f["h"]
                               for g, f in zip(grids, fams)])
                if x2 is None or y2 is None or \
                        ax["origin"]["kind"] != "fixed" or \
                        ay["origin"]["kind"] != "fixed":
                    refusals.append(
                        f"{tag}: static box edges are not exact linear "
                        f"laws. " + refuse_hint)
                    continue
                roles[name] = {"layer": layer,
                               "edges": {"x1": ex1, "y1": ey1,
                                         "x2": x2, "y2": y2}}
                continue
            groups[name] = {
                "unit_roles": {"u0": {"layer": layer, "edges": {
                    "x1": {"kind": "parametric", "base": 0,
                           "coef": {p: 0 for p in param_names}},
                    "y1": {"kind": "parametric", "base": 0,
                           "coef": {p: 0 for p in param_names}},
                    "x2": w_law, "y2": h_law}}},
                "count": {"x": ax["count"], "y": ay["count"]},
                "pitch_dbu": {"x": ax["pitch"], "y": ay["pitch"]},
                "origin": {"x": ax["origin"], "y": ay["origin"]},
            }
    sampled = {
        "params": {p: {"min": min(pt[p] for pt in points),
                       "max": max(pt[p] for pt in points)}
                   for p in param_names},
        "points": [dict(pt) for pt in points],
    }
    return RepeatReport(param_names, roles, groups, refusals, decisions,
                        sampled)


def fit_table_v3(
    report: RepeatReport,
    *,
    style: str = "default",
    sample_order: Sequence[Mapping[str, float]] = (),
    param_units: Mapping[str, str] | None = None,
) -> Dict[str, Any]:
    """Emit a ``klink_fitted_device_pcell_v3`` table — REFUSED (FitterError)
    while the report carries any unexplained family.  A refused fit is not a
    failure of the workflow; it is the workflow: fix the exemplars or take
    the transpile route, never ship a wrong abstraction."""
    if report.refusals:
        raise FitterError(
            "REFUSED to emit a v3 table; %d box famil%s cannot be "
            "expressed exactly:\n- %s" % (
                len(report.refusals),
                "y" if len(report.refusals) == 1 else "ies",
                "\n- ".join(report.refusals)))
    if not report.roles and not report.repeat_groups:
        raise FitterError("nothing to emit: no static roles and no repeat "
                          "groups were detected")
    table: Dict[str, Any] = {
        "format": FIT_FORMAT_V3,
        "param_order": list(report.param_names),
        "sample_order": [dict(s) for s in sample_order],
        "styles": {style: {"roles": dict(report.roles),
                           "repeat_groups": dict(report.repeat_groups)}},
    }
    if report.sampled:
        table["sampled"] = report.sampled
    if param_units:
        table["param_units"] = dict(param_units)
    return table

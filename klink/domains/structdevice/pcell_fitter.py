"""Parametric PCell fitter -- MECHANISM for turning exemplar device geometry
into a fully-parametric edge model, with the workflow baked in so a non-expert
gets it right:

    harvest exemplars (example)  ->  analyze()  ->  read decisions_needed
       ->  confirm the model  ->  fit_table()  ->  register PCell  ->  verify

``analyze`` does the SCREENING: for every box edge of every role it regresses
the edge value against the layout parameters (least squares, pure Python),
reports the correlation + fit quality (R^2), and CLASSIFIES the edge as
``linear`` (cleanly driven by the parameters), ``constant`` (does not move), or
``unexplained`` (R^2 below threshold -- the data does not justify a parametric
law; the caller must decide: a NEW parameter, or pin it to a constant). The
unexplained edges come back in ``decisions_needed`` as instructions, so the
intelligence is in the PROCESS, not in the operator's head.

``fit_table`` emits the canonical ``klink_fitted_device_pcell_v2`` table
(``base + sum(coef[p]*param[p])`` per edge; a constant edge is just coef 0), so
the fitted device draws at ANY parameter values, not only the exemplar points.

klink ships ZERO device data: the exemplars (which cells, which sizes, which
layers) are example/process input. This module only does the math.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence

_EDGES = ("x1", "y1", "x2", "y2")


class FitterError(ValueError):
    """Bad exemplar input; the message says what to fix."""


# --------------------------------------------------------------------------- #
# pure-Python linear algebra (klink core takes no third-party deps)
# --------------------------------------------------------------------------- #
def _solve(a: List[List[float]], b: List[float]) -> List[float]:
    """Solve the small dense system a x = b by Gaussian elimination with partial
    pivoting. Used on the (P+1)x(P+1) normal-equation matrix (P = #params)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            raise FitterError("singular normal-equation matrix; exemplar "
                              "parameters are collinear (vary at least one "
                              "parameter independently)")
        m[col], m[piv] = m[piv], m[col]
        pv = m[col][col]
        m[col] = [v / pv for v in m[col]]
        for r in range(n):
            if r != col and m[r][col]:
                f = m[r][col]
                m[r] = [v - f * m[col][i] for i, v in enumerate(m[r])]
    return [m[i][n] for i in range(n)]


def _least_squares(xs: List[List[float]], ys: List[float]) -> List[float]:
    """Least-squares fit ys ~ xs (xs rows already include the bias column).
    Returns the coefficient vector via the normal equations xs^T xs c = xs^T y."""
    p = len(xs[0])
    ata = [[sum(xs[k][i] * xs[k][j] for k in range(len(xs))) for j in range(p)]
           for i in range(p)]
    atb = [sum(xs[k][i] * ys[k] for k in range(len(xs))) for i in range(p)]
    return _solve(ata, atb)


def _r2(ys: Sequence[float], preds: Sequence[float]) -> float:
    mean = sum(ys) / len(ys)
    ss_tot = sum((y - mean) ** 2 for y in ys)
    if ss_tot < 1e-12:
        return 1.0           # constant target -> a constant model fits perfectly
    ss_res = sum((y - p) ** 2 for y, p in zip(ys, preds))
    return 1.0 - ss_res / ss_tot


def _corr(xs: Sequence[float], ys: Sequence[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sx = (sum((x - mx) ** 2 for x in xs)) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys)) ** 0.5
    if sx < 1e-12 or sy < 1e-12:
        return float("nan")
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


# --------------------------------------------------------------------------- #
# analysis report
# --------------------------------------------------------------------------- #
@dataclass
class EdgeFit:
    role: str
    edge: str
    layer: str
    base_dbu: float
    coef_dbu: Dict[str, float]          # {param_name: dbu per param-unit}
    r2: float
    correlations: Dict[str, float]      # {param_name: pearson r}
    classification: str                 # "linear" | "constant" | "unexplained"


@dataclass
class FitReport:
    param_names: List[str]
    edges: List[EdgeFit]
    decisions_needed: List[str] = field(default_factory=list)

    def summary(self) -> str:
        n_lin = sum(e.classification == "linear" for e in self.edges)
        n_con = sum(e.classification == "constant" for e in self.edges)
        n_un = sum(e.classification == "unexplained" for e in self.edges)
        lines = [f"parameters: {self.param_names}",
                 f"edges: {n_lin} linear, {n_con} constant, {n_un} unexplained"]
        for msg in self.decisions_needed:
            lines.append("  DECIDE: " + msg)
        return "\n".join(lines)


def _exemplar_roles(exemplars: Sequence[Mapping[str, Any]]) -> List[str]:
    if not exemplars:
        raise FitterError("no exemplars given; fitting needs at least 2 "
                          "exemplar cells at different parameter values")
    roles = set(exemplars[0].get("roles", {}))
    for ex in exemplars[1:]:
        roles &= set(ex.get("roles", {}))
    if not roles:
        raise FitterError("exemplars share no common role; every exemplar must "
                          "expose the same set of named roles (e.g. channel, "
                          "source, drain, gate)")
    return sorted(roles)


def analyze(
    exemplars: Sequence[Mapping[str, Any]],
    param_names: Sequence[str],
    *,
    r2_threshold: float = 0.99,
    dbu: float = 0.001,
) -> FitReport:
    """Screen exemplar geometry: regress every role/edge on the parameters,
    classify each edge, and flag the ones the data does not explain.

    ``exemplars``: ``[{"params": {name: value}, "roles": {role: {"layer":
    "L/D", "box_um": [x1, y1, x2, y2]}}}, ...]`` -- at least 2, varying the
    parameters. ``param_names``: the layout parameters to fit against (e.g.
    ``["w_um", "l_um"]``). Returns a :class:`FitReport`; read ``decisions_needed``
    and confirm with the user before calling :func:`fit_table`."""

    param_names = list(param_names)
    if len(exemplars) < 2:
        raise FitterError("need >= 2 exemplars at different parameter values")
    roles = _exemplar_roles(exemplars)
    # design matrix rows: [1, p1, p2, ...] per exemplar
    xs = [[1.0] + [float(ex["params"][p]) for p in param_names] for ex in exemplars]
    param_cols = {p: [float(ex["params"][p]) for ex in exemplars]
                  for p in param_names}

    edges: List[EdgeFit] = []
    decisions: List[str] = []
    for role in roles:
        layer = str(exemplars[0]["roles"][role]["layer"])
        for ei, ename in enumerate(_EDGES):
            ys = [float(ex["roles"][role]["box_um"][ei]) / dbu for ex in exemplars]
            coef = _least_squares(xs, ys)            # [base, c1, c2, ...] in dbu
            preds = [sum(c * x for c, x in zip(coef, row)) for row in xs]
            r2 = _r2(ys, preds)
            corrs = {p: _corr(param_cols[p], ys) for p in param_names}
            spread = max(ys) - min(ys)
            base = coef[0]
            coef_map = {p: coef[i + 1] for i, p in enumerate(param_names)}
            if spread < 1e-6:
                cls = "constant"
                coef_map = {p: 0.0 for p in param_names}
                base = sum(ys) / len(ys)
            elif r2 >= r2_threshold:
                cls = "linear"
            else:
                cls = "unexplained"
                # safe default = pin to a constant (mean); surface the decision
                coef_map = {p: 0.0 for p in param_names}
                base = sum(ys) / len(ys)
                decisions.append(
                    f"{role}.{ename} (layer {layer}) is not explained by "
                    f"{param_names} (R^2={r2:.2f}); pinned to constant "
                    f"{base/1000.0*1000.0:.0f}dbu. If it is really driven by a "
                    f"NEW parameter, add that parameter to the exemplars and "
                    f"re-run; otherwise the constant is correct.")
            edges.append(EdgeFit(role, ename, layer, base, coef_map, r2,
                                 corrs, cls))
    return FitReport(param_names, edges, decisions)


# --------------------------------------------------------------------------- #
# table generation
# --------------------------------------------------------------------------- #
def fit_table(
    report: FitReport,
    *,
    style: str = "default",
    sample_order: Sequence[Mapping[str, float]] = (),
    param_units: Mapping[str, str] | None = None,
    keep_roles: Sequence[str] | None = None,
) -> Dict[str, Any]:
    """Emit a canonical ``klink_fitted_device_pcell_v2`` fit table from an
    analyzed report. Every edge becomes a parametric edge
    ``base + sum(coef[p]*param[p])`` (a constant edge has all-zero coef), so the
    device draws at ANY parameter values. ``keep_roles`` (optional) restricts
    which roles are emitted -- e.g. drop a contact-lead role you do not want."""

    roles: Dict[str, Any] = {}
    for ef in report.edges:
        if keep_roles is not None and ef.role not in keep_roles:
            continue
        r = roles.setdefault(ef.role, {"layer": ef.layer, "edges": {}})
        # Round base + coefficients to whole dbu (dbu per param-unit). The plugin
        # refuses non-integer dbu (no silent rounding at draw time); a least-
        # squares fit of hand-drawn exemplars has fractional coefficients, so we
        # round HERE -> integer dbu for integer parameter values. Clean edges
        # (exact slopes) are unchanged; the rounding error on a noisy edge is
        # sub-dbu and irrelevant for a synthetic device.
        r["edges"][ef.edge] = {
            "kind": "parametric",
            "base": round(ef.base_dbu),
            "coef": {p: round(ef.coef_dbu.get(p, 0.0)) for p in report.param_names},
        }
    table: Dict[str, Any] = {
        "format": "klink_fitted_device_pcell_v2",
        "param_order": list(report.param_names),
        "sample_order": [dict(s) for s in sample_order],
        "styles": {style: {"roles": roles}},
    }
    if param_units:
        table["param_units"] = dict(param_units)
    return table

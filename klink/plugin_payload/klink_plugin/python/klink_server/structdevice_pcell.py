"""
klink_structdevice — GENERIC fitted-device PCell machinery.

By design, the plugin ships NO device-specific PCell classes and NO
device-specific data.  It ships one table-driven declaration class plus a
runtime registration entry point; device definitions (fit tables, produced
by the klink-side fitter from the user's exemplar families) are registered
from OUTSIDE via the `pcell.register_fitted` RPC.  A new device family is one
RPC call — zero plugin edits, zero hot reloads.

N-ARY PARAMETERS (no W/L assumption).  The edge model is
``dbu = base + Σ_i coef[param_i] · params[param_i]`` over an ORDERED, arbitrary
parameter list declared by the table (``param_order``).  A device may carry any
number of parameters with any names (``w_um l_um`` for the back-gate lab device,
``w l a b c`` for something else); the plugin assumes nothing about which
parameters exist or how many.  The two-parameter (W, L) case is just the special
case ``param_order = ["w_um", "l_um"]`` — and the legacy v1 table format is read
by normalising it into exactly that N-ary shape, so old tables stay byte-exact.

Probe-verified API facts (live KLayout 0.30.7):
- lib.layout().register_pcell(name, decl) works AFTER lib.register();
- param declarations may be built dynamically in __init__ loops (this is how
  the N-ary parameter axes are declared);
- add_choice lives on the PCellParameterDeclaration returned by self.param();
- re-registering a LIBRARY name silently replaces it (hot reload uses the
  fresh-library pattern shared with port/anchor libs).

Table formats (both accepted; v1 is normalised to v2 on load):
- ``klink_fitted_device_pcell_v2`` (canonical, N-ary): carries ``param_order``,
  a ``sample_order`` of {param: value} dicts, and per-edge
  ``{"kind": "parametric", "base": .., "coef": {param: ..}}`` or
  ``{"kind": "non_parametric", "values": [..]}``. Units are author-chosen and
  carried by the parameter NAMES (``w_um`` vs ``w_nm``) + coefficient scale;
  the plugin imposes none. An optional ``param_units`` map {param: unit_str}
  sets only the GUI display suffix (a nm process uses clean integers).
- ``klink_transistor_pcell_fit_v1`` (legacy, 2-ary): ``sample_order`` of
  {"W": .., "L": ..} and parametric edges {"a", "b", "c"} for ``a + b·W + c·L``.
  Read for backward compatibility only; normalised to the v2 shape with
  ``param_order = ["w_um", "l_um"]`` (byte-identical arithmetic).
The table fully encodes user layout geometry and never ships with the plugin;
it is referenced by path at registration time.

Honesty rules in produce (unchanged from the v1 carrier):
- validate-before-mutate: all boxes computed before any insert; a failure
  renders error text ONLY, never a partially drawn device;
- parametric edges refuse non-integer dbu (no silent rounding);
- non-parametric edges are exact exemplar lookups; misses list the available
  points (drawing conventions are never extrapolated).
"""

from __future__ import annotations

import json
import os

import pya

# Canonical (N-ary) format and the legacy (2-ary) format we still read.
# v3 = v2 static roles PLUS per-style "repeat_groups" (count-varying
# geometry: floor_linear counts, arithmetic pitch, fixed/centered origin).
# v2 tables render exactly as before — v3 is a new format version, not a
# change to v2 semantics.
_FIT_FORMAT_V3 = "klink_fitted_device_pcell_v3"
_FIT_FORMAT_V2 = "klink_fitted_device_pcell_v2"
_FIT_FORMAT_V1 = "klink_transistor_pcell_fit_v1"
# Legacy v1 carried no param_order; its two axes are W and L, declared as the
# PCell parameters w_um / l_um. Kept in ONE place so the assumption is explicit.
_V1_PARAM_ORDER = ["w_um", "l_um"]
_V1_SAMPLE_KEYS = {"w_um": "W", "l_um": "L"}

_LIB_NAME = "klink_structdevice"
_INT_TOL = 1e-6

_TABLE_CACHE: dict = {}      # (path, mtime) -> NORMALISED table
_REGISTERED: dict = {}       # pcell name -> table path (this session)


def _first_role_layer(styles: dict) -> str:
    """The lexically-first role's layer across all styles — used as the
    fallback layer to render error text on when the table declares none.
    Process-agnostic: it is wherever this device's own geometry lives."""
    for _style_name in sorted(styles):
        style = styles[_style_name]
        roles = style.get("roles", {})
        for role_name in sorted(roles):
            layer = roles[role_name].get("layer")
            if layer:
                return str(layer)
        for group_name in sorted(style.get("repeat_groups", {})):
            units = style["repeat_groups"][group_name].get("unit_roles", {})
            for unit_name in sorted(units):
                layer = units[unit_name].get("layer")
                if layer:
                    return str(layer)
    return "0/0"


def _normalise_v1(table: dict) -> dict:
    """Rewrite a legacy 2-ary (W, L) table into the canonical N-ary shape.

    The arithmetic is preserved EXACTLY: parametric ``a + b·W + c·L`` becomes
    ``base=a`` with ``coef={"w_um": b, "l_um": c}`` summed in param_order
    [w_um, l_um], which evaluates to the identical float sequence."""
    out = {
        "format": _FIT_FORMAT_V2,
        "param_order": list(_V1_PARAM_ORDER),
        "sample_order": [
            {p: s[_V1_SAMPLE_KEYS[p]] for p in _V1_PARAM_ORDER}
            for s in table.get("sample_order", [])
        ],
        "styles": {},
    }
    for style_name, style in table.get("styles", {}).items():
        roles_out = {}
        for role_name, role in style.get("roles", {}).items():
            edges_out = {}
            for edge_name, edge in role.get("edges", {}).items():
                if edge.get("kind") == "parametric":
                    edges_out[edge_name] = {
                        "kind": "parametric",
                        "base": edge.get("a", 0),
                        "coef": {"w_um": edge.get("b", 0),
                                 "l_um": edge.get("c", 0)},
                    }
                else:
                    edges_out[edge_name] = dict(edge)
            roles_out[role_name] = {"layer": role.get("layer"),
                                    "edges": edges_out}
        out["styles"][style_name] = {**{k: v for k, v in style.items()
                                        if k != "roles"},
                                     "roles": roles_out}
    if "error_layer" in table:
        out["error_layer"] = table["error_layer"]
    return out


def _validate_v2(table: dict) -> None:
    if not table.get("styles"):
        raise ValueError("fit table has no styles")
    param_order = table.get("param_order")
    if not param_order or not isinstance(param_order, list):
        raise ValueError(
            "fit table %r needs a non-empty 'param_order' list of parameter "
            "names" % table.get("format", _FIT_FORMAT_V2))
    for s in table.get("sample_order", []):
        missing = [p for p in param_order if p not in s]
        if missing:
            raise ValueError(
                "sample %r is missing parameter(s) %s declared in param_order"
                % (s, missing))


def _validate_v3(table: dict) -> None:
    _validate_v2(table)
    for style_name, style in table.get("styles", {}).items():
        for name, group in style.get("repeat_groups", {}).items():
            missing = [k for k in ("unit_roles", "count", "pitch_dbu",
                                   "origin") if k not in group]
            if missing:
                raise ValueError(
                    "repeat group %r (style %r) is missing key(s) %s"
                    % (name, style_name, missing))
            for axis in ("x", "y"):
                for k in ("count", "pitch_dbu", "origin"):
                    if axis not in group[k]:
                        raise ValueError(
                            "repeat group %r (style %r): %r has no %r axis"
                            % (name, style_name, k, axis))


def _load_table(path: str) -> dict:
    """Load + NORMALISE a fit table to the canonical N-ary shape, cached by
    (path, mtime).  Accepts both the v2 (N-ary) and legacy v1 (2-ary) formats."""
    mtime = os.path.getmtime(path)
    key = (path, mtime)
    if key not in _TABLE_CACHE:
        with open(path, "r", encoding="utf-8") as fh:
            table = json.load(fh)
        fmt = table.get("format")
        if fmt == _FIT_FORMAT_V1:
            table = _normalise_v1(table)
            _validate_v2(table)
        elif fmt == _FIT_FORMAT_V2:
            _validate_v2(table)
        elif fmt == _FIT_FORMAT_V3:
            _validate_v3(table)
        else:
            raise ValueError(
                "fit table format is %r, expected %r (repeat groups), %r "
                "(N-ary) or %r (legacy)"
                % (fmt, _FIT_FORMAT_V3, _FIT_FORMAT_V2, _FIT_FORMAT_V1))
        _TABLE_CACHE[key] = table
    return _TABLE_CACHE[key]


def _edge_value(edge: dict, params: dict, param_order: list,
                sample_order: list) -> int:
    """Resolve one box edge to integer dbu for the given parameter values.

    N-ary edge model:
    - parametric: ``base + Σ_i coef[param_i] · params[param_i]`` over the
      ordered ``param_order`` (a missing coefficient is 0); refuses a
      non-integer dbu result (no silent rounding);
    - non_parametric: exact exemplar lookup — the sample whose EVERY parameter
      matches ``params`` selects ``values[i]``; a miss lists the available
      points (drawing conventions are never extrapolated)."""
    kind = edge.get("kind")
    if kind == "parametric":
        value = float(edge.get("base", 0.0))
        coef = edge.get("coef", {})
        for name in param_order:
            value += float(coef.get(name, 0.0)) * float(params[name])
        if abs(value - round(value)) > _INT_TOL:
            shown = " ".join("%s=%g" % (n, params[n]) for n in param_order)
            raise ValueError(
                "parametric edge gives non-integer dbu %r for %s"
                % (value, shown))
        return int(round(value))
    if kind == "non_parametric":
        for i, sample in enumerate(sample_order):
            if all(abs(float(sample[n]) - float(params[n])) < _INT_TOL
                   for n in param_order):
                return int(edge["values"][i])
        points = "; ".join(
            ", ".join("%s=%s" % (n, s.get(n)) for n in param_order)
            for s in sample_order)
        shown = " ".join("%s=%g" % (n, params[n]) for n in param_order)
        raise ValueError(
            "non-parametric edge has no exemplar for %s; available: %s. "
            "Drawing conventions are not extrapolated — add an exemplar and "
            "re-run the fitter." % (shown, points))
    raise ValueError("unknown edge kind %r" % kind)


# --- v3 repeat groups (byte-identical to klink.domains.structdevice.
# pcell_repeat.eval_count / eval_repeat_group; a drift between the two
# silently breaks fitted geometry, so tests/public/test_structdevice_
# pcell_math.py locks them together) ---

def _count_value(law: dict, params: dict, param_order: list) -> int:
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


def _group_boxes(group: dict, params: dict, param_order: list,
                 sample_order: list) -> list:
    """Expand one repeat group to concrete (layer, (x1, y1, x2, y2))
    elements — the same arithmetic as klink-side eval_repeat_group: a
    'centered' origin stores expr = first + last element origin, so the
    first origin is (expr - (count-1)*pitch)/2, refused when that is not
    an integer (no silent half-dbu shifts)."""
    counts, pitches, origins = {}, {}, {}
    for axis in ("x", "y"):
        counts[axis] = _count_value(group["count"][axis], params, param_order)
        pitches[axis] = _edge_value(group["pitch_dbu"][axis], params,
                                    param_order, sample_order)
        org = group["origin"][axis]
        expr = _edge_value(org["expr"], params, param_order, sample_order)
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
    out = []
    for role_name in sorted(group.get("unit_roles", {})):
        role = group["unit_roles"][role_name]
        e = role["edges"]
        x1 = _edge_value(e["x1"], params, param_order, sample_order)
        y1 = _edge_value(e["y1"], params, param_order, sample_order)
        x2 = _edge_value(e["x2"], params, param_order, sample_order)
        y2 = _edge_value(e["y2"], params, param_order, sample_order)
        for j in range(counts["y"]):
            for i in range(counts["x"]):
                ox = origins["x"] + i * pitches["x"]
                oy = origins["y"] + j * pitches["y"]
                out.append((str(role["layer"]),
                            (ox + x1, oy + y1, ox + x2, oy + y2)))
    return out


def _parse_ld(key: str) -> pya.LayerInfo:
    layer_s, dt_s = str(key).split("/")
    return pya.LayerInfo(int(layer_s), int(dt_s))


class KlinkFittedDevicePcell(pya.PCellDeclarationHelper):
    """One generic class; each registration binds a device definition.

    Parameters are declared FROM the bound table's ``param_order`` (any number,
    any names — the N-ary axes), plus a style choice list taken from the
    table's styles and the table path itself (editable, so a refit can be
    picked up per instance)."""

    def __init__(self, device_name: str, table_path: str, table: dict):
        super().__init__()
        self._device_name = device_name
        # The fit table is BOUND at registration -- its content lives on the
        # declaration, NOT as a per-instance path parameter. So a drawn PCell
        # instance carries only its geometry parameters (no file path leaks into
        # the layout); a refit is a re-registration, not a per-instance edit.
        self._table = table
        self._param_order = list(table.get("param_order", []))
        self._sample_order = table.get("sample_order", [])
        self._error_layer = (table.get("error_layer")
                             or _first_role_layer(table.get("styles", {})))
        # per-parameter default = the first sample point's value (or 1.0)
        first = self._sample_order[0] if self._sample_order else {}
        self._defaults = {n: float(first.get(n, 1.0))
                          for n in self._param_order}
        # UNIT-AGNOSTIC: the plugin does not impose a length unit. The edge
        # math is base + Σ coef·param in integer dbu, so the unit is the
        # author's choice, carried by the parameter NAME (w_um vs w_nm) and the
        # coefficient scale. A nm-scale process declares `w_nm` with integer
        # values; a um-scale process declares `w_um`. The optional table-level
        # `param_units` map only sets the GUI display suffix (cosmetic, never
        # used in produce), so a nm device shows clean integers, not decimals.
        units = table.get("param_units", {})
        for name in self._param_order:
            unit = units.get(name)
            label = "%s (%s)" % (name, unit) if unit else name
            extra = {"unit": unit} if unit else {}
            self.param(name, self.TypeDouble, label,
                       default=self._defaults[name], **extra)
        styles = sorted(table.get("styles", {}))
        style_decl = self.param("style", self.TypeString,
                                "Drawing style", default=styles[0])
        try:
            for choice in styles:
                style_decl.add_choice(choice, choice)
        except Exception:
            pass

    def _params(self) -> dict:
        return {n: getattr(self, n) for n in self._param_order}

    def display_text_impl(self):
        vals = " ".join("%s%g" % (n, getattr(self, n))
                        for n in self._param_order)
        return "%s %s %s" % (self._device_name, vals, self.style)

    def coerce_parameters_impl(self):
        for name in self._param_order:
            if getattr(self, name) <= 0:
                setattr(self, name, self._defaults[name])

    def _error(self, message: str):
        layer_idx = self.layout.layer(_parse_ld(self._error_layer))
        text = pya.Text("klink %s ERROR: %s" % (self._device_name, message),
                        pya.Trans(0, 0))
        text.size = 1000
        self.cell.shapes(layer_idx).insert(text)

    def produce_impl(self):
        table = self._table                 # bound at registration; no path read
        param_order = table.get("param_order", self._param_order)
        sample_order = table.get("sample_order", [])
        params = self._params()
        style = table.get("styles", {}).get(self.style)
        if style is None:
            self._error("style %r not in table (has: %s)"
                        % (self.style,
                           ", ".join(sorted(table.get("styles", {})))))
            return
        # validate-before-mutate: a failure yields error text ONLY
        planned = []
        try:
            for role_name in sorted(style.get("roles", {})):
                role = style["roles"][role_name]
                edges = role["edges"]
                box = pya.Box(
                    _edge_value(edges["x1"], params, param_order, sample_order),
                    _edge_value(edges["y1"], params, param_order, sample_order),
                    _edge_value(edges["x2"], params, param_order, sample_order),
                    _edge_value(edges["y2"], params, param_order, sample_order))
                planned.append((role["layer"], box))
            for role_name in sorted(style.get("repeat_groups", {})):
                group = style["repeat_groups"][role_name]
                for layer_key, (x1, y1, x2, y2) in _group_boxes(
                        group, params, param_order, sample_order):
                    planned.append((layer_key, pya.Box(x1, y1, x2, y2)))
        except Exception as exc:
            self._error("%s (role %r)" % (exc, role_name))
            return
        for layer_key, box in planned:
            self.cell.shapes(
                self.layout.layer(_parse_ld(layer_key))).insert(box)

    def can_create_from_shape_impl(self):
        return False


# ---------------------------------------------------------------------------
# Library + runtime registration
# ---------------------------------------------------------------------------

_STRUCTDEVICE_LIB_REF = None  # module-level anchor against GC


def register_structdevice_library():
    """Register the (initially empty) generic device library."""
    global _STRUCTDEVICE_LIB_REF
    if _STRUCTDEVICE_LIB_REF is not None:
        return _STRUCTDEVICE_LIB_REF

    lib = pya.Library()
    lib.description = "klink fitted device PCells (runtime-registered)"
    lib.register(_LIB_NAME)
    _STRUCTDEVICE_LIB_REF = lib
    return lib


def register_fitted_device(name: str, table_path: str) -> dict:
    """Register one fitted-device PCell from a table, at runtime.

    Called by the `pcell.register_fitted` RPC.  Errors are instructions;
    re-registering an existing name in one session is refused (KLayout's
    behavior for duplicate PCell names is not probe-verified — pick a new name
    or reload the plugin after a refit)."""
    if not name or not str(name).isidentifier():
        raise ValueError(
            "device name %r must be a simple identifier" % (name,))
    if name in _REGISTERED:
        raise ValueError(
            "device %r is already registered in this KLayout session "
            "(from %s); pick a new name, or hot-reload the plugin after "
            "a refit" % (name, _REGISTERED[name]))
    table = _load_table(table_path)  # validates format + styles + param_order
    lib = register_structdevice_library()
    decl = KlinkFittedDevicePcell(name, table_path, table)
    lib.layout().register_pcell(name, decl)
    _REGISTERED[name] = table_path
    try:
        # Push the new declaration into any layout already using the library,
        # so no manual GUI "refresh libraries" step is needed.
        lib.refresh()
    except Exception:
        pass
    return {
        "library": _LIB_NAME,
        "pcell": name,
        "param_order": list(table.get("param_order", [])),
        "styles": sorted(table.get("styles", {})),
        "samples": table.get("sample_order", []),
        "fit_table": table_path,
    }

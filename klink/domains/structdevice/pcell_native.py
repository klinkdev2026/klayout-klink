"""Native-PCell registration from a verified reference generator — the
code-true exit of the T-Cell transpile route.

The fitter (:mod:`.pcell_fitter` v2, :mod:`.pcell_repeat` v3) REFUSES
geometry its locked model classes cannot express exactly (parity
alternation, bilinear M·L extents, non-grid positions).  That refusal is
guidance toward THIS route: port the source generator's logic to a small
Python reference function, prove it byte-exact against ground truth
(``tcell_workflows.py verify``), then register THAT SAME verified
function as a live KLayout PCell — full parameter fidelity, any code.

The reference generator contract is harvest-native, identical to the
exemplar/verify format used everywhere else in the fitting loop:

    def my_device(params: dict) -> dict:
        # params: {name: value} exactly as declared in params_spec
        # returns {"L/D": [[x1, y1, x2, y2], ...], ...} in INTEGER dbu
        ...

Registration happens through the ``exec.python`` escape hatch — by
design there is no plugin RPC that accepts code (the plugin takes data
tables only; arbitrary code goes through the one explicit, auditable
escape hatch).  This module builds that exec payload and interprets its
result; klink ships ZERO device data — the generator source, parameter
spec and names are caller input.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Sequence

DEFAULT_LIBRARY = "klink_custom"

_RESULT_SENTINEL = "KLINK_NATIVE_PCELL_RESULT "

_PARAM_TYPES = ("int", "float")


class NativePCellError(ValueError):
    """Bad input or failed remote registration; the message says what to fix."""


def _validate_spec(params_spec: Sequence[Mapping[str, Any]]) -> None:
    if not params_spec:
        raise NativePCellError(
            "params_spec is empty; a native PCell needs at least one "
            "parameter — [{'name': ..., 'type': 'int'|'float', "
            "'default': ...}, ...]")
    seen = set()
    for spec in params_spec:
        name = spec.get("name")
        if not isinstance(name, str) or not name.isidentifier() \
                or name.startswith("_"):
            raise NativePCellError(
                "param name %r must be a public Python identifier" % (name,))
        if name in seen:
            raise NativePCellError("duplicate param name %r" % (name,))
        seen.add(name)
        if spec.get("type") not in _PARAM_TYPES:
            raise NativePCellError(
                "param %r type must be one of %s, got %r"
                % (name, _PARAM_TYPES, spec.get("type")))
        default = spec.get("default")
        if not isinstance(default, (int, float)) or isinstance(default, bool):
            raise NativePCellError(
                "param %r needs a numeric default, got %r" % (name, default))


def _validate_layer_map(layer_map: Mapping[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, ld in layer_map.items():
        parts = str(ld).split("/")
        if len(parts) != 2 or not all(p.strip().lstrip("-").isdigit()
                                      for p in parts):
            raise NativePCellError(
                "layer_map[%r] must be 'L/D' GDS numbers, got %r"
                % (key, ld))
        out[str(key)] = "%d/%d" % (int(parts[0]), int(parts[1]))
    return out


def build_registration_source(
    generator_source: str,
    entry: str,
    name: str,
    params_spec: Sequence[Mapping[str, Any]],
    *,
    library: str = DEFAULT_LIBRARY,
    layer_map: Mapping[str, str] | None = None,
) -> str:
    """Build the ``exec.python`` payload that registers the PCell.

    ``generator_source`` is the full text of the reference generator
    file; ``entry`` the function name inside it (harvest-native boxes
    contract, module docstring).  ``layer_map`` translates generator
    layer keys (e.g. source-editor layer NAMES, the same keys the
    byte-exact verify compared) to ``"L/D"`` GDS numbers; keys already
    in ``"L/D"`` form need no entry.  The payload validates everything
    it can only know inside KLayout (reserved helper attributes,
    duplicate registration) with instructive errors, and reports
    success as one ``KLINK_NATIVE_PCELL_RESULT {json}`` stdout line."""
    if not name or not str(name).isidentifier():
        raise NativePCellError(
            "PCell name %r must be a simple identifier" % (name,))
    if not entry or not str(entry).isidentifier():
        raise NativePCellError(
            "entry %r must be a function name inside the generator "
            "source" % (entry,))
    if not library or not str(library).isidentifier():
        raise NativePCellError(
            "library %r must be a simple identifier" % (library,))
    _validate_spec(params_spec)
    try:
        compile(generator_source, "<%s>" % entry, "exec")
    except SyntaxError as exc:
        raise NativePCellError(
            "generator source does not parse: %s (fix the reference "
            "file before registering)" % (exc,)) from exc
    spec_json = json.dumps([{"name": s["name"], "type": s["type"],
                             "default": s["default"],
                             "description": str(s.get("description",
                                                      s["name"]))}
                            for s in params_spec])
    # every caller string reaches the payload as a json literal (valid
    # Python literal, no injection surface)
    return _PAYLOAD_TEMPLATE % {
        "source": json.dumps(generator_source),
        "entry": json.dumps(entry),
        "name": json.dumps(name),
        "library": json.dumps(library),
        "spec": spec_json,
        "layer_map": json.dumps(_validate_layer_map(layer_map or {})),
        "sentinel": _RESULT_SENTINEL,
    }


def register_native_pcell(
    client,
    generator_source: str,
    entry: str,
    name: str,
    params_spec: Sequence[Mapping[str, Any]],
    *,
    library: str = DEFAULT_LIBRARY,
    layer_map: Mapping[str, str] | None = None,
) -> Dict[str, Any]:
    """Register the PCell in the live KLayout via ``client.exec_python``.

    Returns the remote report (library, pcell, params).  Raises
    :class:`NativePCellError` carrying the remote message when the
    payload refused (duplicate name, reserved param, bad boxes)."""
    code = build_registration_source(generator_source, entry, name,
                                     params_spec, library=library,
                                     layer_map=layer_map)
    res = client.exec_python(code, result_mode="none")
    exc = res.get("exception")
    if exc is not None:
        raise NativePCellError(
            "remote registration failed: %s: %s\n--- stdout ---\n%s"
            % (exc.get("type"), exc.get("message"), res.get("stdout", "")))
    for line in str(res.get("stdout", "")).splitlines():
        if line.startswith(_RESULT_SENTINEL):
            return json.loads(line[len(_RESULT_SENTINEL):])
    raise NativePCellError(
        "registration produced no result line; stdout:\n%s"
        % res.get("stdout", ""))


_PAYLOAD_TEMPLATE = '''\
import json
import pya

_SRC = %(source)s
_ENTRY = %(entry)s
_NAME = %(name)s
_LIB = %(library)s
_SPEC = json.loads(%(spec)r)
_LAYER_MAP = json.loads(%(layer_map)r)

_ns = {}
exec(compile(_SRC, "<klink_native:" + _NAME + ">", "exec"), _ns)
if _ENTRY not in _ns or not callable(_ns[_ENTRY]):
    raise ValueError(
        "entry %%r is not a function in the generator source; define "
        "def %%s(params) returning {'L/D': [[x1,y1,x2,y2] dbu, ...]}"
        %% (_ENTRY, _ENTRY))
_gen = _ns[_ENTRY]

for _s in _SPEC:
    if hasattr(pya.PCellDeclarationHelper, _s["name"]):
        raise ValueError(
            "param name %%r collides with a PCellDeclarationHelper "
            "attribute; rename it in params_spec AND the generator"
            %% (_s["name"],))

# anchors live on the pya module: they must survive exec.python resets
# and GC, exactly like the plugin's module-level library anchor
_libs = getattr(pya, "_klink_native_libs", None)
if _libs is None:
    _libs = {}
    pya._klink_native_libs = _libs
_regd = getattr(pya, "_klink_native_pcells", None)
if _regd is None:
    _regd = {}
    pya._klink_native_pcells = _regd

if _NAME in _regd:
    raise ValueError(
        "PCell %%r is already registered in this KLayout session; pick "
        "a new name, or restart KLayout after changing the generator"
        %% (_NAME,))


class _KlinkNativePCell(pya.PCellDeclarationHelper):
    def __init__(self):
        super(_KlinkNativePCell, self).__init__()
        for s in _SPEC:
            t = (self.TypeInt if s["type"] == "int" else self.TypeDouble)
            self.param(s["name"], t, s["description"],
                       default=s["default"])

    def display_text_impl(self):
        vals = ", ".join("%%s=%%s" %% (s["name"], getattr(self, s["name"]))
                         for s in _SPEC)
        return "%%s(%%s)" %% (_NAME, vals)

    def produce_impl(self):
        params = {}
        for s in _SPEC:
            v = getattr(self, s["name"])
            params[s["name"]] = int(v) if s["type"] == "int" else float(v)
        boxes_by_layer = _gen(params)
        for layer_key in sorted(boxes_by_layer):
            ld = _LAYER_MAP.get(str(layer_key), str(layer_key))
            try:
                l, d = (int(x) for x in str(ld).split("/"))
            except ValueError:
                raise ValueError(
                    "generator layer %%r has no GDS mapping; pass "
                    "layer_map={%%r: 'L/D'} at registration"
                    %% (layer_key, layer_key))
            li = self.layout.layer(pya.LayerInfo(l, d))
            for b in boxes_by_layer[layer_key]:
                x1, y1, x2, y2 = (int(v) for v in b)
                self.cell.shapes(li).insert(pya.Box(x1, y1, x2, y2))

    def can_create_from_shape_impl(self):
        return False


_lib = _libs.get(_LIB)
if _lib is None:
    _lib = pya.Library()
    _lib.description = "klink native PCells (runtime-registered)"
    _lib.register(_LIB)
    _libs[_LIB] = _lib
_lib.layout().register_pcell(_NAME, _KlinkNativePCell())
_regd[_NAME] = _LIB
try:
    _lib.refresh()
except Exception:
    pass
print(%(sentinel)r + json.dumps({
    "library": _LIB, "pcell": _NAME,
    "params": [s["name"] for s in _SPEC]}))
'''

"""Deterministic tools: a directory (tool.json + run.py) run KLayout-native, inside one undo step.

``<registry_root>/tools/<id>/tool.json`` declares the interface (title, params, presets,
selection input); ``run.py`` is EITHER a module defining ``run(ctx, params) -> dict`` OR a
plain KLayout pya macro exactly as written for the macro IDE (no top-level ``def run``; it is
executed once per run as ``__main__``, with ``pya``, ``ctx`` and ``params`` as globals; see
``docs/TOOL_AUTHORING.md``). Its stdout is captured into the result. The plugin loads the tool, resolves params (defaults <- preset <- given), checks the selection,
asks the params in ONE form dialog (every param, pre-filled; a ``from`` layer param as a list
of the layers recognised in the live view, plus ``new layer…``) -- without a click the single
candidate of a ``from`` layer is filled in, else ERR_TOOL_PARAMS lists them -- runs ``run`` inside ``auto_txn`` (any
exception rolls the whole edit back) and journals every run to
``<registry_root>/journals/<session>.tools.jsonl``. Install/export/list-on-disk and the
button manifest live in the ``klink`` package (``klink tool``). See ``docs/TOOLS.md``.

The validation rules below MUST stay identical to ``klink/tools.py`` (parity-tested).
Stdlib + pya only; runs on KLayout's GUI thread.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import re
import sys
import time
import traceback
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

from .errors import RpcError
from .log import get_logger
from .session_registry import default_registry_root

_log = get_logger("tools")

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
TYPES = ("int", "number", "string", "bool", "enum", "layer")
PROMPTS = ("missing", "always", "never")
# where a layer param's list comes from (docs/TOOLS.md "Layer recognition")
LAYER_FROM = ("region", "selection", "layout")
# "1/0", "1" (datatype 0), "1,0", "1 0", "1:0"; surrounding whitespace allowed
LAYER_RE = re.compile(r"^\s*(\d+)\s*(?:[/,:\s]\s*(\d+))?\s*$")
_TRUE, _FALSE = ("true", "yes", "y", "1", "on"), ("false", "no", "n", "0", "off")
ICON_EXTS = (".png", ".svg")  # toolbar icon formats (KLayout's Qt renders both)
ICON_REASON = "'icon' must be a file name inside the tool folder (relative, no '..') ending in .png or .svg"


def icon_name_ok(icon) -> bool:
    """True for a relative .png/.svg path that stays inside the tool folder (same on every OS)."""
    if not isinstance(icon, str) or not icon.strip():
        return False
    p = PureWindowsPath(icon)  # treats / and \ as separators everywhere; anchor = drive or root
    return not p.anchor and ".." not in p.parts and p.suffix.lower() in ICON_EXTS


class ToolError(RpcError):
    """A tool could not run; ``code`` is one of the ERR_TOOL_* codes (or a passed-through RpcError's)."""


# ------------------------------------------------------------- validation (parity with klink) --
def _parse_layer(value) -> str | None:
    """Canonical "L/D" for "1/0", "1", "1,0", "1 0", "1:0", an int (datatype 0) or [L, D]; else None."""
    def num(v):
        return isinstance(v, int) and not isinstance(v, bool) and v >= 0
    if num(value):
        return f"{value}/0"
    if isinstance(value, list) and len(value) == 2 and all(num(v) for v in value):
        return f"{value[0]}/{value[1]}"
    m = LAYER_RE.match(value) if isinstance(value, str) else None
    return None if m is None else f"{int(m.group(1))}/{int(m.group(2) or 0)}"


def coerce(pdef: dict, value):
    """Validated value for one param (strings from a dialog are converted); ValueError(reason)."""
    kind = pdef["type"]
    if kind in ("int", "number"):
        conv, need = (int, "an integer") if kind == "int" else (float, "a number")
        if isinstance(value, str):
            try:
                value = conv(value.strip())
            except ValueError:
                raise ValueError(f"expected {need}") from None
        if isinstance(value, bool) or not isinstance(value, int if kind == "int" else (int, float)):
            raise ValueError(f"expected {need}")
        if pdef.get("min") is not None and value < pdef["min"]:
            raise ValueError(f"must be >= {pdef['min']}")
        if pdef.get("max") is not None and value > pdef["max"]:
            raise ValueError(f"must be <= {pdef['max']}")
    elif kind == "bool":
        if isinstance(value, str) and value.strip().lower() in _TRUE + _FALSE:
            value = value.strip().lower() in _TRUE
        if not isinstance(value, bool):
            raise ValueError("expected yes/no (a boolean)")
    elif kind == "layer":
        same_ok = pdef.get("allow_same") is True  # "same" is opt-in per param
        if same_ok and isinstance(value, str) and value.strip().lower() == "same":
            return "same"
        layer = _parse_layer(value)
        if layer is None:
            raise ValueError("expected a layer as L/D (e.g. 1/0 or just 1)"
                             + (" or 'same'" if same_ok else ""))
        return layer
    elif kind == "enum" and value not in pdef["choices"]:
        raise ValueError("expected one of " + ", ".join(pdef["choices"]))
    elif kind == "string" and not isinstance(value, str):
        raise ValueError("expected a string")
    return value


def _check_param(name: str, pdef) -> str | None:
    if not ID_RE.fullmatch(name):
        return f"param name {name!r} must match ^[a-z][a-z0-9_]{{0,39}}$"
    if not isinstance(pdef, dict):
        return f"params.{name} must be an object"
    if pdef.get("type") not in TYPES:
        return f"params.{name}.type must be one of " + ", ".join(TYPES)
    if pdef["type"] == "enum":
        ch = pdef.get("choices")
        if not isinstance(ch, list) or not ch or not all(isinstance(c, str) for c in ch) \
                or len(set(ch)) != len(ch):
            return f"params.{name}: enum needs a non-empty 'choices' list of distinct strings"
    for key in ("min", "max"):
        v = pdef.get(key)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
            return f"params.{name}.{key} must be a number"
    for key in ("title", "doc", "unit"):
        if pdef.get(key) is not None and not isinstance(pdef[key], str):
            return f"params.{name}.{key} must be a string"
    if "allow_same" in pdef and (pdef["type"] != "layer" or not isinstance(pdef["allow_same"], bool)):
        return f"params.{name}.allow_same must be true/false, on a layer param"
    for key in ("allow_new", "skip_selection_layers"):
        if key in pdef and (pdef["type"] != "layer" or not isinstance(pdef[key], bool)):
            return f"params.{name}.{key} must be true/false, on a layer param"
    if "from" in pdef and (pdef["type"] != "layer" or pdef["from"] not in LAYER_FROM):
        return f"params.{name}.from must be one of " + ", ".join(LAYER_FROM) + ", on a layer param"
    for key in ("exclude", "allow_new", "skip_selection_layers"):
        if key in pdef and "from" not in pdef:
            return f"params.{name}.{key} needs 'from' (the layer list to pick from)"
    if "default" in pdef:
        try:
            coerce(pdef, pdef["default"])
        except ValueError as exc:
            return f"params.{name}.default: {exc}"
    return None


def validate_tool(spec, dirname: str) -> str | None:
    """None when tool.json is usable, else a one-line reason."""
    if not isinstance(spec, dict):
        return "not a JSON object"
    tid = spec.get("id")
    if not isinstance(tid, str) or not ID_RE.fullmatch(tid):
        return "'id' must match ^[a-z][a-z0-9_]{0,39}$"
    if tid != dirname:
        return f"'id' {tid!r} does not match the directory name {dirname!r}"
    if not isinstance(spec.get("title"), str) or not spec["title"].strip():
        return "'title' must be a non-empty string"
    if spec.get("label") is not None and (not isinstance(spec["label"], str) or not spec["label"].strip()):
        return "'label' must be a non-empty string"
    if spec.get("description") is not None and not isinstance(spec["description"], str):
        return "'description' must be a string"
    if spec.get("icon") is not None and not icon_name_ok(spec["icon"]):
        return ICON_REASON
    inputs = spec.get("inputs", {})
    if not isinstance(inputs, dict):
        return "'inputs' must be an object"
    for kind, idef in inputs.items():
        if kind != "selection":
            return f"unknown input kind {kind!r} (supported: selection)"
        if not isinstance(idef, dict):
            return "'inputs.selection' must be an object"
        for key in ("min_shapes", "min"):
            n = idef.get(key, 0)
            if isinstance(n, bool) or not isinstance(n, int) or n < 0:
                return f"inputs.selection.{key} must be an integer >= 0"
    params = spec.get("params", {})
    if not isinstance(params, dict):
        return "'params' must be an object"
    for name, pdef in params.items():
        problem = _check_param(name, pdef)
        if problem:
            return problem
    for name, pdef in params.items():
        if "exclude" not in pdef:
            continue
        ex = pdef["exclude"]
        if not isinstance(ex, list) or not all(isinstance(n, str) for n in ex):
            return f"params.{name}.exclude must be a list of layer param names"
        for other in ex:
            if other == name or other not in params or params[other].get("type") != "layer":
                return f"params.{name}.exclude: {other!r} is not another layer param of this tool"
    presets = spec.get("presets", {})
    if not isinstance(presets, dict):
        return "'presets' must be an object"
    for name, pre in presets.items():
        if not ID_RE.fullmatch(name):
            return f"preset name {name!r} must match ^[a-z][a-z0-9_]{{0,39}}$"
        if not isinstance(pre, dict):
            return f"presets.{name} must be an object"
        if pre.get("title") is not None and not isinstance(pre["title"], str):
            return f"presets.{name}.title must be a string"
        if not isinstance(pre.get("params"), dict):
            return f"presets.{name}.params must be an object"
        for key, value in pre["params"].items():
            if key not in params:
                return f"presets.{name}: unknown param {key!r}"
            try:
                coerce(params[key], value)
            except ValueError as exc:
                return f"presets.{name}.params.{key}: {exc}"
    if spec.get("prompt", "missing") not in PROMPTS:
        return "'prompt' must be one of " + ", ".join(PROMPTS)
    return None


# ------------------------------------------------------------------------- loading --
def tools_dir(root: str | Path | None = None) -> Path:
    return (Path(root) if root is not None else default_registry_root()) / "tools"


def load_tool(tool_id: str, root: str | Path | None = None) -> dict:
    """Parsed + normalized tool.json with ``dir`` and ``run_path``; ToolError when unusable."""
    folder = tools_dir(root) / str(tool_id)
    if not isinstance(tool_id, str) or not ID_RE.fullmatch(tool_id) or not folder.is_dir():
        have = ", ".join(t["id"] for t in list_tools(root)) or "none"
        raise ToolError("ERR_TOOL_NOT_FOUND", f"no tool {tool_id!r} installed in {folder.parent}",
                        hint=f"installed tools: {have}; install one with: klink tool install <dir|zip>")
    try:
        spec = json.loads((folder / "tool.json").read_text(encoding="utf-8"))
        problem = validate_tool(spec, tool_id)
    except (OSError, ValueError) as exc:
        problem = f"tool.json unreadable: {exc}"
    if problem is None and not (folder / "run.py").is_file():
        problem = "missing run.py"
    if problem:
        raise ToolError("ERR_TOOL_NOT_FOUND", f"tool {tool_id!r} is installed but unusable: {problem}",
                        hint=f"fix {folder} or reinstall it with: klink tool install <dir|zip>")
    params = {n: dict(p) for n, p in spec.get("params", {}).items()}
    for name, p in params.items():
        if "default" in p:
            p["default"] = coerce(p, p["default"])
    presets = {n: {"title": p.get("title") or n,
                   "params": {k: coerce(params[k], v) for k, v in p["params"].items()}}
               for n, p in spec.get("presets", {}).items()}
    return {"id": tool_id, "title": spec["title"], "description": spec.get("description") or "",
            "label": (spec.get("label") or tool_id[:6].upper()).strip()[:6],
            "min_shapes": spec.get("inputs", {}).get("selection", {}).get("min_shapes", 0),
            "min_selected": spec.get("inputs", {}).get("selection", {}).get("min", 0),
            "params": params, "presets": presets, "prompt": spec.get("prompt", "missing"),
            "dir": str(folder), "run_path": str(folder / "run.py")}


def list_tools(root: str | Path | None = None) -> list[dict]:
    folder, out = tools_dir(root), []
    for d in sorted(p for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []:
        try:
            t = load_tool(d.name, root)
            out.append({"id": t["id"], "title": t["title"], "label": t["label"],
                        "presets": list(t["presets"]), "problem": None})
        except ToolError as exc:
            out.append({"id": d.name, "title": None, "label": None, "presets": [], "problem": exc.message})
    return out


# ------------------------------------------------------------------------- params --
def resolve_params(tool: dict, preset=None, given=None, interactive: bool = False):
    """(values, names to ask). Precedence: defaults <- preset <- given; ToolError(ERR_TOOL_PARAMS).

    A ``from`` layer param without a value is not an error here: it is filled from the live
    layer list later (``auto_layers``), or asked as a list."""
    pdefs = tool["params"]
    if preset is not None and preset not in tool["presets"]:
        raise ToolError("ERR_TOOL_PARAMS", f"tool {tool['id']!r} has no preset {preset!r}",
                        hint="presets: " + (", ".join(tool["presets"]) or "none (run it without a preset)"))
    pinned = dict(tool["presets"][preset]["params"]) if preset is not None else {}
    for key, value in (given or {}).items():
        if key not in pdefs:
            raise ToolError("ERR_TOOL_PARAMS", f"tool {tool['id']!r} has no param {key!r}",
                            hint="params: " + (", ".join(pdefs) or "none"))
        try:
            pinned[key] = coerce(pdefs[key], value)
        except ValueError as exc:
            raise ToolError("ERR_TOOL_PARAMS", f"param {key!r}: {exc}",
                            hint=f"pass a valid {pdefs[key]['type']} value for {key!r}") from None
    values = {n: p["default"] for n, p in pdefs.items() if "default" in p}
    values.update(pinned)
    policy = tool["prompt"]
    ask = [] if policy == "never" else list(pdefs) if policy == "always" else [n for n in pdefs if n not in pinned]
    unset = [n for n in pdefs if n not in values and not pdefs[n].get("from")
             and not (interactive and n in ask)]
    if unset:
        raise ToolError("ERR_TOOL_PARAMS", f"tool {tool['id']!r}: required param(s) without a value: "
                        + ", ".join(unset), hint="pass them as params (CLI: --param name=value)"
                        + ("" if policy == "never" else " or run with interactive=true to be asked"))
    return values, ask


# ------------------------------------------------------------- layer recognition --
NEW_LAYER = "new layer…"
SAME_LABEL = "same layer as the first selected shape"
COUNT_CAP = 200         # shape visits per layer (and per selected instance) for a label; more
                        # shows as "200+". Bounds the work per click: layers x COUNT_CAP visits.
_MAX_MEMBERS = 256      # array members outlined one by one; a bigger array adds its bbox


def _area_polygon(shape):
    """The shape's polygon for boxes, polygons and paths; None for texts, edges, points."""
    if shape.is_box() or shape.is_polygon() or shape.is_simple_polygon() or shape.is_path():
        return shape.polygon
    return None


def selection_outline(pya, selected):
    """Merged region the selection marks, in current-cell coordinates: selected shapes'
    polygons (texts add nothing) + selected instances' cell bboxes (every array member)."""
    clip = pya.Region()
    for obj in selected:
        if obj.is_cell_inst():
            inst = obj.inst()
            parent = obj.trans() * inst.cplx_trans.inverted()
            box = inst.cell.bbox()
            if box.empty():
                continue
            if inst.size() <= _MAX_MEMBERS:
                for member in inst.cell_inst.each_cplx_trans():
                    clip.insert(pya.Polygon(box).transformed(parent * member))
            else:
                clip.insert(pya.Polygon(inst.bbox()).transformed(parent))
        else:
            poly = _area_polygon(obj.shape)
            if poly is not None:
                clip.insert(poly.transformed(obj.trans()))
    return clip.merged()


def _count(it, cap=COUNT_CAP):
    """(shapes delivered by a RecursiveShapeIterator, capped?) -- stops after ``cap``."""
    n = 0
    while not it.at_end():
        n += 1
        if n >= cap:
            return cap, True
        it.next()
    return n, False


def _cell_bbox(cell, li):
    try:
        return cell.bbox(li)
    except TypeError:  # very old KLayout: no bbox(layer)
        return cell.bbox_per_layer(li)


def _panel_names(view, cv_index):
    """layer index -> name shown in the layer panel (only entries that have a name)."""
    names = {}
    try:
        it = view.begin_layers()
        while not it.at_end():
            lp = it.current()
            if not lp.has_children() and lp.cellview() == cv_index and lp.name:
                li = lp.layer_index()
                if li >= 0:
                    names.setdefault(li, lp.name)
            it.next()
    except Exception:  # no view / a stand-in without a layer panel
        pass
    return names


def layer_candidates(ctx, scope: str, skip_selection_layers=None) -> list[dict]:
    """Layers the live view offers for ``scope`` (``region`` / ``selection`` / ``layout``).

    ``ctx`` needs ``layout``, ``cell``, ``selected`` (ObjectInstPath list) and optionally
    ``view``/``pya``. Returns ``[{"layer": "L/D", "index", "name", "shapes", "capped",
    "selected"}]`` (``selected`` = selected shapes on that layer). ``selection`` / ``layout``
    are sorted by layer/datatype. ``region``: ALL layers with area geometry (current cell,
    hierarchy included) overlapping the selection's outline -- the selected shapes' own layers
    too -- ordered by priority: selected shapes on >= 2 layers (operands) -> those layers
    first in selection order, then the rest by L/D; selected shapes on ONE layer (a marker)
    -> the other layers by L/D, the marker's layer last; otherwise by L/D. Legacy opt-in:
    ``skip_selection_layers`` true makes the selected shapes not count (a layer whose only
    geometry there is the selection drops out) and sorts by L/D.
    ``selection``: layers of the selected shapes plus layers with geometry inside selected
    instances. ``layout``: every layer of the layout (count = shapes in the current cell
    hierarchy). Counting stops after ``COUNT_CAP`` shape visits per layer (any shape type,
    through the spatial index; ``capped`` true, label ``N+``), so a click costs at most
    layers x ``COUNT_CAP`` visits however big the layout is."""
    if scope not in LAYER_FROM:
        raise ValueError("scope must be one of " + ", ".join(LAYER_FROM))
    pya = getattr(ctx, "pya", None) or _pya()
    layout, cell, selected = ctx.layout, ctx.cell, list(getattr(ctx, "selected", None) or [])
    view = getattr(ctx, "view", None)
    cv_index = 0
    try:
        cv_index = view.active_cellview_index() if view is not None else 0
    except Exception:
        pass
    infos = {}
    for li in layout.layer_indexes():
        info = layout.get_info(li)
        if info.layer >= 0 and info.datatype >= 0:
            infos[li] = info
    counts = {}  # li -> (n, capped)
    if scope == "layout":
        for li in infos:
            counts[li] = (0, False) if _cell_bbox(cell, li).empty() else _count(cell.begin_shapes_rec(li))
    elif scope == "selection":
        for obj in selected:
            if obj.is_cell_inst():
                icell = obj.inst().cell
                for li in infos:
                    if counts.get(li, (0, False))[1] or _cell_bbox(icell, li).empty():
                        continue  # already at the cap: no more visits for this layer
                    n, capped = _count(icell.begin_shapes_rec(li))
                    old = counts.get(li, (0, False))
                    counts[li] = (min(old[0] + n, COUNT_CAP), old[1] or capped or old[0] + n >= COUNT_CAP)
            elif obj.layer in infos:
                old = counts.get(obj.layer, (0, False))
                counts[obj.layer] = (old[0] + 1, old[1])
    else:  # region
        skip = bool(skip_selection_layers)
        clip = selection_outline(pya, selected)
        own = {}  # li -> [(cell_index, Shape)] of the selected shapes
        if skip:
            for obj in selected:
                if not obj.is_cell_inst():
                    own.setdefault(obj.layer, []).append((obj.cell_index(), obj.shape))
        if not clip.is_empty():
            box = clip.bbox()
            for li in infos:
                polys, capped, mine, visits = [], False, own.get(li, ()), 0
                it = cell.begin_shapes_rec_overlapping(li, box)
                while not it.at_end():
                    visits += 1  # every shape visit counts (texts too): the work stops at the cap
                    if visits > COUNT_CAP:
                        capped = True
                        break
                    shape = it.shape()
                    if not (mine and any(ci == it.cell_index() and shape == s for ci, s in mine)):
                        poly = _area_polygon(shape)
                        if poly is not None:
                            polys.append(poly.transformed(it.trans()))
                    it.next()
                n = 0
                if polys:
                    reg = pya.Region()
                    reg.merged_semantics = False  # count shapes, not merged islands
                    for poly in polys:
                        reg.insert(poly)
                    n = reg.overlapping(clip).count()
                if capped:  # more shapes than visited: "n+" (kept even at 0+, never hidden)
                    counts[li] = (n, True)
                elif n:
                    counts[li] = (n, False)
    names = _panel_names(view, cv_index) if view is not None else {}
    sel_n, sel_order = {}, []  # li -> selected shapes on it; distinct layers in selection order
    for obj in selected:
        if not obj.is_cell_inst():
            if obj.layer not in sel_n:
                sel_order.append(obj.layer)
            sel_n[obj.layer] = sel_n.get(obj.layer, 0) + 1
    out = []
    for li, (n, capped) in counts.items():
        info = infos[li]
        out.append({"layer": f"{info.layer}/{info.datatype}", "index": li,
                    "name": names.get(li) or info.name or None, "shapes": n, "capped": capped,
                    "selected": sel_n.get(li, 0)})
    out.sort(key=lambda c: tuple(int(v) for v in c["layer"].split("/")))
    if scope == "region" and not skip and sel_order:
        if len(sel_order) >= 2:  # operands selected: their layers first, in selection order
            rank = {li: i for i, li in enumerate(sel_order)}
            out.sort(key=lambda c: rank.get(c["index"], len(rank)))  # stable: rest stays by L/D
        else:  # a marker on one layer: the layers under it first, the marker's own layer last
            out.sort(key=lambda c: c["index"] == sel_order[0])
    return out


def candidate_label(c: dict) -> str:
    """``1/0  metal1  (3 shapes) [selected]`` -- the list entry for one candidate."""
    n = f"{c['shapes']}+" if c.get("capped") else str(c["shapes"])
    return f"{c['layer']}  " + (f"{c['name']}  " if c.get("name") else "") \
        + f"({n} shape{'' if n == '1' else 's'})" + (" [selected]" if c.get("selected") else "")


def selection_summary(layout, selected) -> str:
    """``Selected: 2 shapes (1/0 x1, 100/0 x1), 1 instance`` (the first dialog's first line)."""
    per, insts, shapes = {}, 0, 0
    for obj in selected:
        if obj.is_cell_inst():
            insts += 1
            continue
        shapes += 1
        try:
            info = layout.get_info(obj.layer)
            key = (info.layer, info.datatype)
        except Exception:
            continue
        per[key] = per.get(key, 0) + 1
    parts = []
    if shapes:
        parts.append(f"{shapes} shape{'' if shapes == 1 else 's'}" + (
            " (" + ", ".join(f"{k[0]}/{k[1]} x{v}" for k, v in sorted(per.items())) + ")" if per else ""))
    if insts:
        parts.append(f"{insts} instance{'' if insts == 1 else 's'}")
    return "Selected: " + (", ".join(parts) or "nothing")


class LayerLists:
    """``layer_candidates`` for one run, computed once per (scope, skip) at click time."""

    def __init__(self, ctx):
        self.ctx, self.memo = ctx, {}

    def get(self, scope, skip=None):
        key = (scope, bool(skip))
        if key not in self.memo:
            self.memo[key] = layer_candidates(self.ctx, scope, key[1])
        return self.memo[key]


def layer_choices(tool: dict, name: str, layers, decided: dict):
    """(candidates, fell_back) of a ``from`` param after ``exclude`` (values ``decided`` so far);
    an empty region/selection list falls back to the layout's layers."""
    p = tool["params"][name]
    taken = {decided[n] for n in p.get("exclude", ()) if n in decided}
    skip = p.get("skip_selection_layers", False)
    cands = [c for c in layers.get(p["from"], skip) if c["layer"] not in taken]
    if cands or p["from"] == "layout":
        return cands, False
    return [c for c in layers.get("layout", skip) if c["layer"] not in taken], True


def _entries(p: dict, cands) -> list:
    """[(value, label)]: "same" first (allow_same), the candidates, ``new layer…`` last (allow_new, value None)."""
    out = [("same", SAME_LABEL)] if p.get("allow_same") else []
    out += [(c["layer"], candidate_label(c)) for c in cands]
    if p.get("allow_new"):
        out.append((None, NEW_LAYER))
    return out


_NOTHING = {
    "region": ("nothing is under the selected region",
               "select shapes or instances whose outline covers geometry on the layers to use"),
    "selection": ("nothing selected sits on a layer",
                  "select shapes (or instances) on the layers to use"),
    "layout": ("the layout has no layer", "draw on a layer first"),
}


def _no_layer_error(tool: dict, name: str) -> ToolError:
    p = tool["params"][name]
    what, todo = _NOTHING[p["from"]]
    other = "" if p["from"] == "layout" else " and the layout has no other layer"
    return ToolError("ERR_TOOL_INPUT", f"no layer to offer for {p.get('title') or name!r}: {what}{other}",
                     hint=f"{todo}, then run the tool again")


def _pick_layer(dlg, tool: dict, name: str, layers, decided: dict, label: str, cur):
    """(value, auto?) for one ``from`` param; None on cancel. One list dialog, or none."""
    p = tool["params"][name]
    cands, fell_back = layer_choices(tool, name, layers, decided)
    entries = _entries(p, cands)
    if not entries:
        raise _no_layer_error(tool, name)
    vals, labels = [v for v, _ in entries], [lb for _, lb in entries]
    if len(entries) == 1 and vals[0] is not None:
        return vals[0], True
    if fell_back:
        label += f"\n({_NOTHING[p['from']][0]}; all layers of the layout)"
    if not (len(entries) == 1 and vals[0] is None):  # only "new layer…": straight to the text field
        index = vals.index(cur) if cur is not None and cur in vals else (
            len(vals) - 1 if p.get("allow_new") and cur is not None else 0)
        ans = dlg.ask_item(tool["title"], label, labels, index)
        if ans is None:
            return None
        value = vals[labels.index(ans)] if ans in labels else None
        if value is not None:
            return value, False
    note, text = "", "" if cur in (None, "same") else str(cur)
    while True:
        ans = dlg.ask_string(tool["title"], f"{note}{label}\n{NEW_LAYER[:-1]} as L/D (e.g. 3/0 or 3)", text)
        if ans is None:
            return None
        try:
            return coerce(p, ans), False
        except ValueError as exc:
            note, text = f"{exc}\n", ans


def ask_params(pya, tool: dict, names, current: dict, layers=None, summary: str = "",
               auto: dict | None = None) -> dict | None:
    """One native InputDialog per name, in order; None when the user cancels any of them.
    The fallback for a KLayout build without Qt (no ``pya.QDialog``); see ``ask_form``.

    With ``layers`` (a ``LayerLists``), a ``from`` layer param is a list of the live layers
    (one candidate: filled without a dialog, recorded in ``auto``). ``summary`` prefixes the
    first dialog actually shown."""
    out, dlg, names = {}, pya.InputDialog, list(names)
    head = f"{summary}\n\n" if summary else ""
    auto = {} if auto is None else auto
    for i, name in enumerate(names):
        p, cur = tool["params"][name], current.get(name)
        label = (p.get("title") or name) + (f" [{p['unit']}]" if p.get("unit") else "") \
            + (f"\n{p['doc']}" if p.get("doc") else "")
        if p["type"] == "layer" and p.get("from") and layers is not None:
            decided = {n: v for n, v in current.items() if n not in names[i:]}
            decided.update(out)
            picked = _pick_layer(dlg, tool, name, layers, decided, head + label, cur)
            if picked is None:
                return None
            out[name] = picked[0]
            if picked[1]:
                auto[name] = picked[0]
            else:
                head = ""
            continue
        note = ""
        while True:
            text = note + head + label
            if p["type"] in ("enum", "bool"):
                items = p["choices"] if p["type"] == "enum" else ["yes", "no"]
                pick = cur if p["type"] == "enum" else ("yes" if cur is not False else "no")
                ans = dlg.ask_item(tool["title"], text, items, items.index(pick) if pick in items else 0)
            elif p["type"] in ("int", "number"):  # *_ex: the variants that take min/max (0.22+)
                lo, hi = p.get("min"), p.get("max")
                if p["type"] == "int":
                    ans = dlg.ask_int_ex(tool["title"], text, int(cur or 0), -2**31 if lo is None
                                         else int(lo), 2**31 - 1 if hi is None else int(hi), 1)
                else:
                    ans = dlg.ask_double_ex(tool["title"], text, float(cur or 0.0), -1e15 if lo is None
                                            else float(lo), 1e15 if hi is None else float(hi), 6)
            else:
                ans = dlg.ask_string(tool["title"], text, "" if cur is None else str(cur))
            if ans is None:
                return None
            try:
                out[name] = coerce(p, ans if p["type"] != "bool" else ans == "yes")
                break
            except ValueError as exc:
                note = f"{exc}\n"
        head = ""
    return out


# ---------------------------------------------------------------- one-form dialog --
FORM_NAME = "klink_tool_form"  # objectName of the form dialog (live tests find it by this)


def _field_label(p: dict, name: str) -> str:
    return (p.get("title") or name) + (f" [{p['unit']}]" if p.get("unit") else "")


def form_fields(tool: dict, values: dict, layers) -> list[dict]:
    """Every param of ``tool`` (tool.json order) as one row of the form, pre-filled.

    ToolError(ERR_TOOL_INPUT) before any dialog when a ``from`` layer has nothing to offer
    (same rule as the non-interactive path). Each row is a dict with ``name``, ``kind``,
    ``label`` (title or name + `` [unit]``), ``doc`` and ``note`` (grey line under the widget,
    e.g. the empty-region fallback), plus per kind:

    - ``layer_list`` (a ``from`` layer) and ``enum``: ``options`` ``[(value, label)]`` and the
      preselected ``index``. A ``layer_list`` option whose value is None is ``new layer…``:
      ``new_index`` is its index (else None) and ``new_text`` the L/D proposed in its text field.
    - ``bool`` / ``int`` / ``number`` / ``string`` / ``layer`` (free L/D text, no ``from``):
      ``value``; ``int``/``number`` also ``min``/``max`` (the widget's range).

    A ``from`` layer lists ALL its candidates (``exclude`` is checked on OK, so every pick stays
    changeable). Without a value it preselects the first candidate not already taken by a param
    named in its ``exclude``: bool_region opens with A = the first candidate and B = the next
    one (the two selected operands' layers, or the two layers under a marker). A value not in the list is preselected as ``new layer…`` with that text
    (``allow_new``), else offered as an extra entry."""
    out, decided = [], dict(values)
    for name, p in tool["params"].items():
        cur, kind = values.get(name), p["type"]
        f = {"name": name, "label": _field_label(p, name), "doc": p.get("doc") or "", "note": ""}
        if kind == "layer" and p.get("from"):
            taken = {decided[n] for n in p.get("exclude", ()) if n in decided}
            free_cands, fell_back = layer_choices(tool, name, layers, decided)
            if not _entries(p, free_cands):
                raise _no_layer_error(tool, name)
            skip = p.get("skip_selection_layers", False)
            entries = _entries(p, layers.get("layout" if fell_back else p["from"], skip))
            vals = [v for v, _ in entries]
            new_index = vals.index(None) if None in vals else None
            new_text = ""
            if cur is not None and cur in vals:
                index = vals.index(cur)
            elif cur is not None and cur != "same" and new_index is not None:
                index, new_text = new_index, cur
            elif cur is not None:
                at = len(entries) if new_index is None else new_index
                entries.insert(at, (cur, cur))
                index, new_index = at, (None if new_index is None else new_index + 1)
            else:
                free = [i for i, v in enumerate(vals) if v is not None and v not in taken]
                index = free[0] if free else (new_index if new_index is not None else 0)
            picked = entries[index][0]
            if picked is not None or new_text:
                decided[name] = picked if picked is not None else new_text
            f.update(kind="layer_list", options=entries, index=index, new_index=new_index,
                     new_text=new_text,
                     note=f"{_NOTHING[p['from']][0]}; all layers of the layout" if fell_back else "")
        elif kind == "enum":
            ch = p["choices"]
            f.update(kind="enum", options=[(c, c) for c in ch], index=ch.index(cur) if cur in ch else 0)
        elif kind == "bool":
            f.update(kind="bool", value=cur is not False)
        elif kind in ("int", "number"):
            lo, hi = p.get("min"), p.get("max")
            if kind == "int":
                lo = -2**31 if lo is None else int(-(-lo // 1))   # ceil
                hi = 2**31 - 1 if hi is None else int(hi // 1)    # floor
                value = int(cur) if isinstance(cur, (int, float)) and not isinstance(cur, bool) else 0
            else:
                lo = -1e15 if lo is None else float(lo)
                hi = 1e15 if hi is None else float(hi)
                value = float(cur) if isinstance(cur, (int, float)) and not isinstance(cur, bool) else 0.0
            f.update(kind=kind, value=min(max(value, lo), hi), min=lo, max=hi)
        else:  # string, or a layer typed as L/D
            f.update(kind="layer" if kind == "layer" else "string", value="" if cur is None else str(cur))
        out.append(f)
    return out


def _initial_pick(f: dict):
    if f["kind"] in ("layer_list", "enum"):
        if f.get("new_index") is not None and f["index"] == f["new_index"]:
            return ("new", f["new_text"])
        return ("option", f["index"])
    return ("value", f["value"])


def form_collect(tool: dict, fields: list, picks: dict):
    """(values, error) from what the form's widgets hold; error is None or the text for the red line.

    ``picks`` maps a param name to ``("option", index)`` (a combo: index into the row's
    ``options``), ``("new", "L/D text")`` (``new layer…`` selected, its text field) or
    ``("value", v)`` (checkbox bool, spin box int/float, line edit str). A missing name keeps
    the row's preselection. Every value is ``coerce``d; then ``exclude`` is checked
    (``layer B must differ from layer A``). All problems are listed, one per line."""
    values, errors = {}, []
    for f in fields:
        name = f["name"]
        p = tool["params"][name]
        how, raw = picks.get(name) or _initial_pick(f)
        if how == "option":
            raw = f["options"][raw][0]
            if raw is None:
                how, raw = "new", ""
        try:
            values[name] = coerce(p, raw)
        except ValueError as exc:
            errors.append(f"{f['label']}: " + (f"{NEW_LAYER} " if how == "new" else "") + str(exc))
    params = tool["params"]
    for name, p in params.items():
        for other in p.get("exclude", ()):
            if name in values and other in values and values[name] == values[other]:
                errors.append(f"{params[name].get('title') or name} must differ from "
                              f"{params[other].get('title') or other}")
    return values, ("\n".join(errors) or None)


def _get(obj, attr):
    """A Qt getter in KLayout's binding is a property (``combo.currentIndex``); a method elsewhere."""
    v = getattr(obj, attr)
    return v() if callable(v) else v


def _form_row(pya, dlg, form, f: dict, keep: list):
    """Adds one row to the QFormLayout; returns the reader () -> pick for ``form_collect``."""
    name, kind = f["name"], f["kind"]
    if kind in ("layer_list", "enum"):
        combo = pya.QComboBox(dlg)
        combo.setObjectName(f"param_{name}")
        for _, text in f["options"]:
            combo.addItem(text)
        combo.setCurrentIndex(f["index"])
        widget, ni = combo, f.get("new_index")
        reader = (lambda: ("option", _get(combo, "currentIndex")))
        if ni is not None:
            edit = pya.QLineEdit(dlg)
            edit.setObjectName(f"new_{name}")
            edit.setText(f["new_text"])
            edit.setPlaceholderText("L/D, e.g. 3/0")
            edit.setEnabled(f["index"] == ni)

            def toggled(i, edit=edit, ni=ni):
                edit.setEnabled(i == ni)
            combo.currentIndexChanged = toggled
            keep.append(toggled)
            widget = pya.QWidget(dlg)
            row = pya.QHBoxLayout(widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(combo, 1)
            row.addWidget(edit)
            keep.extend((combo, edit, row))

            def reader(combo=combo, edit=edit, ni=ni):
                i = _get(combo, "currentIndex")
                return ("new", _get(edit, "text")) if i == ni else ("option", i)
    elif kind == "bool":
        widget = pya.QCheckBox(dlg)
        widget.setChecked(bool(f["value"]))
        reader = (lambda w=widget: ("value", bool(_get(w, "isChecked"))))
    elif kind in ("int", "number"):
        widget = pya.QSpinBox(dlg) if kind == "int" else pya.QDoubleSpinBox(dlg)
        if kind == "number":
            widget.setDecimals(6)
        widget.setRange(f["min"], f["max"])
        widget.setValue(f["value"])
        reader = (lambda w=widget: ("value", _get(w, "value")))
    else:
        widget = pya.QLineEdit(dlg)
        widget.setText(f["value"])
        if kind == "layer":
            widget.setPlaceholderText("L/D, e.g. 1/0")
        reader = (lambda w=widget: ("value", _get(w, "text")))
    if kind not in ("layer_list", "enum"):
        widget.setObjectName(f"param_{name}")
    label =pya.QLabel(f["label"], dlg)
    grey = "\n".join(t for t in (f.get("doc"), f.get("note")) if t)
    if f.get("doc"):
        label.setToolTip(f["doc"])
        widget.setToolTip(f["doc"])
    cell = widget
    if grey:
        cell = pya.QWidget(dlg)
        col = pya.QVBoxLayout(cell)
        col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(widget)
        small = pya.QLabel(grey, dlg)
        small.setStyleSheet("color: gray; font-size: small")
        small.setWordWrap(True)
        col.addWidget(small)
        keep.extend((col, small))
    form.addRow(label, cell)
    keep.extend((widget, label, cell, reader))
    return reader


def ask_form(pya, tool: dict, fields: list, summary: str = ""):
    """ONE modal QDialog with a row per field (``form_fields``), like KLayout's Boolean dialog.

    Returns the picks for ``form_collect``, or None on cancel. OK runs ``form_collect`` first
    and keeps the dialog open with the problem in red until the input is valid."""
    app = pya.Application.instance()
    dlg = pya.QDialog(app.main_window() if app is not None else None)
    keep, state = [], {"ok": False}
    try:
        dlg.setObjectName(FORM_NAME)
        dlg.setWindowTitle(tool["title"])
        box = pya.QVBoxLayout(dlg)
        if summary:
            head = pya.QLabel(summary, dlg)
            box.addWidget(head)
            keep.append(head)
        form = pya.QFormLayout()
        box.addLayout(form)
        readers = {f["name"]: _form_row(pya, dlg, form, f, keep) for f in fields}
        err = pya.QLabel("", dlg)
        err.setObjectName("form_error")
        err.setStyleSheet("color: #c00000")
        err.setWordWrap(True)
        err.setVisible(False)
        box.addWidget(err)
        buttons = pya.QDialogButtonBox(dlg)
        buttons.setStandardButtons(pya.QDialogButtonBox.Ok | pya.QDialogButtonBox.Cancel)
        box.addWidget(buttons)

        def picks():
            return {n: r() for n, r in readers.items()}

        def on_ok():
            problem = form_collect(tool, fields, picks())[1]
            if problem:
                err.setText(problem)
                err.setVisible(True)
            else:
                dlg.accept()

        def on_cancel():
            dlg.reject()

        def on_accepted():
            state["ok"] = True
        buttons.accepted = on_ok
        buttons.rejected = on_cancel
        dlg.accepted = on_accepted
        keep.extend((box, form, err, buttons, on_ok, on_cancel, on_accepted))
        run = getattr(dlg, "exec_", None) or getattr(dlg, "exec")
        res = run()
        try:
            res = int(res)
        except Exception:
            res = 0
        return picks() if state["ok"] or res == 1 else None
    finally:
        try:
            dlg._destroy()
        except Exception as exc:
            _log.error("tools: form dialog cleanup failed: %s", exc)


def needs_form(pya, interactive: bool, ask) -> bool:
    """One form instead of the per-param InputDialogs: interactive, something to ask, Qt present."""
    return bool(interactive and ask) and hasattr(pya, "QDialog")


def auto_layers(tool: dict, values: dict, layers, auto: dict) -> None:
    """Fill every ``from`` layer param that still has no value with its single candidate;
    ERR_TOOL_PARAMS listing the candidates when there is a choice to make (non-interactive)."""
    pending = {}
    for name, p in tool["params"].items():
        if p["type"] != "layer" or not p.get("from") or name in values:
            continue
        cands, fell_back = layer_choices(tool, name, layers, values)
        entries = _entries(p, cands)
        if not entries:
            raise _no_layer_error(tool, name)
        if len(entries) == 1 and entries[0][0] is not None:
            values[name] = auto[name] = entries[0][0]
        else:
            pending[name] = ([v for v, _ in entries if v is not None], fell_back, bool(p.get("allow_new")))
    if not pending:
        return
    parts = []
    for name, (vals, fell_back, new) in pending.items():
        what = ("one of " + ", ".join(vals)) if vals else "a layer"
        if fell_back:
            what += f" ({_NOTHING[tool['params'][name]['from']][0]}; these are the layout's layers)"
        if new:
            what += " or any new L/D"
        parts.append(f"{name}: {what}")
    first = next(iter(pending))
    example = (pending[first][0] or ["1/0"])[0]
    raise ToolError("ERR_TOOL_PARAMS", f"tool {tool['id']!r}: pick a layer for " + ", ".join(pending),
                    hint="; ".join(parts) + f". Pass it as a param, e.g. params={{\"{first}\": \"{example}\"}} "
                    f"(CLI: --param {first}={example}), or run with interactive=true to pick from a list",
                    data={"candidates": {n: v[0] for n, v in pending.items()}})


# ------------------------------------------------------------------------ running --
def _pya():
    import pya
    return pya


def _txn(view, title: str):
    from .txn import auto_txn
    return auto_txn(view, title)


def journal_path(session_id: str) -> Path:
    return default_registry_root() / "journals" / f"{session_id}.tools.jsonl"


def journal_append(session_id: str, record: dict) -> int | None:
    """Append one run record (monotonic seq, same rotation as buttons); None if it could not be written."""
    from .buttons import _MAX_JOURNAL_BYTES, _last_seq
    try:
        path = journal_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        seq = _last_seq(path) + 1
        if path.exists() and path.stat().st_size > _MAX_JOURNAL_BYTES:
            path.replace(path.with_suffix(".jsonl.1"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"seq": seq, **record}, ensure_ascii=False, default=str) + "\n")
        return seq
    except Exception as exc:
        _log.error("tools journal write failed: %s", exc)
        return None


def _load_run(tool: dict, pya):
    """``run(ctx, params)`` for either form of run.py; ToolError(ERR_TOOL_RUN) when it cannot load.

    Form (a): a top-level ``def run`` -> the module is executed now (``__name__`` =
    ``klink_tool_<id>``) and its ``run`` returned. Form (b), a plain macro: only compiled now;
    the returned callable executes the whole source as ``__main__`` (inside the caller's
    transaction), with ``pya``, ``ctx`` and ``params`` as globals.
    """
    path = Path(tool["run_path"])
    fail = f"run.py of tool {tool['id']!r} failed to load"
    try:
        src = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(src, str(path))
        code = compile(tree, str(path), "exec")
    except Exception as exc:
        raise ToolError("ERR_TOOL_RUN", f"{fail} ({path}): {type(exc).__name__}: {exc}",
                        hint=f"fix {path} and run the tool again") from exc
    if not any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body):
        def run_macro(ctx, params):
            ns = {"__name__": "__main__", "__file__": str(path), "pya": pya, "ctx": ctx, "params": params}
            try:
                exec(code, ns)
            except SystemExit as exc:  # sys.exit() in a macro: 0/None is a normal end
                if exc.code not in (None, 0):
                    raise RuntimeError(f"the macro called sys.exit({exc.code!r})") from None
            return None
        return run_macro
    ns = {"__name__": f"klink_tool_{tool['id']}", "__file__": str(path), "pya": pya}
    try:
        exec(code, ns)
    except Exception as exc:
        raise ToolError("ERR_TOOL_RUN", f"{fail}: {type(exc).__name__}: {exc}",
                        hint=f"fix {path} and run the tool again") from exc
    if not callable(ns.get("run")):
        raise ToolError("ERR_TOOL_RUN", f"run.py of tool {tool['id']!r} defines no run(ctx, params)",
                        hint=f"add `def run(ctx, params): ...` to {path}")
    return ns["run"]


class _Tee(io.TextIOBase):
    """Captures what the tool prints and still forwards it to the KLayout console."""

    def __init__(self, other):
        self.buf, self.other = io.StringIO(), other

    def writable(self):
        return True

    def write(self, text):
        self.buf.write(text)
        try:
            if self.other is not None:
                self.other.write(text)
        except Exception:
            pass
        return len(text)

    def flush(self):
        try:
            if self.other is not None:
                self.other.flush()
        except Exception:
            pass


def _status_message(pya, text: str) -> None:
    """One line in KLayout's status bar; never fails the run."""
    try:
        pya.Application.instance().main_window().message(text, 4000)
    except Exception as exc:
        _log.error("tools: status bar message failed: %s", exc)


def run_tool(tool_id: str, preset=None, params=None, interactive: bool = False, root=None) -> dict:
    """Load, resolve, check inputs, ask, run inside one undo transaction, journal. ToolError on failure."""
    from .buttons import _session_id
    pya, t0, session = _pya(), time.time(), _session_id()
    rec = {"tool": tool_id, "preset": preset, "interactive": bool(interactive), "session_id": session}
    auto = {}  # from-layer params filled without asking (one candidate; never from the form)

    def done(status: str, **extra) -> dict:
        if auto:
            rec["auto"] = auto
        rec.update(status=status, wall_ms=round((time.time() - t0) * 1000.0, 1), ts=time.time(), **extra)
        return {**rec, "seq": journal_append(session, rec)}

    body, tee = [], _Tee(None)
    try:
        tool = load_tool(tool_id, root)
        values, ask = resolve_params(tool, preset, params, interactive)
        rec["params"] = values
        app = pya.Application.instance()
        view = app.main_window().current_view() if app is not None and app.main_window() else None
        cv = view.active_cellview() if view is not None else None
        if cv is None or not cv.is_valid() or cv.cell is None:
            raise ToolError("ERR_TOOL_INPUT", "no layout with a current cell is open",
                            hint="open a layout, show the cell to work on, and run the tool again")
        selected = list(view.each_object_selected())
        shapes = [o for o in selected if not o.is_cell_inst()]
        rec["selection_count"], rec["selected_count"] = len(shapes), len(selected)
        if len(selected) < tool["min_selected"]:
            msg = (f"tool {tool['title']!r} needs at least {tool['min_selected']} selected object(s); "
                   f"{len(selected)} selected")
            raise ToolError("ERR_TOOL_INPUT", msg, hint=f"select at least {tool['min_selected']} shapes "
                            "or instances in the current view and run it again")
        if len(shapes) < tool["min_shapes"]:
            msg = f"tool {tool['title']!r} needs at least {tool['min_shapes']} selected shape(s); {len(shapes)} selected"
            raise ToolError("ERR_TOOL_INPUT", msg, hint=f"select at least {tool['min_shapes']} shapes "
                            "(instances do not count) in the current view and run it again")
        ctx = SimpleNamespace(pya=pya, view=view, cellview=cv, layout=cv.layout(), cell=cv.cell,
                              dbu=cv.layout().dbu, selection=shapes, selected=selected,
                              tool_dir=Path(tool["dir"]), log=get_logger(f"tool.{tool_id}"))
        def _layers(scope="region", skip_selection_layers=None):
            return layer_candidates(ctx, scope, skip_selection_layers)
        ctx.layers = _layers
        layers = LayerLists(ctx)  # computed at click time, once per scope
        if needs_form(pya, interactive, ask):  # ONE form with every param
            fields = form_fields(tool, values, layers)
            picks = ask_form(pya, tool, fields, selection_summary(ctx.layout, selected))
            if picks is None:
                return done("cancelled")
            answers, problem = form_collect(tool, fields, picks)
            if problem:
                raise ToolError("ERR_TOOL_PARAMS", problem.replace("\n", "; "),
                                hint="run the tool again and fix these fields")
            values.update(answers)
        elif interactive and ask:  # no Qt in this build: one InputDialog per param
            answers = ask_params(pya, tool, ask, values, layers,
                                 selection_summary(ctx.layout, selected), auto)
            if answers is None:
                return done("cancelled")
            values.update(answers)
        auto_layers(tool, values, layers, auto)
        run = _load_run(tool, pya)
        tee = _Tee(sys.stdout)
        with _txn(view, f"klink tool: {tool['title']}"):
            try:
                with contextlib.redirect_stdout(tee):
                    result = run(ctx, dict(values))
                result = {} if result is None else result
                if not isinstance(result, dict):
                    raise TypeError(f"run() must return a dict, got {type(result).__name__}")
                printed = tee.buf.getvalue().rstrip("\n")
                if printed and "stdout" not in result:
                    result = {**result, "stdout": printed}
                json.dumps(result)
                # New layers need their layer views added INSIDE the transaction: auto_txn does it
                # after commit, which (verified 0.30.7) wipes the whole undo stack for a new layer.
                getattr(view, "add_missing_layers", lambda: None)()
            except BaseException as exc:
                body.append(exc)
                raise
        out = done("ok", result=result)
        if interactive:
            lines = [ln.strip() for ln in tee.buf.getvalue().splitlines() if ln.strip()]
            _status_message(pya, lines[-1] if lines else f"{tool['title']}: done")
        return out
    except Exception as exc:
        err = exc
        if body:  # run.py raised (or returned garbage): auto_txn rolled the edit back
            orig = body[0]
            txn = (getattr(exc, "data", None) or {}).get("transaction") if isinstance(exc, RpcError) else None
            tb = "".join(traceback.format_exception(type(orig), orig, orig.__traceback__)[-6:])
            _log.error("tool %s failed:\n%s", tool_id, tb)
            msg = str(orig) if isinstance(orig, ValueError) else f"{type(orig).__name__}: {orig}"
            state = (txn or {}).get("partial_edit", "unknown")
            first = "nothing was changed" if state in ("none", "undone") else (
                f"the edit may NOT have been rolled back (partial_edit={state}); check edit.status")
            err = ToolError("ERR_TOOL_RUN", msg, hint=f"{first}; fix the selection/params, or the tool's "
                            f"run.py at {tool['run_path']}, and run it again",
                            data={"transaction": txn, "traceback": tb})
            printed = tee.buf.getvalue().rstrip("\n")
            if printed:
                err.data["stdout"] = rec["stdout"] = printed
        elif isinstance(exc, RpcError) and not isinstance(exc, ToolError):
            err = ToolError(exc.code, exc.message, hint=exc.hint, data=exc.data)
        elif not isinstance(exc, RpcError):
            err = ToolError("ERR_TOOL_RUN", f"{type(exc).__name__}: {exc}",
                            hint="see the KLayout console / klink_server.log for the stack trace")
        done("error", error={"code": err.code, "type": type(body[0] if body else exc).__name__,
                             "message": (err.message.splitlines() or [""])[0], "hint": err.hint})
        if interactive and err.code == "ERR_TOOL_INPUT":
            try:
                pya.MessageBox.info(tool_id, f"{err.message}\n\n{err.hint}", pya.MessageBox.Ok)
            except Exception as box_exc:
                _log.error("tool %s: info dialog failed: %s", tool_id, box_exc)
        if err is exc:
            raise
        raise err from exc

"""
Layer DISPLAY methods (view-side; data-side layer.* lives in layer_m).

`layer.display_list` : enumerate view layer entries (visibility + style)
`layer.set_visible`  : show/hide layers; `exclusive` = show ONLY these
`layer.set_style`    : set a layer's colors / dither pattern / line width
`layer.load_lyp`     : load a .lyp layer-properties file into the view
`layer.save_lyp`     : save the view's layer properties to a .lyp file

Rationale
---------
Agents could draw geometry but not control what the USER sees: debugging
"only show 1/0 and 3/0" or styling a scratch layer needed manual GUI
clicks. These wrap the official LayerPropertiesNode surface. Probe-verified:
`view.each_layer()` yields mutable references — in-place `visible` /
`fill_color` writes take effect immediately. None of this touches layout
DATA (pure view state; no undo interaction).

Colors are '#RRGGBB' at the RPC face; KLayout stores 0xAARRGGBB
internally, the alpha byte is preserved/forced opaque on write.
"""

from __future__ import annotations

import os

import pya

from ..registry import method
from ..errors import RpcError, ErrorCode


def _view():
    mw = pya.Application.instance().main_window()
    view = mw.current_view() if mw is not None else None
    if view is None:
        raise RpcError(ErrorCode.NO_VIEW, "no layout view is open")
    return view


def _parse_ld(spec):
    """'L/D' string or {layer, datatype} -> (int, int)."""
    if isinstance(spec, str):
        parts = spec.split("/")
        if len(parts) == 2:
            try:
                return int(parts[0]), int(parts[1])
            except ValueError:
                pass
        raise RpcError(ErrorCode.BAD_PARAMS,
                       "layer %r is not 'L/D' (e.g. '1/0')" % (spec,))
    if isinstance(spec, dict) and "layer" in spec:
        return int(spec["layer"]), int(spec.get("datatype", 0))
    raise RpcError(ErrorCode.BAD_PARAMS,
                   "layer entry %r must be 'L/D' or {layer, datatype}" % (spec,))


def _hex_color(value) -> str:
    return "#%06X" % (int(value) & 0xFFFFFF)


def _parse_color(value) -> int:
    s = str(value).strip().lstrip("#")
    if s.lower().startswith("0x"):
        s = s[2:]
    try:
        return (int(s, 16) & 0xFFFFFF) | 0xFF000000  # force opaque
    except ValueError:
        raise RpcError(ErrorCode.BAD_PARAMS,
                       "color %r is not '#RRGGBB' hex" % (value,))


def _iter_nodes(view):
    """Yield (node, (layer, datatype)) for concrete source layers."""
    for node in view.each_layer():
        try:
            ld = (int(node.source_layer), int(node.source_datatype))
        except Exception:
            continue
        yield node, ld


def _available(view) -> list:
    return sorted({"%d/%d" % ld for _n, ld in _iter_nodes(view)})


@method(
    "layer.display_list",
    description=(
        "List the current view's layer DISPLAY entries: layer/datatype, "
        "`visible`, `fill_color`/`frame_color` ('#RRGGBB'), "
        "`dither_pattern` index and `name`. This is the view-side "
        "counterpart of layer.list (which lists data layers)."
    ),
    params_schema={"type": "object", "additionalProperties": False},
    returns_schema={
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "layers": {"type": "array", "items": {"type": "object"}},
        },
    },
    tags=["layer", "view", "read"],
)
def layer_display_list(params, ctx):
    view = _view()
    out = []
    for node, (l, d) in _iter_nodes(view):
        entry = {"layer": l, "datatype": d, "visible": bool(node.visible)}
        try:
            entry["fill_color"] = _hex_color(node.eff_fill_color(True))
            entry["frame_color"] = _hex_color(node.eff_frame_color(True))
            entry["dither_pattern"] = int(node.eff_dither_pattern(True))
        except Exception:
            pass
        try:
            if node.name:
                entry["name"] = str(node.name)
        except Exception:
            pass
        out.append(entry)
    return {"count": len(out), "layers": out}


@method(
    "layer.set_visible",
    description=(
        "Show or hide layers in the current view (display only — layout "
        "data untouched, no undo involved). `layers` = ['L/D', ...]; "
        "`visible` (default true); `exclusive: true` = show ONLY the "
        "listed layers and hide every other one (the 'let me see just "
        "1/0 and 3/0' debugging call). Unknown layers are reported, not "
        "silently ignored."
    ),
    params_schema={
        "type": "object",
        "required": ["layers"],
        "properties": {
            "layers": {"type": "array", "minItems": 1,
                       "description": "['L/D', ...] or [{layer, datatype}]"},
            "visible": {"type": "boolean", "default": True},
            "exclusive": {"type": "boolean", "default": False,
                          "description": "hide all layers NOT listed"},
        },
        "additionalProperties": False,
    },
    returns_schema={
        "type": "object",
        "properties": {
            "changed": {"type": "integer"},
            "visible_layers": {"type": "array", "items": {"type": "string"}},
        },
    },
    tags=["layer", "view"],
)
def layer_set_visible(params, ctx):
    view = _view()
    wanted = {_parse_ld(s) for s in params["layers"]}
    visible = bool(params.get("visible", True))
    exclusive = bool(params.get("exclusive", False))

    seen = set()
    changed = 0
    for node, ld in _iter_nodes(view):
        if ld in wanted:
            seen.add(ld)
            target = visible
        elif exclusive:
            target = False
        else:
            continue
        if bool(node.visible) != target:
            node.visible = target
            changed += 1

    missing = wanted - seen
    if missing:
        raise RpcError(
            ErrorCode.NOT_FOUND,
            "layers not in this view's display list: %s. Present: %s "
            "(layer.display_list for details; layer.ensure creates data "
            "layers but a display entry appears once the layer exists in "
            "the layout)." % (sorted("%d/%d" % m for m in missing),
                              _available(view)),
        )
    return {
        "changed": changed,
        "visible_layers": sorted(
            "%d/%d" % ld for node, ld in _iter_nodes(view) if node.visible),
    }


@method(
    "layer.set_style",
    description=(
        "Style one layer's display in the current view: `color` sets fill "
        "AND frame ('#RRGGBB'); or set `fill_color` / `frame_color` "
        "separately; `dither_pattern` (KLayout stipple index, 0..) and "
        "`line_width` optionally. Display only — layout data untouched."
    ),
    params_schema={
        "type": "object",
        "required": ["layer"],
        "properties": {
            "layer": {"description": "'L/D' or {layer, datatype}"},
            "color": {"description": "'#RRGGBB' for both fill and frame"},
            "fill_color": {"description": "'#RRGGBB'"},
            "frame_color": {"description": "'#RRGGBB'"},
            "dither_pattern": {"type": "integer", "minimum": 0},
            "line_width": {"type": "integer", "minimum": 0},
        },
        "additionalProperties": False,
    },
    returns_schema={
        "type": "object",
        "properties": {
            "layer": {"type": "string"},
            "applied": {"type": "object"},
        },
    },
    tags=["layer", "view"],
)
def layer_set_style(params, ctx):
    view = _view()
    ld = _parse_ld(params["layer"])
    applied = {}
    found = False
    for node, node_ld in _iter_nodes(view):
        if node_ld != ld:
            continue
        found = True
        if params.get("color") is not None:
            c = _parse_color(params["color"])
            node.fill_color = c
            node.frame_color = c
            applied["color"] = _hex_color(c)
        if params.get("fill_color") is not None:
            node.fill_color = _parse_color(params["fill_color"])
            applied["fill_color"] = _hex_color(node.fill_color)
        if params.get("frame_color") is not None:
            node.frame_color = _parse_color(params["frame_color"])
            applied["frame_color"] = _hex_color(node.frame_color)
        if params.get("dither_pattern") is not None:
            node.dither_pattern = int(params["dither_pattern"])
            applied["dither_pattern"] = int(params["dither_pattern"])
        if params.get("line_width") is not None:
            node.width = int(params["line_width"])
            applied["line_width"] = int(params["line_width"])
    if not found:
        raise RpcError(
            ErrorCode.NOT_FOUND,
            "layer %d/%d has no display entry in this view. Present: %s"
            % (ld[0], ld[1], _available(view)),
        )
    if not applied:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "nothing to apply — pass color / fill_color / frame_color / "
            "dither_pattern / line_width")
    return {"layer": "%d/%d" % ld, "applied": applied}


@method(
    "layer.load_lyp",
    description=(
        "Load a KLayout .lyp layer-properties file into the current view "
        "(colors/stipples/visibility for the whole stack in one call)."
    ),
    params_schema={
        "type": "object",
        "required": ["path"],
        "properties": {"path": {"type": "string"}},
        "additionalProperties": False,
    },
    returns_schema={"type": "object",
                    "properties": {"path": {"type": "string"},
                                   "count": {"type": "integer"}}},
    tags=["layer", "view"],
)
def layer_load_lyp(params, ctx):
    view = _view()
    path = str(params.get("path") or "")
    if not path or not os.path.isfile(path):
        raise RpcError(ErrorCode.BAD_PARAMS,
                       "path %r does not exist; pass an absolute path to a "
                       ".lyp file" % (path,))
    try:
        view.load_layer_props(path)
    except Exception as exc:
        raise RpcError(ErrorCode.BAD_PARAMS,
                       "KLayout could not load %r as .lyp: %s" % (path, exc))
    return {"path": path, "count": sum(1 for _ in _iter_nodes(view))}


@method(
    "layer.save_lyp",
    description=(
        "Save the current view's layer properties (colors/stipples/"
        "visibility) to a KLayout .lyp file."
    ),
    params_schema={
        "type": "object",
        "required": ["path"],
        "properties": {"path": {"type": "string"}},
        "additionalProperties": False,
    },
    returns_schema={"type": "object",
                    "properties": {"path": {"type": "string"}}},
    tags=["layer", "view"],
)
def layer_save_lyp(params, ctx):
    view = _view()
    path = str(params.get("path") or "")
    if not path:
        raise RpcError(ErrorCode.BAD_PARAMS, "path is required")
    try:
        view.save_layer_props(path)
    except Exception as exc:
        raise RpcError(ErrorCode.INTERNAL,
                       "could not save .lyp to %r: %s" % (path, exc))
    return {"path": path}

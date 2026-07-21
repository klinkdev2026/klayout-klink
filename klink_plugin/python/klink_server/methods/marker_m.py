"""
Highlight-marker methods.

`view.highlight`       : draw transient highlight markers (boxes/polygons)
`view.highlight_clear` : remove all klink highlight markers

Rationale
---------
"Look HERE" from agent to user. pya.Marker objects are view-layer
overlays: they touch neither the layout nor the selection nor the undo
stack, so an agent can point at geometry ("the short is in this box")
without clobbering the user's selection (selection.set_box) or polluting
the layout with throwaway shapes. Together with the SEND toolbar action
this closes the two-way pointing loop: the user SENDs a region to the
agent, the agent highlights its answer back.

Lifetime (probe-verified): a Marker vanishes as soon as its Python object
is garbage-collected, so this module keeps strong references and owns the
cleanup. Optional `expire_s` auto-clears a batch via QTimer.
"""

from __future__ import annotations

import pya

from ..registry import method
from ..errors import RpcError, ErrorCode

_MAX_MARKERS = 500

# batches of {"markers": [pya.Marker...], "timer": QTimer|None}
_BATCHES: list = []


def _parse_color(value, default=0xFF3030) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value & 0xFFFFFF
    s = str(value).strip().lstrip("#")
    if s.lower().startswith("0x"):
        s = s[2:]
    try:
        return int(s, 16) & 0xFFFFFF
    except ValueError:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "color %r is not '#RRGGBB' hex (e.g. '#FF3030')" % (value,))


def _clear_all() -> int:
    n = 0
    while _BATCHES:
        batch = _BATCHES.pop()
        timer = batch.get("timer")
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        for m in batch["markers"]:
            try:
                m.destroy()
                n += 1
            except Exception:
                pass
    return n


def _expire_batch(batch) -> None:
    try:
        _BATCHES.remove(batch)
    except ValueError:
        return  # already cleared
    for m in batch["markers"]:
        try:
            m.destroy()
        except Exception:
            pass


def _active_count() -> int:
    return sum(len(b["markers"]) for b in _BATCHES)


@method(
    "view.highlight",
    description=(
        "Draw transient highlight markers on the current view to point the "
        "user at locations — view-layer overlays that do NOT touch the "
        "layout, the selection, or undo. Pass `boxes_um` and/or "
        "`polygons_um` (microns, top-cell coordinates). Style: `color` "
        "('#RRGGBB', default red), `line_width` px, `halo`. By default "
        "REPLACES previous highlights (`clear: false` accumulates, e.g. "
        "different colors per category). `expire_s` auto-removes this "
        "batch after N seconds. Use this instead of selection.set_box "
        "when you only want to POINT — set_box clobbers the user's real "
        "selection. view.highlight_clear removes everything."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "boxes_um": {
                "type": "array",
                "items": {"type": "array", "minItems": 4, "maxItems": 4},
                "description": "Highlight boxes [l, b, r, t] in microns.",
            },
            "polygons_um": {
                "type": "array",
                "items": {"type": "array",
                          "items": {"type": "array", "minItems": 2, "maxItems": 2}},
                "description": "Highlight polygons as [[x, y], ...] in microns.",
            },
            "color": {"description": "'#RRGGBB' (default '#FF3030' red)"},
            "line_width": {"type": "integer", "minimum": 1, "default": 2},
            "halo": {"type": "boolean", "default": True,
                     "description": "contrast halo around the lines"},
            "expire_s": {"type": "number", "exclusiveMinimum": 0,
                         "description": "auto-remove this batch after N seconds"},
            "clear": {"type": "boolean", "default": True,
                      "description": "remove previous highlights first"},
        },
        "additionalProperties": False,
    },
    returns_schema={
        "type": "object",
        "properties": {
            "added": {"type": "integer"},
            "active": {"type": "integer"},
            "cleared": {"type": "integer"},
        },
    },
    mutates=False,
    tags=["view"],
)
def view_highlight(params, ctx):
    mw = pya.Application.instance().main_window()
    view = mw.current_view() if mw is not None else None
    if view is None:
        raise RpcError(ErrorCode.NO_VIEW, "no layout view is open")

    boxes = params.get("boxes_um") or []
    polys = params.get("polygons_um") or []
    if not boxes and not polys:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "pass at least one of boxes_um / polygons_um. To point at a "
            "stored selection, read its bbox from interaction.selection.* "
            "first and pass it as boxes_um.")
    if len(boxes) + len(polys) > _MAX_MARKERS:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "too many markers (%d > %d); highlight the aggregate bbox "
            "instead of every element" % (len(boxes) + len(polys), _MAX_MARKERS))

    color = _parse_color(params.get("color"))
    line_width = int(params.get("line_width") or 2)
    halo = params.get("halo")

    cleared = _clear_all() if params.get("clear", True) else 0

    markers = []
    try:
        for bb in boxes:
            l, b, r, t = (float(v) for v in bb)
            m = pya.Marker(view)
            m.set(pya.DBox(l, b, r, t))
            markers.append(m)
        for pts in polys:
            m = pya.Marker(view)
            m.set(pya.DPolygon([pya.DPoint(float(x), float(y)) for x, y in pts]))
            markers.append(m)
    except RpcError:
        raise
    except Exception as exc:
        for m in markers:
            try:
                m.destroy()
            except Exception:
                pass
        raise RpcError(ErrorCode.BAD_PARAMS, "bad highlight geometry: %s" % (exc,))

    for m in markers:
        try:
            m.color = color
            m.line_width = line_width
            m.vertex_size = 0
            # official semantics: halo -1 default / 0 off / 1 on;
            # dismissable=True lets the user hide highlights via the
            # View > Show Markers menu.
            m.dismissable = True
            if halo is not None:
                m.halo = 1 if halo else 0
        except Exception:
            pass

    batch = {"markers": markers, "timer": None}
    expire_s = params.get("expire_s")
    if expire_s:
        try:
            timer = pya.QTimer()
            timer.setSingleShot(True)
            timer.timeout += lambda *a, _b=batch: _expire_batch(_b)
            timer.start(int(float(expire_s) * 1000))
            batch["timer"] = timer
        except Exception:
            pass  # no Qt timer -> highlights stay until cleared
    _BATCHES.append(batch)

    return {"added": len(markers), "active": _active_count(), "cleared": cleared}


@method(
    "view.highlight_clear",
    description=(
        "Remove ALL klink highlight markers (from view.highlight) "
        "immediately. Never touches the layout, selection, or undo."
    ),
    params_schema={"type": "object", "additionalProperties": False},
    returns_schema={
        "type": "object",
        "properties": {"cleared": {"type": "integer"}},
    },
    mutates=False,
    tags=["view"],
)
def view_highlight_clear(params, ctx):
    return {"cleared": _clear_all()}

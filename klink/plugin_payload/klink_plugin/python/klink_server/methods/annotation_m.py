"""
Annotation (ruler) methods — the VIEW-side mechanism, nothing more.

`annotation.list`    : read the rulers the user drew (or klink drew)
`annotation.get`     : one ruler by id
`annotation.insert`  : draw a ruler (agent -> user: "I mean THIS line")
`annotation.update`  : change an existing ruler in place
`annotation.delete`  : remove by id or by category
`annotation.clear`   : remove all (destructive, needs confirm)
`annotation.measure` : auto-measure ruler at a seed point

Why this has to live in the plugin
----------------------------------
Rulers are `pya.Annotation` objects attached to a `pya.LayoutView`. They
are NOT part of the Layout: they do not survive a GDS write and the
standalone `klayout.db` module cannot see them at all. Only code running
inside the KLayout application can reach `main_window().current_view()`,
so the raw access has to be here. Nothing else does.

THIN BY CONTRACT
----------------
This module serialises what the view holds and applies what it is told.
It owns exactly two things that genuinely require pya: the constant
name<->value tables (built with getattr, because the constants arrived
over several KLayout releases) and the property get/set calls.

Policy lives on the klink side in `klink/annotation.py`: which ruler is a
cross-section cut line, what to do with a multi-segment ruler, segment
lengths, defaults, and the derivation of a cut from a ruler. Do not grow
this file with decisions — grow that one.

Multi-segment rulers (KLayout 0.28+)
------------------------------------
A ruler can have more than two points. `p1`/`p2` are kept for backward
compatibility and return only the FIRST and LAST point, so reading them
off a 4-point ruler silently invents a line the user never drew. This
module therefore always reports the full `points_um` list plus the
segment count, and never exposes a bare p1/p2 pair.
"""

from __future__ import annotations

import pya

from ..errors import ErrorCode, RpcError
from ..registry import method
from ..txn import auto_txn


_MAX_POINTS = 1000


def _const_table(pairs):
    """token -> int for the constants THIS KLayout build defines.

    StyleCross* arrived in 0.26, Outline{Angle,Radius} in 0.28,
    AngleDiagonalOnly in 0.30.6 — a missing constant is a version fact,
    not an error, so the token simply is not offered.
    """
    table = {}
    for token, attr in pairs:
        value = getattr(pya.Annotation, attr, None)
        if value is not None:
            try:
                table[token] = int(value)
            except Exception:
                pass
    return table


_STYLES = _const_table([
    ("ruler", "StyleRuler"),
    ("arrow_end", "StyleArrowEnd"),
    ("arrow_start", "StyleArrowStart"),
    ("arrow_both", "StyleArrowBoth"),
    ("line", "StyleLine"),
    ("cross_start", "StyleCrossStart"),
    ("cross_end", "StyleCrossEnd"),
    ("cross_both", "StyleCrossBoth"),
])
_OUTLINES = _const_table([
    ("diag", "OutlineDiag"),
    ("xy", "OutlineXY"),
    ("yx", "OutlineYX"),
    ("diag_xy", "OutlineDiagXY"),
    ("diag_yx", "OutlineDiagYX"),
    ("box", "OutlineBox"),
    ("ellipse", "OutlineEllipse"),
    ("angle", "OutlineAngle"),
    ("radius", "OutlineRadius"),
])
_ANGLES = _const_table([
    ("any", "AngleAny"),
    ("global", "AngleGlobal"),
    ("horizontal", "AngleHorizontal"),
    ("vertical", "AngleVertical"),
    ("ortho", "AngleOrtho"),
    ("diagonal", "AngleDiagonal"),
    ("diagonal_only", "AngleDiagonalOnly"),
])


def _reverse(table):
    out = {}
    for token, value in table.items():
        out.setdefault(value, token)      # first token listed wins
    return out


_STYLE_NAMES = _reverse(_STYLES)
_OUTLINE_NAMES = _reverse(_OUTLINES)
_ANGLE_NAMES = _reverse(_ANGLES)


def _active_view():
    """The current view. Rulers need a VIEW, not a layout — so this does
    not create a scratch layout the way `_active_layout` does."""
    mw = pya.Application.instance().main_window()
    view = mw.current_view() if mw is not None else None
    if view is None:
        raise RpcError(
            ErrorCode.NO_VIEW,
            "no layout view is open, so there are no rulers",
            hint="open a layout first (layout.show_file / view.new_tab)",
        )
    return view


def _resolve(table, value, what):
    key = str(value).strip().lower()
    if key not in table:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "unknown %s %r" % (what, value),
            hint="this KLayout build accepts: %s" % ", ".join(sorted(table)),
        )
    return table[key]


def _token_of(names, value):
    try:
        return names.get(int(value))
    except Exception:
        return None


def _points_of(annotation):
    """Full point list. Falls back to p1/p2 on pre-0.28 builds, where
    multi-segment rulers did not exist and the pair IS the whole ruler."""
    try:
        pts = list(annotation.points)
        if pts:
            return pts
    except Exception:
        pass
    try:
        return [annotation.p1, annotation.p2]
    except Exception:
        return []


def _segments_of(annotation, points):
    try:
        return int(annotation.segments())
    except Exception:
        return max(1, len(points) - 1)


def _selected_ids(view):
    ids = set()
    try:
        for a in view.each_annotation_selected():
            ids.add(int(a.id()))
    except Exception:
        pass
    return ids


def _ruler_dict(annotation, selected_ids=frozenset()):
    """A ruler as PLAIN JSON — a snapshot, never a live reference.

    `each_annotation` yields live objects (0.25+): assigning to one of
    their properties edits the user's view immediately. Everything that
    leaves this module is therefore copied out into primitives here.
    """
    points = _points_of(annotation)
    nseg = _segments_of(annotation, points)
    d = {
        "id": int(annotation.id()),
        "points_um": [[float(p.x), float(p.y)] for p in points],
        "segments": nseg,
    }
    for key, attr, names in (
        ("style", "style", _STYLE_NAMES),
        ("outline", "outline", _OUTLINE_NAMES),
        ("angle_constraint", "angle_constraint", _ANGLE_NAMES),
    ):
        try:
            raw = getattr(annotation, attr)
            d[key] = _token_of(names, raw)
            d[key + "_value"] = int(raw)
        except Exception:
            pass
    for key, attr in (("category", "category"), ("fmt", "fmt")):
        try:
            d[key] = str(getattr(annotation, attr) or "")
        except Exception:
            d[key] = ""
    try:
        d["snap"] = bool(annotation.snap)
    except Exception:
        pass
    texts = []
    for i in range(nseg):
        try:
            texts.append(str(annotation.text(i)))
        except Exception:
            break
    d["texts"] = texts
    try:
        bb = annotation.box()
        if not bb.empty():
            d["bbox_um"] = [bb.left, bb.bottom, bb.right, bb.top]
    except Exception:
        pass
    try:
        d["s"] = str(annotation.to_s())
    except Exception:
        pass
    d["selected"] = int(d["id"]) in selected_ids
    return d


def _parse_points(value):
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "points_um must be a list of at least two [x, y] pairs (microns)",
            hint="a two-point ruler is [[0, 0], [10, 0]]; more points make "
                 "a multi-segment ruler",
        )
    if len(value) > _MAX_POINTS:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "points_um has %d points, max %d" % (len(value), _MAX_POINTS))
    points = []
    for i, pair in enumerate(value):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise RpcError(ErrorCode.BAD_PARAMS,
                           "points_um[%d] must be [x, y] in microns" % i)
        try:
            points.append(pya.DPoint(float(pair[0]), float(pair[1])))
        except Exception:
            raise RpcError(ErrorCode.BAD_PARAMS,
                           "points_um[%d] is not numeric" % i)
    # KLayout silently drops duplicate consecutive points on assignment,
    # which would turn a 2-point ruler into a 1-point one AFTER the edit
    # landed. Refuse before mutating instead.
    distinct = [points[0]]
    for p in points[1:]:
        if abs(p.x - distinct[-1].x) > 1e-9 or abs(p.y - distinct[-1].y) > 1e-9:
            distinct.append(p)
    if len(distinct) < 2:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "points_um collapses to a single point (KLayout drops "
            "duplicates), so there is no ruler to draw",
            hint="give two DIFFERENT points",
        )
    return points


def _apply_properties(annotation, params):
    """Set only the properties the caller actually passed."""
    if "points_um" in params:
        annotation.points = _parse_points(params["points_um"])
    if params.get("style") is not None:
        annotation.style = _resolve(_STYLES, params["style"], "style")
    if params.get("outline") is not None:
        annotation.outline = _resolve(_OUTLINES, params["outline"], "outline")
    if params.get("angle_constraint") is not None:
        annotation.angle_constraint = _resolve(
            _ANGLES, params["angle_constraint"], "angle_constraint")
    if params.get("text") is not None:
        # `text` is a formatted, READ-only value in pya; a literal label
        # is set by making it the format string.
        annotation.fmt = str(params["text"])
    if params.get("fmt") is not None:
        annotation.fmt = str(params["fmt"])
    if params.get("category") is not None:
        annotation.category = str(params["category"])
    if params.get("snap") is not None:
        annotation.snap = bool(params["snap"])


_RULER_PROPS = {
    "points_um": {
        "type": "array",
        "description": "Ruler points in MICRONS: [[x1,y1],[x2,y2],...]. "
                       "More than two points makes a multi-segment ruler.",
        "items": {"type": "array", "items": {"type": "number"},
                  "minItems": 2, "maxItems": 2},
        "minItems": 2,
    },
    "text": {"type": "string",
             "description": "Literal label (stored as the ruler format string)."},
    "fmt": {"type": "string",
            "description": "KLayout format string with measurement "
                           "placeholders; overrides `text` if both are given."},
    "style": {"type": "string",
              "description": "ruler | line | arrow_end | arrow_start | "
                             "arrow_both | cross_start | cross_end | cross_both"},
    "outline": {"type": "string",
                "description": "diag | xy | yx | diag_xy | diag_yx | box | "
                               "ellipse | angle | radius"},
    "angle_constraint": {"type": "string",
                         "description": "any | global | horizontal | vertical | "
                                        "ortho | diagonal"},
    "category": {"type": "string",
                 "description": "Free tag. klink stamps 'klink' by default so "
                                "agent-drawn rulers can be told apart from the "
                                "user's."},
    "snap": {"type": "boolean", "description": "Snap to objects when moved."},
}


@method(
    "annotation.list",
    description=(
        "List the rulers (annotations) in the current view — including the "
        "ones the USER drew by hand. Rulers live in the view, not in the "
        "layout: they are invisible to selection.get and to any saved GDS, "
        "so this is the only way to read them. Each entry carries the FULL "
        "points_um list and the segment count; a multi-segment ruler is not "
        "reducible to a start/end pair."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "selected_only": {"type": "boolean", "default": False,
                              "description": "Only rulers selected in the GUI."},
            "category": {"type": "string",
                         "description": "Only rulers with this category tag."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 5000,
                      "default": 500},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "truncated": {"type": "boolean"},
            "rulers": {"type": "array"},
        },
    },
    tags=["annotation", "read"],
)
def annotation_list(params, ctx):
    view = _active_view()
    limit = int(params.get("limit", 500))
    if limit < 1 or limit > 5000:
        raise RpcError(ErrorCode.BAD_PARAMS, "limit must be 1..5000")
    selected_only = bool(params.get("selected_only", False))
    category = params.get("category")
    selected = _selected_ids(view)

    source = (view.each_annotation_selected() if selected_only
              else view.each_annotation())
    rulers = []
    truncated = False
    for a in source:
        if category is not None and str(a.category or "") != str(category):
            continue
        if len(rulers) >= limit:
            truncated = True
            break
        rulers.append(_ruler_dict(a, selected))
    return {"count": len(rulers), "truncated": truncated, "rulers": rulers}


@method(
    "annotation.get",
    description="One ruler by id (see annotation.list). Errors if the id is "
                "not a live ruler in the current view.",
    params_schema={
        "type": "object",
        "required": ["id"],
        "properties": {"id": {"type": "integer"}},
    },
    returns_schema={"type": "object",
                    "properties": {"ruler": {"type": "object"}}},
    tags=["annotation", "read"],
)
def annotation_get(params, ctx):
    view = _active_view()
    if "id" not in params:
        raise RpcError(ErrorCode.BAD_PARAMS, "id is required")
    rid = int(params["id"])
    a = view.annotation(rid)
    if a is None or not a.is_valid():
        raise RpcError(
            ErrorCode.NOT_FOUND,
            "no ruler with id %d in the current view" % rid,
            hint="call annotation.list for the live ids",
        )
    return {"ruler": _ruler_dict(a, _selected_ids(view))}


@method(
    "annotation.insert",
    description=(
        "Draw a ruler in the current view. This is the agent->user pointing "
        "channel for a LINE (view.highlight points at an AREA): propose a "
        "cross-section cut, mark a spacing, show where a route will go — the "
        "user can then drag the ruler and you re-read it with annotation.list."
    ),
    params_schema={
        "type": "object",
        "required": ["points_um"],
        "properties": dict(_RULER_PROPS),
    },
    returns_schema={
        "type": "object",
        "properties": {"id": {"type": "integer"}, "ruler": {"type": "object"}},
    },
    mutates=True,
    tags=["annotation", "write"],
)
def annotation_insert(params, ctx):
    view = _active_view()
    if "points_um" not in params:
        raise RpcError(ErrorCode.BAD_PARAMS, "points_um is required")
    a = pya.Annotation()
    merged = dict(params)
    merged.setdefault("category", "klink")
    merged.setdefault("style", "ruler")
    _apply_properties(a, merged)
    with auto_txn(view, "klink: annotation.insert"):
        view.insert_annotation(a)
    return {"id": int(a.id()), "ruler": _ruler_dict(a, _selected_ids(view))}


@method(
    "annotation.update",
    description="Change an existing ruler in place (only the properties you "
                "pass). Use annotation.list first to get the id.",
    params_schema={
        "type": "object",
        "required": ["id"],
        "properties": dict(_RULER_PROPS, id={"type": "integer"}),
    },
    returns_schema={
        "type": "object",
        "properties": {"id": {"type": "integer"}, "ruler": {"type": "object"}},
    },
    mutates=True,
    tags=["annotation", "write"],
)
def annotation_update(params, ctx):
    view = _active_view()
    if "id" not in params:
        raise RpcError(ErrorCode.BAD_PARAMS, "id is required")
    rid = int(params["id"])
    a = view.annotation(rid)
    if a is None or not a.is_valid():
        raise RpcError(
            ErrorCode.NOT_FOUND,
            "no ruler with id %d in the current view" % rid,
            hint="call annotation.list for the live ids",
        )
    edits = {k: v for k, v in params.items() if k != "id"}
    if not edits:
        raise RpcError(ErrorCode.BAD_PARAMS,
                       "nothing to update; pass at least one property",
                       hint="e.g. points_um, text, style, category")
    with auto_txn(view, "klink: annotation.update %d" % rid):
        _apply_properties(a, edits)
    return {"id": rid, "ruler": _ruler_dict(a, _selected_ids(view))}


@method(
    "annotation.delete",
    description="Delete rulers by id or by category tag. Pass exactly one of "
                "`id` / `category`. To wipe every ruler use annotation.clear.",
    params_schema={
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "category": {"type": "string",
                         "description": "Delete every ruler carrying this tag "
                                        "(e.g. 'klink' for agent-drawn ones)."},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {"deleted": {"type": "integer"},
                       "ids": {"type": "array"}},
    },
    mutates=True,
    tags=["annotation", "write"],
)
def annotation_delete(params, ctx):
    view = _active_view()
    has_id = params.get("id") is not None
    has_cat = params.get("category") is not None
    if has_id == has_cat:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "pass exactly one of id / category",
            hint="annotation.delete {id: 3} or {category: 'klink'}",
        )
    if has_id:
        rid = int(params["id"])
        a = view.annotation(rid)
        if a is None or not a.is_valid():
            raise RpcError(ErrorCode.NOT_FOUND,
                           "no ruler with id %d in the current view" % rid)
        ids = [rid]
    else:
        want = str(params["category"])
        # collect ids FIRST: erasing while iterating the view's own
        # annotation list is undefined
        ids = [int(a.id()) for a in view.each_annotation()
               if str(a.category or "") == want]
    with auto_txn(view, "klink: annotation.delete (%d)" % len(ids)):
        for rid in ids:
            try:
                view.erase_annotation(rid)
            except Exception:
                pass
    return {"deleted": len(ids), "ids": ids}


@method(
    "annotation.clear",
    description=(
        "Delete EVERY ruler in the current view, the user's included. "
        "Destructive: requires confirm=true. Prefer "
        "annotation.delete{category:'klink'} to clear only agent-drawn ones."
    ),
    params_schema={
        "type": "object",
        "properties": {"confirm": {"type": "boolean", "default": False}},
    },
    returns_schema={"type": "object",
                    "properties": {"deleted": {"type": "integer"}}},
    mutates=True,
    tags=["annotation", "write"],
)
def annotation_clear(params, ctx):
    view = _active_view()
    count = sum(1 for _ in view.each_annotation())
    if not bool(params.get("confirm", False)):
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "annotation.clear would delete %d ruler(s) including the user's; "
            "pass confirm=true if that is really the intent" % count,
            hint="to remove only agent-drawn rulers: "
                 "annotation.delete {category: 'klink'}",
        )
    with auto_txn(view, "klink: annotation.clear (%d)" % count):
        view.clear_annotations()
    return {"deleted": count}


@method(
    "annotation.measure",
    description=(
        "Auto-measure ruler at a seed point: KLayout looks outward from the "
        "point along the allowed directions, finds the nearest edges on "
        "VISIBLE layers, and pulls a ruler between them. This is 'how wide is "
        "this gap / this line' without knowing any coordinates. If nothing is "
        "found nearby the measurement fails and the degenerate zero-length "
        "ruler is removed again (measured=false)."
    ),
    params_schema={
        "type": "object",
        "required": ["point_um"],
        "properties": {
            "point_um": {"type": "array", "items": {"type": "number"},
                         "minItems": 2, "maxItems": 2,
                         "description": "Seed point [x, y] in microns."},
            "angle_constraint": {"type": "string", "default": "any",
                                 "description": "Search directions: any | "
                                                "horizontal | vertical | ortho "
                                                "| diagonal | global"},
            "category": {"type": "string", "default": "klink"},
            "keep_failed": {"type": "boolean", "default": False,
                            "description": "Keep the zero-length ruler when "
                                           "the measurement finds nothing."},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "measured": {"type": "boolean"},
            "id": {"type": "integer"},
            "ruler": {"type": "object"},
        },
    },
    mutates=True,
    tags=["annotation", "write"],
)
def annotation_measure(params, ctx):
    view = _active_view()
    if not hasattr(view, "create_measure_ruler"):
        raise RpcError(
            ErrorCode.BAD_REQUEST,
            "this KLayout build has no LayoutView.create_measure_ruler",
            hint="auto-measure needs KLayout 0.26 or newer; draw the ruler "
                 "explicitly with annotation.insert instead",
        )
    pt = params.get("point_um")
    if not isinstance(pt, (list, tuple)) or len(pt) != 2:
        raise RpcError(ErrorCode.BAD_PARAMS,
                       "point_um must be [x, y] in microns")
    ac = _resolve(_ANGLES, params.get("angle_constraint", "any"),
                  "angle_constraint")
    with auto_txn(view, "klink: annotation.measure"):
        a = view.create_measure_ruler(
            pya.DPoint(float(pt[0]), float(pt[1])), ac)
        if a is None or not a.is_valid():
            raise RpcError(ErrorCode.INTERNAL,
                           "create_measure_ruler returned no ruler")
        try:
            a.category = str(params.get("category") or "klink")
        except Exception:
            pass
        points = _points_of(a)
        measured = (len(points) >= 2
                    and (abs(points[0].x - points[-1].x) > 1e-9
                         or abs(points[0].y - points[-1].y) > 1e-9))
        out = {"measured": measured, "id": int(a.id()),
               "ruler": _ruler_dict(a, _selected_ids(view))}
        if not measured and not bool(params.get("keep_failed", False)):
            # a zero-length ruler is litter in the user's view, and it is
            # not a measurement — take it back out
            try:
                view.erase_annotation(int(a.id()))
                out["removed"] = True
            except Exception:
                pass
    if not measured:
        out["hint"] = ("no visible geometry near the seed point in the "
                       "allowed directions; check the layer is visible, move "
                       "the point, or widen angle_constraint")
    return out

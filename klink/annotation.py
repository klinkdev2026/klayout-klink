"""Rulers as data — the DECISION half of klink's annotation support.

The plugin module ``annotation_m.py`` does only what genuinely needs the
KLayout GUI: read and write ``pya.Annotation`` objects on the current
view, and hand them out as plain dicts. It makes no choices.

Every choice lives here, in ordinary Python that runs anywhere and is
testable without KLayout:

* which of several rulers a request means (:func:`pick_ruler`);
* what a ruler measures (:func:`segment_lengths_um`, :func:`total_length_um`);
* whether a ruler can serve as a cross-section cut, and what to do when it
  cannot (:func:`cut_line_um`).

The multi-segment rule is the reason this module exists
-------------------------------------------------------
Since KLayout 0.28 a ruler may have more than two points. The convenient
``p1``/``p2`` pair still exists but reports only the first and last point,
so a 4-point ruler read that way turns into a straight line the user never
drew — and a cross-section taken along it is wrong without anything having
failed. So: nothing here ever reduces a ruler to its endpoints silently.
A multi-segment ruler either names its segment or raises.

Ruler dicts are whatever ``annotation.list`` returned; the only keys this
module requires are ``points_um`` and ``id``.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "RulerError",
    "segment_points_um",
    "segment_lengths_um",
    "total_length_um",
    "describe",
    "pick_ruler",
    "cut_line_um",
    "read_rulers",
]

Point = Tuple[float, float]


class RulerError(ValueError):
    """Bad or ambiguous ruler input; the message says what to do next."""


def _points(ruler: Mapping[str, Any]) -> List[Point]:
    pts = ruler.get("points_um")
    if not isinstance(pts, (list, tuple)) or len(pts) < 2:
        raise RulerError(
            "ruler %s has no usable points_um (got %r); annotation.list "
            "returns the full point list — do not fall back to p1/p2"
            % (ruler.get("id", "?"), pts)
        )
    return [(float(p[0]), float(p[1])) for p in pts]


def segment_points_um(ruler: Mapping[str, Any]) -> List[Tuple[Point, Point]]:
    """The ruler's segments as ``(start, end)`` point pairs."""
    pts = _points(ruler)
    return list(zip(pts[:-1], pts[1:]))


def segment_lengths_um(ruler: Mapping[str, Any]) -> List[float]:
    """Length of each segment, in microns."""
    return [math.hypot(b[0] - a[0], b[1] - a[1])
            for a, b in segment_points_um(ruler)]


def total_length_um(ruler: Mapping[str, Any]) -> float:
    """Summed length of every segment (the polyline length, NOT the
    straight distance between the first and last point)."""
    return float(sum(segment_lengths_um(ruler)))


def describe(ruler: Mapping[str, Any]) -> str:
    """One line naming a ruler, for choose-one error messages."""
    pts = ruler.get("points_um") or []
    try:
        length = total_length_um(ruler)
    except RulerError:
        length = float("nan")
    label = ""
    texts = ruler.get("texts") or []
    if texts and str(texts[0]).strip():
        label = " %r" % str(texts[0]).strip()
    seg = len(pts) - 1 if len(pts) >= 2 else 0
    where = ""
    if len(pts) >= 2:
        where = " (%.3f, %.3f)->(%.3f, %.3f)" % (
            float(pts[0][0]), float(pts[0][1]),
            float(pts[-1][0]), float(pts[-1][1]))
    return "id=%s%s %d segment(s), %.3f um total%s" % (
        ruler.get("id", "?"), label, seg, length, where)


def pick_ruler(
    rulers: Sequence[Mapping[str, Any]],
    *,
    ruler_id: Optional[int] = None,
    category: Optional[str] = None,
    prefer_selected: bool = True,
) -> Dict[str, Any]:
    """Resolve "the ruler" out of what the view holds.

    Order of narrowing: explicit ``ruler_id`` wins; then ``category``;
    then, if several remain and ``prefer_selected``, the ones the user has
    selected in the GUI. Ambiguity is never broken by picking the first
    one — it raises and lists the candidates, because guessing here means
    sectioning a line nobody asked for.
    """
    if ruler_id is not None:
        for r in rulers:
            if int(r.get("id", -1)) == int(ruler_id):
                return dict(r)
        raise RulerError(
            "no ruler with id %s in the view; annotation.list currently "
            "reports: %s" % (
                ruler_id,
                ", ".join(str(r.get("id")) for r in rulers) or "none")
        )

    pool = list(rulers)
    if category is not None:
        pool = [r for r in pool if str(r.get("category", "")) == str(category)]
        if not pool:
            raise RulerError(
                "no ruler tagged category=%r; drop the filter or tag the "
                "ruler with annotation.update" % category)

    if not pool:
        raise RulerError(
            "there are no rulers in the view. Draw one in KLayout (the "
            "ruler tool), or place it from here with annotation.insert, "
            "or pass explicit coordinates instead")

    if len(pool) > 1 and prefer_selected:
        selected = [r for r in pool if r.get("selected")]
        if len(selected) == 1:
            return dict(selected[0])
        if selected:
            pool = selected

    if len(pool) == 1:
        return dict(pool[0])

    raise RulerError(
        "%d rulers match, so which one is meant is ambiguous:\n  %s\n"
        "Pass ruler_id=<id>, select exactly one in KLayout, or filter by "
        "category." % (len(pool), "\n  ".join(describe(r) for r in pool))
    )


def cut_line_um(
    ruler: Mapping[str, Any],
    *,
    segment: Optional[int] = None,
) -> List[List[float]]:
    """``[[x1, y1], [x2, y2]]`` — one STRAIGHT cut line, in microns.

    A cross-section engine sections along a single straight line. A
    two-point ruler is that line. A multi-segment ruler is not, and this
    refuses to flatten it to its endpoints: pass ``segment=<index>`` to
    name which leg to cut along.
    """
    segs = segment_points_um(ruler)
    if segment is None:
        if len(segs) != 1:
            lengths = segment_lengths_um(ruler)
            listing = ", ".join(
                "segment=%d (%.3f um)" % (i, L) for i, L in enumerate(lengths))
            raise RulerError(
                "ruler %s has %d segments, and a cross-section is taken "
                "along ONE straight line — its endpoints are NOT the cut "
                "(that would section a line you never drew). Choose one: "
                "%s; or draw a plain two-point ruler."
                % (ruler.get("id", "?"), len(segs), listing))
        index = 0
    else:
        index = int(segment)
        if index < 0 or index >= len(segs):
            raise RulerError(
                "segment=%d is out of range; ruler %s has %d segment(s) "
                "(0..%d)" % (index, ruler.get("id", "?"), len(segs),
                             len(segs) - 1))
    (x1, y1), (x2, y2) = segs[index]
    if abs(x1 - x2) < 1e-9 and abs(y1 - y2) < 1e-9:
        raise RulerError(
            "ruler %s segment %d has zero length, so it defines no cut "
            "line" % (ruler.get("id", "?"), index))
    return [[x1, y1], [x2, y2]]


def read_rulers(client, **kwargs) -> List[Dict[str, Any]]:
    """``annotation.list`` through any klink client, as a plain list.

    Convenience only — the RPC is the contract, and this adds nothing
    beyond unwrapping the envelope.
    """
    result = client.call("annotation.list", dict(kwargs)) or {}
    return list(result.get("rulers") or [])

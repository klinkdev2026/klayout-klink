"""Pad ingestion + peripheral port helpers -- the MODULAR sources of an
``io_pads`` table (see ``route_and_draw_flexdr``).

The lab-real contract: the probe card / pad ring is an ARTIFACT THE USER HANDS
YOU (a GDS file, or a cell already open in KLayout) -- never something the
router invents. These helpers turn any of those sources into the same plain
pad table ``[{"id", "box_um"}, ...]``; net assignment stays a human/agent
decision made on top (add ``"net": ...`` to the entries you use).

Pure mechanism: no layer numbers, no pad sizes, no card geometry in klink.
``pads_from_gds`` needs the ``klayout`` pip package (named in the error).
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

__all__ = ["pads_from_gds", "pads_from_boxes", "spread_ports"]


def pads_from_boxes(boxes_um: Sequence[Sequence[float]], *, prefix: str = "PAD") -> List[dict]:
    """Wrap raw boxes as a pad table with stable ids (sorted top row first,
    then left to right -- the order a human reads a card)."""
    norm = []
    for b in boxes_um:
        x1, y1, x2, y2 = (float(v) for v in b)
        norm.append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
    norm.sort(key=lambda b: (-round(b[3], 3), round(b[0], 3)))
    return [{"id": f"{prefix}{i:02d}", "box_um": list(b)} for i, b in enumerate(norm)]


def pads_from_gds(path: str, cell: str, layer: str, *,
                  min_size_um: float = 0.0, prefix: str = "PAD") -> List[dict]:
    """Harvest pad boxes from a probe-card GDS: merged shapes on ``layer``
    (\"L/D\") of ``cell`` -> pad table. Each merged polygon contributes its
    bounding box (cards are rectangles; a non-rectangular pad still gets its
    bbox). ``min_size_um`` drops slivers (alignment marks, labels)."""
    try:
        import klayout.db as kdb
    except ImportError as exc:
        raise ImportError(
            "pads_from_gds needs the 'klayout' package in THIS interpreter: "
            "pip install klayout") from exc
    ly = kdb.Layout()
    ly.read(path)
    top = ly.cell(cell)
    if top is None:
        raise ValueError(f"cell {cell!r} not found in {path!r}; "
                         f"cells: {[c.name for c in ly.each_cell()][:10]}")
    l, d = (int(x) for x in layer.split("/"))
    li = ly.layer(l, d)
    region = kdb.Region(top.begin_shapes_rec(li))
    region.merge()
    dbu = ly.dbu
    boxes = []
    for poly in region.each():
        bb = poly.bbox()
        w, h = bb.width() * dbu, bb.height() * dbu
        if w >= min_size_um and h >= min_size_um:
            boxes.append([bb.left * dbu, bb.bottom * dbu, bb.right * dbu, bb.top * dbu])
    if not boxes:
        raise ValueError(
            f"no pads found on layer {layer!r} of cell {cell!r} in {path!r} "
            f"(min_size_um={min_size_um}); check the layer number and that the "
            "pads are drawn in this cell (or its children)")
    return pads_from_boxes(boxes, prefix=prefix)


def spread_ports(bbox_um: Sequence[float], nets: Sequence[str], *, side: str,
                 size_um: float, clear_um: float, prefix: str = "IO",
                 snap: Sequence[float] = ()) -> List[dict]:
    """The NO-pad default: each net is brought out as a BARE labelled trace --
    one wire-end target per net (``draw: False``: no pad box is drawn, the
    route just ends there and gets its net-name text), evenly spread along one
    side of ``bbox_um``. Pass ``size_um`` = the profile's wire width so the
    target is exactly a wire end. side: N|S|E|W.

    ``snap``: candidate coordinates ALONG the edge (y for E/W, x for N/S) the
    stubs snap to -- e.g. the ROUTING CHANNEL centres between device rows. A
    stub whose y lands inside a device-row band forces its horizontal run to
    jog into a neighbouring channel; when every stub does that, one channel
    overflows. Snapping distributes stubs channel-by-channel (round-robin,
    wrap offsets by 2*size) and removes that artificial contention."""
    x1, y1, x2, y2 = (float(v) for v in bbox_um)
    n = len(nets)
    if n == 0:
        return []
    snap_s = sorted(float(v) for v in snap)
    out: List[dict] = []
    for i, net in enumerate(nets):
        if snap_s:
            c = snap_s[i % len(snap_s)] + (i // len(snap_s)) * 2.0 * size_um
        elif side in ("N", "S"):
            c = x1 + (x2 - x1) * (i + 0.5) / n
        else:
            c = y1 + (y2 - y1) * (i + 0.5) / n
        if side in ("N", "S"):
            yb = (y2 + clear_um) if side == "N" else (y1 - clear_um - size_um)
            box = [c - size_um / 2, yb, c + size_um / 2, yb + size_um]
        elif side in ("E", "W"):
            xb = (x2 + clear_um) if side == "E" else (x1 - clear_um - size_um)
            box = [xb, c - size_um / 2, xb + size_um, c + size_um / 2]
        else:
            raise ValueError(f"side must be N|S|E|W, got {side!r}")
        out.append({"id": f"{prefix}_{side}{i:02d}", "box_um": box, "net": net,
                    "draw": False})
    return out

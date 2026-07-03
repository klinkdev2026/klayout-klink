"""
Bridge KLayout pya signals -> klink event channels.

Implementation notes
--------------------
pya exposes two different kinds of callbacks:

* Qt signals on QObject subclasses (e.g. QTcpSocket.readyRead) are bound
  by CALLING the signal attribute: `sock.readyRead(handler)`.
* KLayout "events" on non-QObject classes (LayoutView / MainWindow's
  `on_*`) are bound with `+=`: `view.on_selection_changed += handler`.
  Critically, `+=` must be applied to the attribute of the OWNING
  object, not to a local variable: `local = view.on_x; local += h`
  silently does nothing.

So we resolve the event each time and hit it directly. We try `+=`
first, then fall back to a plain call `event(handler)` for builds
where that is the supported form.

To avoid double-binding when a view is re-hooked (e.g. view rebind
after opening a new file), we track which views we've already hooked
by Python id.
"""

from __future__ import annotations

import operator
import os
import time

import pya

from .log import get_logger

_log = get_logger("signals")


# Events we want on every LayoutView (attribute name on the view).
_VIEW_EVENTS = (
    "on_selection_changed",
    "on_viewport_changed",
    "on_layer_list_changed",
    "on_cellviews_changed",
    "on_active_cellview_changed",
)


def _fingerprint(shape) -> tuple:
    """Full, hashable identity for a pya.Shape. Encodes every coordinate
    (points, widths, bboxes, text), so it is exact: moving a polygon by
    one dbu yields a different fingerprint. Used both for Counter diff
    across snapshots AND as the source of truth for event payloads, so
    that events carry full geometry."""
    try:
        if shape.is_box():
            b = shape.box
            return ("box", b.left, b.bottom, b.right, b.top)
        if shape.is_polygon():
            p = shape.polygon
            hull = tuple((pt.x, pt.y) for pt in p.each_point_hull())
            holes = tuple(
                tuple((pt.x, pt.y) for pt in p.each_point_hole(i))
                for i in range(p.holes())
            )
            return ("polygon", hull, holes)
        if shape.is_simple_polygon():
            p = shape.simple_polygon
            pts = tuple((pt.x, pt.y) for pt in p.each_point())
            return ("polygon", pts, ())
        if shape.is_path():
            p = shape.path
            pts = tuple((pt.x, pt.y) for pt in p.each_point())
            # bgn_ext / end_ext / round capture the path's endcap style
            # (KLayout's "extension type" UI setting). Without these, a
            # flush-ended path (bgn/end=0) replays as a square-ended one
            # (bgn/end=width/2) because that's the pya.Path default.
            # We include them in the fingerprint so that changing JUST
            # the endcap style on an existing path registers as a real
            # mutation in the diff.
            return (
                "path", pts, int(p.width),
                int(p.bgn_ext), int(p.end_ext), bool(p.round),
            )
        if shape.is_text():
            t = shape.text
            return ("text", t.x, t.y, t.string or "")
    except Exception as exc:
        _log.debug("swallowed in _fingerprint: %s", exc)
    try:
        return ("other", shape.to_s())
    except Exception:
        return ("other", id(shape))


def _fp_to_dict(fp: tuple) -> dict:
    kind = fp[0]
    if kind == "box":
        return {
            "type": "box",
            "bbox_dbu": [fp[1], fp[2], fp[3], fp[4]],
        }
    if kind == "polygon":
        hull, holes = fp[1], fp[2]
        d: dict = {
            "type": "polygon",
            "points_dbu": [[pt[0], pt[1]] for pt in hull],
        }
        if holes:
            d["holes_dbu"] = [[[pt[0], pt[1]] for pt in h] for h in holes]
        return d
    if kind == "path":
        pts, width = fp[1], fp[2]
        d: dict = {
            "type": "path",
            "points_dbu": [[pt[0], pt[1]] for pt in pts],
            "width_dbu": width,
        }
        # Endcap metadata (added in lock-step with _fingerprint). Always
        # emitted (even for pya defaults) so recorded scripts are an
        # exact transcript: a user who drew a flush path should see
        # begin_ext_dbu=0 / end_ext_dbu=0 in the replay, not have those
        # fields omitted and silently default to square (width/2).
        if len(fp) >= 6:
            d["begin_ext_dbu"] = int(fp[3])
            d["end_ext_dbu"] = int(fp[4])
            d["round_ends"] = bool(fp[5])
        return d
    if kind == "text":
        return {
            "type": "text",
            "position_dbu": [fp[1], fp[2]],
            "string": fp[3],
        }
    return {"type": "other"}


def _json_safe(v):
    """Make a pya-returned value JSON serialisable AND round-trippable.

    Geometric pya types are emitted as the SAME magic-dict shapes that
    `_adapt_pcell_value` in methods/instance_m.py knows how to parse
    back. Concretely:

        pya.LayerInfo             -> {"layer": L, "datatype": D}
        pya.DPoint / pya.Point    -> {"point_um": [x, y]}
        pya.DBox   / pya.Box      -> {"bbox_um": [l, b, r, t]}
        pya.DPath  / pya.Path     -> {"points_um": [...], "width_um": w}
        pya.DPolygon / pya.Polygon
        / pya.DSimplePolygon / pya.SimplePolygon
                                  -> {"points_um": [...]}

    This matters for the macro recorder: Basic-library PCells take
    geometric parameters whose pya types we previously stringified via
    `.to_s()`, yielding payloads like `"(-5,0)"` or `"(0,0;1,0;...) w=2"`
    that the server-side adapter cannot turn back into pya objects.
    With this mapping, feeding a recorded `instance.insert_pcell`
    payload back through the same RPC reproduces the original PCell
    variant exactly (CIRCLE.handle, ELLIPSE.handle_*, ROUND_PATH.path,
    ROUND_POLYGON.polygon, STROKED_BOX/POLYGON.shape, etc).

    NOTE: integer-dbu pya types (Point/Box/Path/Polygon) are assumed
    to represent micrometer-scale intent even though their coordinates
    are raw ints. Basic PCells declare their geometric params as the
    D-variants, so in practice pya only ever hands us D-variants here.
    Anything we still can't classify falls back to `.to_s()` (useful
    for diagnostics, not replayable)."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _json_safe(x) for k, x in v.items()}

    # LayerInfo
    try:
        if isinstance(v, pya.LayerInfo):
            return {"layer": int(v.layer), "datatype": int(v.datatype)}
    except Exception as exc:
        _log.debug("swallowed in _json_safe: %s", exc)
    # DPoint / Point
    try:
        if isinstance(v, (pya.DPoint, pya.Point)):
            return {"point_um": [float(v.x), float(v.y)]}
    except Exception as exc:
        _log.debug("swallowed in _json_safe: %s", exc)
    # DVector / Vector (same shape as DPoint for round-trip purposes)
    try:
        if isinstance(v, (pya.DVector, pya.Vector)):
            return {"point_um": [float(v.x), float(v.y)]}
    except Exception as exc:
        _log.debug("swallowed in _json_safe: %s", exc)
    # DBox / Box
    try:
        if isinstance(v, (pya.DBox, pya.Box)):
            return {"bbox_um": [float(v.left), float(v.bottom),
                                float(v.right), float(v.top)]}
    except Exception as exc:
        _log.debug("swallowed in _json_safe: %s", exc)
    # DPath / Path (preserve width so replay rebuilds the stroked path)
    try:
        if isinstance(v, (pya.DPath, pya.Path)):
            pts = [[float(p.x), float(p.y)] for p in v.each_point()]
            return {"points_um": pts, "width_um": float(v.width)}
    except Exception as exc:
        _log.debug("swallowed in _json_safe: %s", exc)
    # DPolygon / Polygon (with holes if any)
    try:
        if isinstance(v, (pya.DPolygon, pya.Polygon)):
            pts = [[float(p.x), float(p.y)] for p in v.each_point_hull()]
            out: dict = {"points_um": pts}
            try:
                nh = int(v.holes())
                if nh > 0:
                    out["holes_um"] = [
                        [[float(p.x), float(p.y)]
                         for p in v.each_point_hole(i)]
                        for i in range(nh)
                    ]
            except Exception as exc:
                _log.debug("swallowed in _json_safe: %s", exc)
            return out
    except Exception as exc:
        _log.debug("swallowed in _json_safe: %s", exc)
    # DSimplePolygon / SimplePolygon (no holes)
    try:
        if isinstance(v, (pya.DSimplePolygon, pya.SimplePolygon)):
            pts = [[float(p.x), float(p.y)] for p in v.each_point()]
            return {"points_um": pts}
    except Exception as exc:
        _log.debug("swallowed in _json_safe: %s", exc)

    for attr in ("to_s", "__str__"):
        try:
            s = getattr(v, attr)()
            return s if isinstance(s, str) else str(s)
        except Exception:
            continue
    return str(v)


def _hashable(v):
    """Version of _json_safe whose output is also hashable (for tuple fp).

    LOSSY: dicts -> tuple-of-pairs, lists -> plain tuple; the two are
    indistinguishable downstream. Prefer `_jsonsafe_to_hashable` /
    `_hashable_to_jsonsafe` when you need to round-trip."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        return tuple(_hashable(x) for x in v)
    if isinstance(v, dict):
        return tuple(sorted((str(k), _hashable(x)) for k, x in v.items()))
    try:
        return v.to_s()
    except Exception:
        return str(v)


# Tagged hashable form that preserves dict-vs-list identity so payloads
# can be reconstructed from fingerprints without guessing. The tags
# ("__d", "__l") are chosen to be unlikely to collide with real tuple
# data in pya params - real pya values never start with these strings.
def _jsonsafe_to_hashable(v):
    """Render a JSON-safe value (dict/list/primitive) into a hashable
    tuple form that is reversible via `_hashable_to_jsonsafe`. Used to
    embed PCell params inside the hashable instance fingerprint without
    losing the dict-vs-list distinction - which is critical for replay,
    since `_adapt_pcell_value` on the server keys off magic-dict shapes
    like {"point_um": [...]} / {"points_um": [...], "width_um": ...}."""
    if isinstance(v, dict):
        return ("__d", tuple(sorted(
            (str(k), _jsonsafe_to_hashable(x)) for k, x in v.items()
        )))
    if isinstance(v, (list, tuple)):
        return ("__l", tuple(_jsonsafe_to_hashable(x) for x in v))
    return v


def _hashable_to_jsonsafe(h):
    """Inverse of `_jsonsafe_to_hashable`. Unknown tuples pass through
    unchanged so callers can mix legacy hashables in without crashing."""
    if isinstance(h, tuple) and len(h) == 2 and h[0] == "__d":
        try:
            return {k: _hashable_to_jsonsafe(v) for k, v in h[1]}
        except Exception:
            return h
    if isinstance(h, tuple) and len(h) == 2 and h[0] == "__l":
        return [_hashable_to_jsonsafe(x) for x in h[1]]
    return h


def _pcell_info(cell) -> dict | None:
    """Return {lib, pcell_name, params} for PCell variant cells; None
    otherwise. Used so that event payloads can fully describe how a
    PCell instance was parameterised, not just the baked geometry."""
    if cell is None:
        return None
    try:
        if not cell.is_pcell_variant():
            return None
    except Exception:
        return None
    info: dict = {}
    try:
        decl = cell.pcell_declaration()
        info["pcell_name"] = decl.name() if decl is not None else None
    except Exception:
        info["pcell_name"] = None
    try:
        lib = cell.library()
        info["lib"] = lib.name() if lib is not None else None
    except Exception:
        info["lib"] = None
    try:
        params = cell.pcell_parameters_by_name() or {}
        info["params"] = {str(k): _json_safe(v) for k, v in params.items()}
    except Exception:
        info["params"] = None
    return info


def _pcell_fp(cell) -> tuple | None:
    """Hashable version of _pcell_info for fingerprinting.

    Uses `_jsonsafe_to_hashable` (not `_hashable`) so that when
    `_inst_fp_to_dict` renders an event payload from a fingerprint it
    can reconstruct the ORIGINAL magic-dict shapes PCell params had
    (e.g. {"point_um": [-5, 0]} rather than (("point_um", (-5, 0)),)).
    Replay consumers feed that straight back to instance.insert_pcell."""
    d = _pcell_info(cell)
    if d is None:
        return None
    params = d.get("params") or {}
    return (
        d.get("lib"),
        d.get("pcell_name"),
        tuple(sorted((k, _jsonsafe_to_hashable(v)) for k, v in params.items())),
    )


_KLINK_DEBUG_TRANS = os.environ.get("KLINK_DEBUG_TRANS", "0") == "1"


def _inst_fingerprint(inst) -> tuple:
    """Full, hashable identity for a pya.Instance. Encodes target cell,
    placement transformation (position, rotation, mirror, magnification),
    array parameters, AND (for PCell variants) the PCell library / name
    / parameter set, so that changing a PCell's radius, an instance's
    rotation, or an array pitch is detected as a fresh fingerprint.

    Transformation is NORMALISED to (disp_dbu, angle_deg_float, mirror,
    mag). We read via `inst.cplx_trans` which per KLayout docs ALWAYS
    returns a pya.ICplxTrans regardless of how the instance was placed;
    its `.angle` is in degrees (float) and `.mag` is the magnification.
    Historically this code used `inst.trans` which in some 0.30.x builds
    silently returns a simple pya.Trans, whose `.angle` is a 0..3 90deg
    unit - reading that as if it were degrees produced very wrong
    rotations (a user's 45-degree rotation would come out as 45*90=4050).

    Opt-in debug: set env KLINK_DEBUG_TRANS=1 to dump the raw trans text
    for every fingerprinted instance so we can diagnose cases where the
    GUI *appears* to rotate a PCell but the rotation seems to be missing
    from the recording (it may be baked into the PCell handle params
    rather than into the placement transform)."""
    try:
        tcell = inst.cell
    except Exception:
        tcell = None
    try:
        target = tcell.name if tcell is not None else ""
    except Exception:
        target = ""
    disp, angle_deg, mirror, mag = (0, 0), 0.0, False, 1.0
    raw_trans_s, simple_trans_s, cplx_trans_s = "?", "?", "?"
    try:
        # `inst.trans` is what the KLayout docs now say always returns an
        # ICplxTrans, but we don't rely on that. cplx_trans is the API
        # guaranteed to give us a complex form we can read in degrees.
        try:
            ct = inst.cplx_trans
        except Exception:
            ct = None
        if ct is not None:
            disp = (int(ct.disp.x), int(ct.disp.y))
            angle_deg = float(ct.angle)
            mirror = bool(ct.is_mirror())
            mag = float(ct.mag)
            cplx_trans_s = ct.to_s()
        else:
            t = inst.trans
            if isinstance(t, pya.ICplxTrans):
                disp = (int(t.disp.x), int(t.disp.y))
                angle_deg = float(t.angle)
                mirror = bool(t.is_mirror())
                mag = float(t.mag)
            else:
                # pya.Trans: integer 90-deg stepped transform
                disp = (int(t.disp.x), int(t.disp.y))
                angle_deg = float(int(t.angle) * 90)
                mirror = bool(t.is_mirror())
                mag = 1.0
        if _KLINK_DEBUG_TRANS:
            try:
                raw_trans_s = inst.trans.to_s()
            except Exception:
                raw_trans_s = "<trans.to_s failed>"
            try:
                simple_trans_s = type(inst.trans).__name__
            except Exception:
                simple_trans_s = "?"
            print(
                f"[klink.debug_trans] target={target!r} "
                f"cplx_trans={cplx_trans_s!r} "
                f"inst.trans={raw_trans_s!r} "
                f"inst.trans_type={simple_trans_s} "
                f"-> disp={disp} angle_deg={angle_deg} "
                f"mirror={mirror} mag={mag}"
            )
    except Exception as e:
        if _KLINK_DEBUG_TRANS:
            print(f"[klink.debug_trans] target={target!r} FAILED: {e}")
    array = None
    try:
        if inst.is_regular_array():
            a = inst.a
            b = inst.b
            array = (int(inst.na), int(inst.nb),
                     int(a.x), int(a.y), int(b.x), int(b.y))
    except Exception as exc:
        _log.debug("swallowed in _inst_fingerprint: %s", exc)
    pcell = _pcell_fp(tcell)
    return ("inst", target, disp, angle_deg, mirror, mag, array, pcell)


def _inst_fp_to_dict(fp: tuple) -> dict:
    """Convert a fingerprint tuple to the event-payload dict shape.

    Forward-compatible: the tuple used to be 7 entries (no mag), so we
    tolerate old fingerprints. Current format is 8-tuple:
        ('inst', target, disp, angle_deg, mirror, mag, array, pcell)."""
    kind = fp[0]
    _ = kind
    target = fp[1]
    disp = fp[2]
    angle_deg = fp[3]
    mirror = fp[4]
    # Detect new vs old layout. New: index 5 is `mag` (number),
    # index 6 is `array`, index 7 is `pcell`. Old: index 5 is array.
    if len(fp) >= 8:
        mag = fp[5]
        array = fp[6]
        pcell_fp = fp[7]
    else:
        mag = 1.0
        array = fp[5] if len(fp) >= 6 else None
        pcell_fp = fp[6] if len(fp) >= 7 else None
    d: dict = {
        "target_cell": target,
        "trans_dbu": {
            "disp": [int(disp[0]), int(disp[1])],
            "angle": float(angle_deg),   # degrees (float)
            "mirror": bool(mirror),
            "mag": float(mag),
        },
    }
    if array is not None:
        na, nb, ax, ay, bx, by = array
        d["array"] = {
            "na": int(na), "nb": int(nb),
            "a_dbu": [int(ax), int(ay)],
            "b_dbu": [int(bx), int(by)],
        }
    if pcell_fp is not None:
        lib, pname, params = pcell_fp
        # `params` is a tuple of (key, _jsonsafe_to_hashable(value))
        # pairs. Reverse the tagged hashable so each value is back to a
        # dict/list/primitive - same shape the server adapter accepts.
        d["pcell"] = {
            "lib": lib,
            "pcell_name": pname,
            "params": {k: _hashable_to_jsonsafe(v) for k, v in params}
                      if params else {},
        }
    return d


def _summarise_selection(view, max_items: int = 10) -> dict:
    """JSON-safe summary of view.each_object_selected().

    Each entry carries enough info for an LLM to uniquely identify the
    object:
      shape    -> cell, layer_index + layer/datatype, shape_type, full
                  geometry (points_dbu etc., same shape as shapes_changed)
      instance -> cell, target_cell, trans_dbu (displacement + rotation/mirror),
                  bbox_dbu, a/b/na/nb for array instances
    """
    items: list = []
    n = 0
    truncated = False
    try:
        cv_index = -1
        ly = None
        try:
            cv_index = view.active_cellview_index
            cv = view.cellview(cv_index)
            if cv is not None and cv.is_valid():
                ly = cv.layout()
        except Exception:
            ly = None

        for obj in view.each_object_selected():
            n += 1
            if len(items) >= max_items:
                truncated = True
                continue
            entry: dict = {}
            try:
                c = obj.cell()
                entry["cell"] = c.name if c is not None else None
            except Exception:
                entry["cell"] = None
            entry["is_cell_inst"] = bool(obj.is_cell_inst())

            if obj.is_cell_inst():
                entry["kind"] = "instance"
                try:
                    inst = obj.inst()
                    try:
                        entry["target_cell"] = inst.cell.name if inst.cell is not None else None
                    except Exception as exc:
                        _log.debug("swallowed in _summarise_selection: %s", exc)
                    try:
                        bb = inst.bbox()
                        if not bb.empty():
                            entry["bbox_dbu"] = [bb.left, bb.bottom, bb.right, bb.top]
                    except Exception as exc:
                        _log.debug("swallowed in _summarise_selection: %s", exc)
                    # Transformation (placement + rotation/mirror). This is
                    # what uniquely identifies the placement vs. another
                    # instance of the same cell.
                    try:
                        t = inst.trans
                        entry["trans_dbu"] = {
                            "disp": [int(t.disp.x), int(t.disp.y)],
                            "angle": int(t.angle),
                            "mirror": bool(t.is_mirror()),
                        }
                    except Exception as exc:
                        _log.debug("swallowed in _summarise_selection: %s", exc)
                    # Array instance parameters (na=nb=1 for a single instance).
                    try:
                        if inst.is_regular_array():
                            a = inst.a
                            b = inst.b
                            entry["array"] = {
                                "na": int(inst.na), "nb": int(inst.nb),
                                "a_dbu": [int(a.x), int(a.y)],
                                "b_dbu": [int(b.x), int(b.y)],
                            }
                    except Exception as exc:
                        _log.debug("swallowed in _summarise_selection: %s", exc)
                    # PCell metadata (library, pcell name, params) so an
                    # LLM can reconstruct parametric cells, not just
                    # their baked geometry.
                    try:
                        pinfo = _pcell_info(inst.cell)
                        if pinfo is not None:
                            entry["pcell"] = pinfo
                    except Exception as exc:
                        _log.debug("swallowed in _summarise_selection: %s", exc)
                except Exception as exc:
                    _log.debug("swallowed in _summarise_selection: %s", exc)
            else:
                entry["kind"] = "shape"
                try:
                    entry["layer_index"] = obj.layer
                    # Expose layer / datatype too, so the LLM does not have
                    # to cross-reference a separate layers list.
                    if ly is not None:
                        try:
                            linfo = ly.get_info(obj.layer)
                            if linfo is not None:
                                entry["layer"] = int(linfo.layer)
                                entry["datatype"] = int(linfo.datatype)
                                if linfo.name:
                                    entry["name"] = linfo.name
                        except Exception as exc:
                            _log.debug("swallowed in _summarise_selection: %s", exc)
                    sh = obj.shape
                    fp = _fingerprint(sh)
                    geom = _fp_to_dict(fp)
                    # geom = {"type": "...", "points_dbu"/"bbox_dbu"/..., ...}
                    entry["shape_type"] = geom.get("type")
                    # copy the rest of the geometry fields (points_dbu,
                    # bbox_dbu, width_dbu, position_dbu, string, ...)
                    for k, v in geom.items():
                        if k != "type":
                            entry[k] = v
                except Exception as exc:
                    _log.debug("swallowed in _summarise_selection: %s", exc)
            items.append(entry)
    except Exception as e:
        return {"count": n, "error": str(e), "items": items, "truncated": truncated}
    return {"count": n, "truncated": truncated, "items": items}


def _capture_cause() -> dict | None:
    """Snapshot the dispatcher's currently-executing RPC, if any.

    Imported lazily to avoid a signals<->dispatcher import cycle (the
    dispatcher module constructs a SignalHub indirectly via server).
    Returns a dict with request_id / method / trace_id / conn_id, or
    None when called outside of an RPC dispatch (e.g. pure user GUI
    activity). Any failure falls back to None so a broken dispatcher
    never brings down signal emission."""
    try:
        from .dispatcher import current_request
        return current_request()
    except Exception:
        return None


def _bind(obj, attr: str, handler) -> str:
    """
    Attach `handler` to `obj.<attr>`. pya events implement __iadd__
    (in-place add) but NOT __add__, so we must use operator.iadd which
    tries __iadd__ first. Returns the binding form that succeeded.
    """
    if not hasattr(obj, attr):
        return "failed:no-attr"

    # Form A: the real `+=` semantics via operator.iadd
    try:
        ev = getattr(obj, attr)
        new_ev = operator.iadd(ev, handler)
        setattr(obj, attr, new_ev)
        return "iadd"
    except Exception as ex_a:
        form_a_err = ex_a

    return f"failed:A={type(form_a_err).__name__}({form_a_err})"


class SignalHub:
    _VIEWPORT_MIN_INTERVAL_MS = 150
    # Coalesce rapid-fire on_layer_list_changed / piggyback triggers: a
    # single user action (or batched RPC) typically fires KLayout's
    # invalidation several times in quick succession. Deferring the
    # expensive full snapshot+diff into one pass per window cuts the
    # per-edit cost noticeably on large layouts. 50ms is well below
    # human-perceivable latency but wide enough to absorb a TCP-paced
    # RPC batch (round trip ~1-5ms on localhost).
    _DIFF_DEBOUNCE_MS = 50

    def __init__(self, server):
        self.server = server
        self._mw = pya.Application.instance().main_window()
        self._view = None
        self._last_viewport_emit = 0.0
        self._bound_view_ids: set = set()
        # Count how many times each handler actually fires - helpful to
        # distinguish "event bound but never triggered" from "never bound".
        self.fire_counts: dict = {}
        # Debounced diff state. `_diff_timer` is a lazy pya.QTimer; we
        # can't use pya.QTimer.singleShot() as a static method in 0.30.x
        # (it's exposed as a getset_descriptor, see connection.py). So
        # we keep an instance timer and restart it when more triggers
        # arrive before it fires.
        self._diff_timer = None
        self._diff_pending: bool = False
        # Each entry: {"source": str, "cause": {request_id, method, ...} | None}
        # `cause` is snapshotted at schedule time because the diff runs
        # AFTER the RPC's dispatcher frame has popped off the request
        # stack, so we can't fetch it lazily at fire time.
        self._diff_sources: list = []
        self._diff_timer_usable: bool = True
        # Snapshot of the last observed layer list, per view id. Used to
        # decide whether on_layer_list_changed is a genuine layer-set
        # change or just a redraw hint from a shape edit.
        self._layer_snapshot: dict = {}
        # Per-cell / per-layer shape snapshot. Structure:
        #   view_id -> {cell_index: {layer_index: {count, bbox, fps}}}
        # so that shape edits in any sub-cell are detected, not just TOP.
        self._shape_snapshot: dict = {}
        # Per-cell instance snapshot. Structure:
        #   view_id -> {cell_index: Counter({inst_fp: count})}
        # Detects instance additions / removals / transformation changes.
        self._inst_snapshot: dict = {}
        # Layout-level cell list snapshot. Structure:
        #   view_id -> {cell_index: cell_name}
        # Detects cell creation, deletion and rename.
        self._cell_snapshot: dict = {}
        # Track which layout objects we've already hooked hier_changed on.
        self._bound_layout_ids: set = set()

        if self._mw is None:
            print("[klink] SignalHub: no main window, signals disabled")
            return

        # Keep a detailed diagnostic log that can be fetched via
        # meta.debug_signals (see methods/meta_m.py).
        self.diagnostic: list = []

        res = _bind(self._mw, "on_current_view_changed", self._on_current_view_changed)
        self.diagnostic.append(f"mw.on_current_view_changed -> {res}")
        print(f"[klink] bind mw.on_current_view_changed -> {res}")

        try:
            self._rebind_view(self._mw.current_view())
        except Exception as e:
            print(f"[klink] initial view bind failed: {e}")
            self.diagnostic.append(f"initial view bind error: {e}")

    # ------------------------------------------------------------------
    def _on_current_view_changed(self, *args):
        try:
            new_view = self._mw.current_view()
        except Exception:
            new_view = None
        self._rebind_view(new_view)
        self._emit("cellview_changed", {
            "active_cellview": self._active_cellview_index(),
        })
        # When the current view changes (e.g. after load_layout opens a
        # new tab), the old view's on_cellviews_changed handler already
        # bailed via _is_current(), and on_layer_list_changed / hier_changed
        # won't fire for the new view because the data was loaded BEFORE
        # our handlers were bound. Schedule a full diff so the recorder
        # (and any external subscriber) sees the fresh cells/shapes/etc.
        self._schedule_diff(source="current_view_changed")

    def _rebind_view(self, view):
        """Mark `view` as current. If we haven't hooked its events yet,
        do so now. Handlers guard against firing for a stale view via
        `_is_current`."""
        self._view = view
        if view is None:
            self.diagnostic.append("rebind: view=None (no active view yet)")
            return

        vid = id(view)
        if vid in self._bound_view_ids:
            self.diagnostic.append(f"rebind: view[{vid}] already bound")
            return

        bound_any = False
        for attr in _VIEW_EVENTS:
            handler = getattr(self, "_handler_for_" + attr, None)
            if handler is None:
                continue
            res = _bind(view, attr, handler)
            line = f"view[{vid}].{attr} -> {res}"
            self.diagnostic.append(line)
            print(f"[klink] bind {line}")
            if res == "iadd":
                bound_any = True
        if bound_any:
            self._bound_view_ids.add(vid)

        # Also hook the active Layout's hier_changed event. shape edits
        # already come through on_layer_list_changed (KLayout refreshes
        # the layer cache on every edit), but hier_changed is the real
        # source of truth for cell creation/deletion and instance edits,
        # which may not touch any layer cache at all.
        self._bind_layout_signals()

    # ------------------------------------------------------------------
    # Handler dispatch: `_handler_for_on_selection_changed` etc.
    # Each guards against being called for a stale (old) view.
    # ------------------------------------------------------------------
    def _is_current(self) -> bool:
        try:
            return self._mw.current_view() is self._view and self._view is not None
        except Exception:
            return False

    def _bump(self, channel: str) -> None:
        self.fire_counts[channel] = self.fire_counts.get(channel, 0) + 1

    def _handler_for_on_selection_changed(self, *args):
        self._bump("selection_changed")
        if not self._is_current():
            return
        try:
            summary = _summarise_selection(self._view, max_items=10)
            self._emit("selection_changed", summary)
        except Exception as e:
            print(f"[klink] selection_changed handler error: {e}")
        # This build of pya does not expose Layout-level hier_changed,
        # so "new cell" / "delete cell" without a layer or active-view
        # change would otherwise wait for the next layer edit to surface.
        # Piggybacking a diff on selection_changed means that any click
        # after a silent hier edit makes cells_changed / instances_changed
        # appear within one user action. Debounced so rapid clicks don't
        # run N full diffs.
        self._schedule_diff(source="selection_changed")

    def _handler_for_on_viewport_changed(self, *args):
        self._bump("viewport_changed")
        if not self._is_current():
            return
        now = time.monotonic() * 1000.0
        if now - self._last_viewport_emit < self._VIEWPORT_MIN_INTERVAL_MS:
            return
        self._last_viewport_emit = now
        data: dict = {}
        try:
            # view.box() returns a DBox in MICRONS. Historically these
            # micron floats were stored under "bbox_dbu" (a lying field
            # name, CODE_REVIEW_ISSUES 4.1). Emit both units honestly.
            bb = self._view.box()
            data["bbox_um"] = [bb.left, bb.bottom, bb.right, bb.top]
            try:
                cv = self._view.active_cellview()
                dbu = float(cv.layout().dbu) if cv is not None and cv.is_valid() else None
            except Exception:
                dbu = None
            if dbu:
                data["bbox_dbu"] = [
                    int(round(bb.left / dbu)), int(round(bb.bottom / dbu)),
                    int(round(bb.right / dbu)), int(round(bb.top / dbu)),
                ]
        except Exception as exc:
            _log.debug("swallowed in _handler_for_on_viewport_changed: %s", exc)
        try:
            data["width_px"] = int(self._view.viewport_width())
            data["height_px"] = int(self._view.viewport_height())
        except Exception as exc:
            _log.debug("swallowed in _handler_for_on_viewport_changed: %s", exc)
        self._emit("viewport_changed", data)

    def _handler_for_on_layer_list_changed(self, *args):
        """Layer-cache refresh. Schedules a debounced full snapshot+diff
        (cells, shapes, instances) since KLayout fires this on almost
        every edit that touches a layer - usually multiple times per
        user action. hier_changed (bound separately) covers edits that
        don't touch layers, e.g. pure instance moves (when the build
        exposes that event - many 0.30.x builds don't)."""
        self._bump("on_layer_list_changed_raw")
        if not self._is_current():
            return
        self._schedule_diff(source="layer_list_changed")

    def _handler_for_on_hier_changed(self, *args):
        """Layout.hier_changed fires on cell creation/deletion and on
        instance edits. We forward to the same debounced diff pipeline."""
        self._bump("on_hier_changed_raw")
        if not self._is_current():
            return
        self._schedule_diff(source="hier_changed")

    # Budget: skip per-shape fingerprinting if a single (cell, layer)
    # pair has more than this many shapes, since we touch it on every
    # layer_list_changed / hier_changed tick. Falls back to (count, bbox).
    _FINGERPRINT_MAX_SHAPES = 2000
    # Max cells to walk per diff pass. For human-scale designs that is
    # effectively unlimited; for huge PDK-laden layouts we cap it.
    _MAX_CELLS_PER_DIFF = 500

    def _do_full_diff(self, source: str, caused_by: list | None = None) -> None:
        """One pass: refresh layer / cell / shape / instance snapshots
        and emit any of layer_list_changed / cells_changed / shapes_changed
        / instances_changed as appropriate.

        `caused_by` (optional) is forwarded to every emitted event so the
        client can correlate the event back to the RPC(s) that triggered
        it. If None, falls back to the currently-dispatching RPC (if any)
        at emit time."""
        try:
            layers = self._snapshot_layers()
        except Exception:
            layers = []

        vid = id(self._view)
        prev_layers = self._layer_snapshot.get(vid)
        self._layer_snapshot[vid] = layers
        layer_set_changed = (prev_layers is None) or (prev_layers != layers)

        ly = self._active_layout()
        prev_cells = self._cell_snapshot.get(vid, {})
        curr_cells = self._snapshot_cells(ly)
        self._cell_snapshot[vid] = curr_cells
        cells_added, cells_removed, cells_renamed, pcell_changed = \
            self._diff_cells(prev_cells, curr_cells)

        # Downstream helpers just need {cell_index: name}, so build a
        # thin projection once.
        cell_names = {ci: e.get("name") for ci, e in curr_cells.items()}

        prev_shape_snap = self._shape_snapshot.get(vid, {})
        curr_shape_snap = self._snapshot_shapes_all(ly, layers, cell_names)
        self._shape_snapshot[vid] = curr_shape_snap
        changed_layers = self._diff_shape_snap(prev_shape_snap, curr_shape_snap,
                                               layers, cell_names)

        prev_inst_snap = self._inst_snapshot.get(vid, {})
        curr_inst_snap = self._snapshot_instances(ly, cell_names)
        self._inst_snapshot[vid] = curr_inst_snap
        changed_insts = self._diff_inst_snap(prev_inst_snap, curr_inst_snap, cell_names)

        if layer_set_changed:
            self._bump("layer_list_changed")
            self._emit("layer_list_changed", {
                "layers": layers,
                "count": len(layers),
            }, caused_by=caused_by)

        if cells_added or cells_removed or cells_renamed or pcell_changed:
            self._bump("cells_changed")
            payload: dict = {
                "cell_count": len(curr_cells),
                "added": cells_added,
                "removed": cells_removed,
                "renamed": cells_renamed,
                "reason": source,
            }
            if pcell_changed:
                payload["pcell_changed"] = pcell_changed
            self._emit("cells_changed", payload, caused_by=caused_by)

        if changed_layers:
            self._bump("shapes_changed")
            self._emit("shapes_changed", {
                "layer_count": len(layers),
                "changed_layers": changed_layers,
                "reason": source,
            }, caused_by=caused_by)
        elif source == "layer_list_changed" and not layer_set_changed:
            self._bump("shapes_changed_suppressed")

        if changed_insts:
            self._bump("instances_changed")
            self._emit("instances_changed", {
                "changed_cells": changed_insts,
                "reason": source,
            }, caused_by=caused_by)

    # ------------------------------------------------------------------
    # Debounced diff scheduling
    # ------------------------------------------------------------------
    def _schedule_diff(self, source: str) -> None:
        """Record that a diff is needed and arm the debounce timer.

        Subsequent calls within `_DIFF_DEBOUNCE_MS` only append their
        source to the pending list; they do NOT restart the timer, so
        the window from the first trigger bounds the worst-case latency.
        If QTimer is unavailable for any reason we fall back to a
        synchronous diff (old behaviour), keeping this change safe for
        builds where pya.QTimer misbehaves.

        Also snapshots the currently-dispatching RPC (if any) so the
        eventual diff events can carry a `caused_by` credit.
        """
        cause = _capture_cause()
        self._diff_sources.append({"source": source, "cause": cause})
        if self._diff_pending:
            return
        if not self._diff_timer_usable:
            # Fallback path: no debounce available, run immediately.
            self._run_pending_diff()
            return

        timer = self._diff_timer
        if timer is None:
            try:
                timer = pya.QTimer()
                timer.setSingleShot(True)
                # pya events use += not .connect; same pattern as
                # view events elsewhere in this file.
                timer.timeout += self._on_diff_timer_fired
                self._diff_timer = timer
                try:
                    self.diagnostic.append("diff_timer: pya.QTimer created (debounce on)")
                except Exception as exc:
                    _log.debug("swallowed in _schedule_diff: %s", exc)
            except Exception as e:
                self._diff_timer_usable = False
                try:
                    self.diagnostic.append(
                        f"diff_timer: create failed ({type(e).__name__}: {e}); "
                        "falling back to synchronous diff"
                    )
                except Exception as exc:
                    _log.debug("swallowed in _schedule_diff: %s", exc)
                self._run_pending_diff()
                return
        try:
            timer.start(self._DIFF_DEBOUNCE_MS)
            self._diff_pending = True
        except Exception as e:
            self._diff_timer_usable = False
            try:
                self.diagnostic.append(
                    f"diff_timer.start failed ({type(e).__name__}: {e}); "
                    "falling back to synchronous diff"
                )
            except Exception as exc:
                _log.debug("swallowed in _schedule_diff: %s", exc)
            self._run_pending_diff()

    def _on_diff_timer_fired(self, *args):
        self._diff_pending = False
        self._run_pending_diff()

    def _run_pending_diff(self) -> None:
        """Drain the pending sources list and run one diff pass."""
        if not self._diff_sources:
            return
        entries = self._diff_sources
        self._diff_sources = []

        src_strs = [e["source"] for e in entries]
        if len(src_strs) == 1:
            src = src_strs[0]
        else:
            from collections import Counter
            counts = Counter(src_strs)
            parts = [f"{s}x{n}" if n > 1 else s for s, n in counts.items()]
            src = "+".join(parts)

        # Dedupe causers: multiple signal ticks from the same RPC
        # collapse to one entry in `caused_by`. Ordering preserved
        # (insertion order) so the first RPC to trigger appears first.
        seen: set = set()
        caused_by: list = []
        for e in entries:
            c = e.get("cause")
            if not c:
                continue
            key = (c.get("conn_id"), c.get("request_id"))
            if key in seen:
                continue
            seen.add(key)
            caused_by.append(c)

        self._bump("diff_runs")
        try:
            self._do_full_diff(source=src, caused_by=caused_by or None)
        except Exception as e:
            print(f"[klink] debounced diff error: {e}")

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------
    def _snapshot_cells(self, ly) -> dict:
        """{cell_index: {"name": str, "pcell": {...}|None}} for the
        active layout. PCell metadata lets the LLM reconstruct
        parametric cells rather than just their baked geometry."""
        snap: dict = {}
        if ly is None:
            return snap
        try:
            for c in ly.each_cell():
                try:
                    ci = int(c.cell_index())
                except Exception:
                    continue
                entry: dict = {"name": c.name}
                try:
                    pinfo = _pcell_info(c)
                    if pinfo is not None:
                        entry["pcell"] = pinfo
                except Exception as exc:
                    _log.debug("swallowed in _snapshot_cells: %s", exc)
                snap[ci] = entry
        except Exception as exc:
            _log.debug("swallowed in _snapshot_cells: %s", exc)
        return snap

    def _snapshot_shapes_all(self, ly, layers, cells) -> dict:
        """{cell_index: {"_pcell": bool, layer_index: {count, bbox, fps}}}
        across all cells. Uses per-(cell, layer) fingerprinting with a
        budget so huge PDK cells don't dominate CPU. The "_pcell" flag
        lets diff emit `pcell_derived: true` on shapes belonging to a
        PCell variant cell - those are not user-drawn geometry but
        PCell evaluation artefacts and should not be replayed."""
        snap: dict = {}
        if ly is None or not cells:
            return snap

        cell_ids = list(cells.keys())
        if len(cell_ids) > self._MAX_CELLS_PER_DIFF:
            cell_ids = cell_ids[: self._MAX_CELLS_PER_DIFF]

        layer_idxs = [e["index"] for e in layers]
        for ci in cell_ids:
            try:
                cell = ly.cell(ci)
            except Exception:
                continue
            if cell is None:
                continue
            try:
                is_pcell = bool(cell.is_pcell_variant())
            except Exception:
                is_pcell = False
            per_layer: dict = {"_pcell": is_pcell}
            for idx in layer_idxs:
                try:
                    shapes = cell.shapes(idx)
                except Exception:
                    continue
                try:
                    n = shapes.size()
                except Exception:
                    n = -1
                if n == 0:
                    continue
                try:
                    bb = shapes.bbox()
                    bb_tup = (bb.left, bb.bottom, bb.right, bb.top) if not bb.empty() else None
                except Exception:
                    bb_tup = None
                fps = None
                if 0 <= n <= self._FINGERPRINT_MAX_SHAPES:
                    fps = []
                    try:
                        for s in shapes.each():
                            fps.append(_fingerprint(s))
                    except Exception:
                        fps = None
                per_layer[idx] = {"count": n, "bbox": bb_tup, "fps": fps}
            # Only keep this cell in the snapshot if it actually has
            # shapes on some layer - "_pcell" alone is not enough.
            if any(k != "_pcell" for k in per_layer):
                snap[ci] = per_layer
        return snap

    def _snapshot_instances(self, ly, cells) -> dict:
        """{cell_index: Counter({inst_fp: count})} across all cells."""
        from collections import Counter
        snap: dict = {}
        if ly is None or not cells:
            return snap

        cell_ids = list(cells.keys())
        if len(cell_ids) > self._MAX_CELLS_PER_DIFF:
            cell_ids = cell_ids[: self._MAX_CELLS_PER_DIFF]

        for ci in cell_ids:
            try:
                cell = ly.cell(ci)
            except Exception:
                continue
            if cell is None:
                continue
            fps = []
            try:
                for inst in cell.each_inst():
                    fps.append(_inst_fingerprint(inst))
            except Exception:
                continue
            if fps:
                snap[ci] = Counter(fps)
        return snap

    # ------------------------------------------------------------------
    # Diffs
    # ------------------------------------------------------------------
    def _diff_cells(self, prev: dict, curr: dict):
        """Return (added, removed, renamed, pcell_changed) lists.

        Each cell entry is `{name, pcell?}`. We compare full entries so
        that editing a PCell's parameters (same cell_index, same name,
        different params) surfaces as a pcell_changed event.

        When prev is empty (first diff on a brand-new view, e.g. after a
        file load during recording), all cells in curr are reported as
        added so the recorder can emit _ensure_cell() calls for them."""
        added: list = []
        removed: list = []
        renamed: list = []
        pcell_changed: list = []
        if not prev:
            for ci, e in sorted(curr.items()):
                entry = {"cell_index": ci, "name": e.get("name")}
                if e.get("pcell") is not None:
                    entry["pcell"] = e["pcell"]
                added.append(entry)
            return added, removed, renamed, pcell_changed
        prev_ids = set(prev.keys())
        curr_ids = set(curr.keys())
        for ci in sorted(curr_ids - prev_ids):
            e = curr[ci]
            entry = {"cell_index": ci, "name": e.get("name")}
            if e.get("pcell") is not None:
                entry["pcell"] = e["pcell"]
            added.append(entry)
        for ci in sorted(prev_ids - curr_ids):
            e = prev[ci]
            entry = {"cell_index": ci, "name": e.get("name")}
            if e.get("pcell") is not None:
                entry["pcell"] = e["pcell"]
            removed.append(entry)
        for ci in sorted(prev_ids & curr_ids):
            p, c = prev[ci], curr[ci]
            if p.get("name") != c.get("name"):
                renamed.append({"cell_index": ci,
                                "old_name": p.get("name"),
                                "new_name": c.get("name")})
            if p.get("pcell") != c.get("pcell"):
                pcell_changed.append({
                    "cell_index": ci,
                    "name": c.get("name"),
                    "old_pcell": p.get("pcell"),
                    "new_pcell": c.get("pcell"),
                })
        return added, removed, renamed, pcell_changed

    def _diff_shape_snap(self, prev: dict, curr: dict, layers_info, cells):
        """Diff per (cell, layer). Returns list of entries; each entry
        carries cell + layer identification plus added/removed samples."""
        from collections import Counter

        info_by_idx = {e["index"]: e for e in layers_info}
        changed: list = []

        all_cell_ids = set(prev.keys()) | set(curr.keys())
        for ci in sorted(all_cell_ids):
            pl = prev.get(ci, {})
            cl = curr.get(ci, {})
            # "_pcell" is a synthetic flag, not a layer_index; exclude it.
            is_pcell_variant = bool(cl.get("_pcell") or pl.get("_pcell"))
            layer_ids = (set(pl.keys()) | set(cl.keys())) - {"_pcell"}
            for idx in sorted(layer_ids):
                p = pl.get(idx)
                c = cl.get(idx)

                if p is None and c is not None:
                    if c["count"] <= 0:
                        continue
                    count_before = 0
                    count_after = c["count"]
                    bbox = c["bbox"]
                    added_fps = list(c["fps"]) if c["fps"] is not None else []
                    removed_fps: list = []
                elif c is None and p is not None:
                    if p["count"] <= 0:
                        continue
                    count_before = p["count"]
                    count_after = 0
                    bbox = None
                    added_fps = []
                    removed_fps = list(p["fps"]) if p["fps"] is not None else []
                elif p == c:
                    continue
                else:
                    count_before = p["count"]
                    count_after = c["count"]
                    bbox = c["bbox"]
                    if p["fps"] is not None and c["fps"] is not None:
                        a = Counter(c["fps"])
                        b = Counter(p["fps"])
                        added_fps = list((a - b).elements())
                        removed_fps = list((b - a).elements())
                    else:
                        added_fps = []
                        removed_fps = []

                delta = count_after - count_before
                if (delta == 0 and p is not None and c is not None
                        and p["bbox"] == c["bbox"]
                        and not added_fps and not removed_fps):
                    continue

                info = info_by_idx.get(idx, {"layer": None, "datatype": None, "name": None})
                entry: dict = {
                    "cell": cells.get(ci),
                    "cell_index": ci,
                    "layer_index": idx,
                    "layer": info.get("layer"),
                    "datatype": info.get("datatype"),
                    "name": info.get("name"),
                    "count_before": count_before,
                    "count_after": count_after,
                    "delta": delta,
                }
                # Shapes inside a PCell variant cell are produced by the
                # PCell's declaration from its parameters, not by a user
                # `shapes.insert(...)` call. An LLM reconstructing a
                # script should skip these and just recreate the PCell
                # declaration (see cells_changed.added[].pcell). So we
                # keep the counts but strip out the verbose points /
                # bboxes to avoid flooding the event stream with big
                # auto-generated polygons (think 64-point circles).
                if is_pcell_variant:
                    entry["pcell_derived"] = True
                    # Intentionally do NOT attach bbox_dbu / added / removed
                    # for pcell-derived layers. Consumers that want the
                    # baked geometry can still query it via shape.query.
                    changed.append(entry)
                    continue
                if bbox is not None:
                    entry["bbox_dbu"] = list(bbox)
                # Historically capped at 5 entries per diff to keep
                # event payloads small, but that silently dropped user
                # edits when >5 shapes landed in one debounce window
                # (e.g. 9 text labels inserted back-to-back by a script).
                # Since the macro recorder must see every add/remove to
                # produce a faithful replay script, we lift the cap to a
                # ceiling that still protects against pathological batches
                # (paste a 100k-shape polygon set) but will not truncate
                # normal GUI/RPC activity.
                _CAP = 10000
                if added_fps:
                    entry["added"] = [_fp_to_dict(fp) for fp in added_fps[:_CAP]]
                    if len(added_fps) > _CAP:
                        entry["added_truncated"] = len(added_fps) - _CAP
                if removed_fps:
                    entry["removed"] = [_fp_to_dict(fp) for fp in removed_fps[:_CAP]]
                    if len(removed_fps) > _CAP:
                        entry["removed_truncated"] = len(removed_fps) - _CAP
                changed.append(entry)

        return changed

    def _diff_inst_snap(self, prev: dict, curr: dict, cells):
        """Diff per parent-cell. Returns list of {cell, cell_index,
        count_before, count_after, delta, added[], removed[]}."""
        changed: list = []
        all_cell_ids = set(prev.keys()) | set(curr.keys())
        for ci in sorted(all_cell_ids):
            p = prev.get(ci)
            c = curr.get(ci)
            if p == c:
                continue
            if p is None:
                # Freshly observed cell with instances already in it is
                # typically a newly created reference; still emit it so
                # the LLM sees the initial wiring.
                added_fps = list(c.elements()) if c is not None else []
                removed_fps: list = []
                count_before = 0
                count_after = sum(c.values()) if c is not None else 0
            elif c is None:
                added_fps = []
                removed_fps = list(p.elements())
                count_before = sum(p.values())
                count_after = 0
            else:
                added_fps = list((c - p).elements())
                removed_fps = list((p - c).elements())
                count_before = sum(p.values())
                count_after = sum(c.values())

            delta = count_after - count_before
            if not added_fps and not removed_fps:
                continue
            entry: dict = {
                "cell": cells.get(ci),
                "cell_index": ci,
                "count_before": count_before,
                "count_after": count_after,
                "delta": delta,
            }
            # Same rationale as _diff_shape_snap: cap high enough that
            # realistic batches survive untruncated so the recorder can
            # reproduce every placement.
            _CAP = 10000
            if added_fps:
                entry["added"] = [_inst_fp_to_dict(fp) for fp in added_fps[:_CAP]]
                if len(added_fps) > _CAP:
                    entry["added_truncated"] = len(added_fps) - _CAP
            if removed_fps:
                entry["removed"] = [_inst_fp_to_dict(fp) for fp in removed_fps[:_CAP]]
                if len(removed_fps) > _CAP:
                    entry["removed_truncated"] = len(removed_fps) - _CAP
            changed.append(entry)
        return changed

    def _active_layout(self):
        try:
            cv = self._view.active_cellview()
            if cv is not None and cv.is_valid():
                return cv.layout()
        except Exception as exc:
            _log.debug("swallowed in _active_layout: %s", exc)
        return None

    # pya's Layout exposes events as "<name>_event" (no 'on_' prefix,
    # unlike LayoutView). Try the documented name first, then a few
    # fallbacks in case of version skew.
    _LAYOUT_HIER_CANDIDATES = (
        "hier_changed_event",
        "on_hier_changed",
        "hier_changed",
    )

    def _bind_layout_signals(self):
        ly = self._active_layout()
        if ly is None:
            return
        lid = id(ly)
        if lid in self._bound_layout_ids:
            return

        bound = False
        for attr in self._LAYOUT_HIER_CANDIDATES:
            if not hasattr(ly, attr):
                self.diagnostic.append(f"layout[{lid}].{attr} -> no-attr")
                continue
            res = _bind(ly, attr, self._handler_for_on_hier_changed)
            line = f"layout[{lid}].{attr} -> {res}"
            self.diagnostic.append(line)
            print(f"[klink] bind {line}")
            if res == "iadd":
                bound = True
                break

        # Also log which event-ish attributes this Layout actually has,
        # so future mismatches are easy to spot in meta.debug_signals.
        try:
            evs = [n for n in dir(ly) if "event" in n.lower() or "changed" in n.lower()]
            self.diagnostic.append(f"layout[{lid}] event attrs: {evs}")
        except Exception as exc:
            _log.debug("swallowed in _bind_layout_signals: %s", exc)

        if bound:
            self._bound_layout_ids.add(lid)

    def _snapshot_layers(self):
        """Stable, comparable summary of the current layer set."""
        view = self._view
        if view is None:
            return []
        cv = view.active_cellview()
        if cv is None or cv.cell is None:
            return []
        ly = cv.layout()
        out = []
        for idx in ly.layer_indexes():
            info = ly.get_info(idx)
            out.append({
                "index": idx,
                "layer": info.layer,
                "datatype": info.datatype,
                "name": info.name or None,
            })
        return out

    def _handler_for_on_cellviews_changed(self, *args):
        self._bump("cellview_changed")
        if not self._is_current():
            return
        # A new cellview means a (possibly new) Layout object is active.
        # Hook hier_changed on it too.
        self._bind_layout_signals()
        self._emit("cellview_changed", {
            "active_cellview": self._active_cellview_index(),
            "cell": self._active_cell_name(),
            "reason": "cellviews_changed",
        })
        # pya's Layout does not expose hier_changed_event in this build,
        # so on_layer_list_changed is our only passive trigger for
        # shape/instance edits. Creating a new cell often fires
        # cellviews_changed (the user activates the new cell), so
        # piggyback a full diff here to surface new cells promptly.
        self._schedule_diff(source="cellviews_changed")

    def _handler_for_on_active_cellview_changed(self, *args):
        self._bump("cellview_changed")
        if not self._is_current():
            return
        self._bind_layout_signals()
        self._emit("cellview_changed", {
            "active_cellview": self._active_cellview_index(),
            "cell": self._active_cell_name(),
            "reason": "active_cellview_changed",
        })
        self._schedule_diff(source="active_cellview_changed")

    def _active_cell_name(self):
        try:
            cv = self._view.active_cellview()
            if cv is not None and cv.cell is not None:
                return cv.cell.name
        except Exception as exc:
            _log.debug("swallowed in _active_cell_name: %s", exc)
        return None

    # ------------------------------------------------------------------
    def _active_cellview_index(self):
        try:
            return self._view.active_cellview_index if self._view is not None else None
        except Exception:
            return None

    def _emit(self, channel: str, data: dict, caused_by: list | None = None) -> None:
        """Broadcast `data` on `channel`. Attaches a `caused_by` field
        naming the RPC(s) responsible:
          * If `caused_by` is provided (debounced diff path), use it.
          * Otherwise look at the dispatcher's current request stack -
            this covers events that fire synchronously during an RPC
            handler (e.g. selection.set_box -> on_selection_changed).
          * If neither applies (pure user GUI action), the field is
            omitted so the payload shape stays minimal."""
        if caused_by is None:
            live = _capture_cause()
            if live is not None:
                caused_by = [live]
        if caused_by:
            # Never mutate the caller's dict; copy to local.
            data = dict(data)
            data["caused_by"] = caused_by
        # Tee into the recorder BEFORE external broadcast. Recorder is a
        # passive sink and errors never bubble back into the pipeline.
        try:
            from .recorder import instance as _rec_instance
            rec = _rec_instance()
            if rec.is_recording():
                rec.ingest(channel, data)
        except Exception as e:
            print(f"[klink] recorder.ingest {channel!r} failed: {e}")
        try:
            self.server.events.emit(channel, data)
        except Exception as e:
            print(f"[klink] emit {channel!r} failed: {e}")

    def shutdown(self):
        # We don't try to detach handlers; the self._view=None guard
        # makes any late-firing event a no-op.
        self._view = None
        self._bound_view_ids.clear()
        self._bound_layout_ids.clear()
        # Stop the debounce timer so it cannot fire into a dead hub.
        try:
            if self._diff_timer is not None:
                self._diff_timer.stop()
        except Exception as exc:
            _log.debug("swallowed in shutdown: %s", exc)
        self._diff_pending = False
        self._diff_sources = []

"""Thin Region PCell RPC methods (Intent Region marker family).

KLayout-side Region CRUD only: claim (rulers -> Region PCell), claim_preview
(no-mutation candidate listing + dry-run), list, get, unclaim, set_layer.
Rulers are read as box / ellipse (2 points) or exact polygon (3+ points).
Boolean composition, polygon validation and safe ellipse discretization are
pure functions in ``klink_server.region_geom`` (offline-testable); all
higher-level interpretation (Intent, executors, analyzers) lives in the
external ``klink`` package. Design: docs/REGION_INTENT_DESIGN.md.

Coordinate contract (sect.3.1): the Region PCell ``contours`` parameter is
PCell-local with the bbox lower-left anchor at the local origin; the
instance transform places the anchor in the target cell. ``region.get``
composes instance transform x local contours and REFUSES magnification != 1.

Rulers are the input draft only: a successful claim consumes them (sect.4.3).
"""

from __future__ import annotations

import hashlib
import json
import re

import pya

from ..errors import ErrorCode, RpcError
from ..registry import method
from ..txn import auto_txn
from ..region_geom import (
    ROLE_CLIP,
    ROLE_EXCLUDE,
    ROLE_INCLUDE,
    ROLES,
    DEFAULT_NPOINTS,
    RegionGeomError,
    box_polygon,
    compose,
    decode_contours,
    ellipse_polygon,
    encode_contours,
    polygon_from_points,
    to_local,
)
from .annotation_m import _active_view, _points_of, _OUTLINE_NAMES
from .cell_m import _active_layout, _resolve_cell
from .instance_m import KLINK_ID_PROPKEY

_DEFAULT_REGION_LAYER_KEY = "klink_default_region_layer"
_DEFAULT_REGION_LAYER = "999/10"
_KLINK_ID_PREFIX = "region:"
_NAME_RE = re.compile(r"^R(\d+)$")
_MAX_CLAIM_RULERS = 64


def _get_region_layer_str(params: dict, ly: pya.Layout) -> str:
    if "layer" in params:
        return str(params["layer"])
    try:
        value = ly.meta_info_value(_DEFAULT_REGION_LAYER_KEY)
    except Exception:
        value = None
    return str(value) if value else _DEFAULT_REGION_LAYER


def _parse_layer_ld(layer_str: str) -> tuple[int, int]:
    try:
        left, right = str(layer_str).split("/", 1)
        return int(left), int(right)
    except Exception:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "layer must be 'L/D' format, e.g. '999/10'",
        )


def _resolve_region_layer_idx(ly: pya.Layout, layer_str: str) -> int:
    layer, datatype = _parse_layer_ld(layer_str)
    info = pya.LayerInfo(layer, datatype)
    idx = ly.find_layer(info)
    if idx is None:
        idx = ly.insert_layer(info)
    return int(idx)


def _decl_name(decl) -> str:
    try:
        raw = decl.name
        return str(raw() if callable(raw) else raw)
    except Exception:
        return ""


def _is_region_instance(inst) -> bool:
    """klink Region recognition: decl name AND owning library.

    "Region" alone is a generic word (pya.Region is a geometry class; other
    libraries may ship a PCell of the same name), so unlike the Port/Anchor
    matchers this one also requires the cell to come from the klink_region
    library when that information is available.
    """
    try:
        cell = inst.cell
        if cell is None:
            return False
        decl = cell.pcell_declaration()
        if decl is None or _decl_name(decl) != "Region":
            return False
        try:
            lib = cell.library()
            if lib is not None and str(lib.name()) != "klink_region":
                return False
        except Exception:
            pass  # library info unavailable: fall back to decl-name match
        return True
    except Exception:
        return False


def _pcell_params_to_dict(variant_cell) -> dict:
    out: dict = {}
    try:
        decl = variant_cell.pcell_declaration()
        if decl is None:
            return out
        defs = list(decl.get_parameters())
        vals = list(variant_cell.pcell_parameters())
    except Exception:
        return out
    for pdef, value in zip(defs, vals):
        try:
            name = str(pdef.name)
            if isinstance(value, pya.LayerInfo):
                value = "%d/%d" % (value.layer, value.datatype)
            out[name] = value
        except Exception:
            pass
    return out


def _iter_region_instances(ly: pya.Layout):
    """Yield (parent_cell, inst) for every Region PCell instance."""
    for cell in ly.each_cell():
        try:
            if cell.is_proxy():
                continue
        except Exception:
            pass
        try:
            insts = list(cell.each_inst())
        except Exception:
            continue
        for inst in insts:
            if _is_region_instance(inst):
                yield cell, inst


def _collect_region_names(ly: pya.Layout) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _, inst in _iter_region_instances(ly):
        name = str(_pcell_params_to_dict(inst.cell).get("region_name", ""))
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _auto_region_name(existing: dict[str, int]) -> str:
    highest = 0
    for name in existing:
        m = _NAME_RE.match(name)
        if m:
            highest = max(highest, int(m.group(1)))
    return "R%03d" % (highest + 1)


def _inst_cplx_trans(inst) -> pya.ICplxTrans:
    try:
        return pya.ICplxTrans(inst.cplx_trans)
    except Exception:
        return pya.ICplxTrans(inst.trans)


def _composed_polygon(inst) -> pya.Polygon:
    """instance transform x local contours (refuses magnification != 1)."""
    params = _pcell_params_to_dict(inst.cell)
    trans = _inst_cplx_trans(inst)
    mag = float(trans.mag)
    if abs(mag - 1.0) > 1e-9:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "Region instance has magnification %.6g; regions must not be "
            "scaled" % mag,
            hint="reset the instance magnification to 1 (Edit > Properties), "
                 "then retry",
        )
    try:
        local = decode_contours(str(params.get("contours", "")))
    except RegionGeomError as exc:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "Region %r has corrupt contours: %s"
            % (params.get("region_name", "?"), exc),
            hint=exc.hint or "unclaim and re-claim the region",
        )
    return local.transformed(trans)


def _region_dict(parent: pya.Cell, inst, ly: pya.Layout,
                 include_polygon: bool = False) -> dict:
    params = _pcell_params_to_dict(inst.cell)
    dbu = ly.dbu
    out = {
        "name": str(params.get("region_name", "")),
        "cell": parent.name,
        "layer": str(params.get("layer", "")),
        "npoints": int(params.get("npoints", DEFAULT_NPOINTS)),
    }
    kid = None
    try:
        kid = inst.property(KLINK_ID_PROPKEY)
    except Exception:
        pass
    out["klink_id"] = str(kid) if kid else None
    try:
        poly = _composed_polygon(inst)
        bbox = poly.bbox()
        out["bbox_dbu"] = [bbox.left, bbox.bottom, bbox.right, bbox.top]
        out["bbox_um"] = [bbox.left * dbu, bbox.bottom * dbu,
                          bbox.right * dbu, bbox.top * dbu]
        out["holes"] = int(poly.holes())
        region = pya.Region()
        region.insert(poly)
        out["area_um2"] = float(region.area()) * dbu * dbu
        if include_polygon:
            out["hull_dbu"] = [[int(p.x), int(p.y)]
                               for p in poly.each_point_hull()]
            out["hull_um"] = [[p.x * dbu, p.y * dbu]
                              for p in poly.each_point_hull()]
            out["holes_dbu"] = [
                [[int(p.x), int(p.y)] for p in poly.each_point_hole(h)]
                for h in range(poly.holes())
            ]
    except RpcError as exc:
        out["error"] = str(exc)
    return out


def _find_regions_by_name(ly: pya.Layout, name: str) -> list:
    hits = []
    for parent, inst in _iter_region_instances(ly):
        params = _pcell_params_to_dict(inst.cell)
        if str(params.get("region_name", "")) == name:
            hits.append((parent, inst))
    return hits


def _require_single_region(ly: pya.Layout, name: str):
    hits = _find_regions_by_name(ly, name)
    if not hits:
        raise RpcError(
            ErrorCode.NOT_FOUND,
            "no Region named %r" % name,
            hint="call region.list for the live regions",
        )
    if len(hits) > 1:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "%d Region instances share the name %r (copy-paste duplicate)"
            % (len(hits), name),
            hint="this needs the keeper repair flow (region.repair_ids, "
                 "planned); until then delete the duplicates by hand",
        )
    return hits[0]


KIND_BOX = "box"
KIND_ELLIPSE = "ellipse"
KIND_POLYGON = "polygon"
KIND_LINE = "line"  # 2-point non-box/ellipse ruler: not claimable
_LABEL_TOKEN_RE = re.compile(r"^\s*region(?:\s*[:=]\s*(include|clip|exclude))?\s*$",
                             re.IGNORECASE)


def _ruler_label(ann) -> str:
    """The ruler's literal label (its format string, what the GUI shows as
    'text'); KLayout's default measurement formats ($D etc.) are reported
    as-is and simply never match the region token."""
    try:
        return str(ann.fmt or "")
    except Exception:
        return ""


def _labeled_role(label: str):
    """'region' / 'region:exclude' label token -> role (express lane)."""
    m = _LABEL_TOKEN_RE.match(label or "")
    if not m:
        return None
    return (m.group(1) or ROLE_INCLUDE).lower()


def _ruler_info(ann, dbu: float) -> dict:
    """How claim would read one ruler: kind by point count + outline."""
    try:
        outline = _OUTLINE_NAMES.get(int(ann.outline))
    except Exception:
        outline = None
    points = _points_of(ann)
    pts_dbu = [(int(round(p.x / dbu)), int(round(p.y / dbu))) for p in points]
    if len(points) >= 3:
        kind = KIND_POLYGON
    elif outline in (KIND_BOX, KIND_ELLIPSE):
        kind = outline
    else:
        kind = KIND_LINE
    info = {
        "id": int(ann.id()),
        "kind": kind,
        "outline": outline,
        "point_count": len(points),
        "points_um": [[float(p.x), float(p.y)] for p in points],
        "points_dbu": pts_dbu,
        "label": _ruler_label(ann),
    }
    try:
        info["category"] = str(ann.category or "")
    except Exception:
        info["category"] = ""
    try:
        bb = ann.box()
        if not bb.empty():
            info["bbox_um"] = [bb.left, bb.bottom, bb.right, bb.top]
    except Exception:
        pass
    info["labeled_role"] = _labeled_role(info["label"])
    return info


def _ruler_polygon_from_info(info: dict, role: str, npoints: int,
                             dbu: float) -> pya.Polygon:
    rid = int(info["id"])
    kind = info["kind"]
    pts = info["points_dbu"]
    if kind == KIND_LINE:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "ruler %d is a 2-point %s ruler (a line); region.claim reads "
            "2-point rulers only as box/ellipse, and 3+ point rulers as "
            "closed polygons" % (rid, info.get("outline")),
            hint="drag with the ruler tool's Box or Ellipse template, or "
                 "click 3+ points to outline the region (auto-closed "
                 "first..last..first)",
        )
    try:
        if kind == KIND_POLYGON:
            # exact vertices, auto-closed; same contribution for every role
            return polygon_from_points(pts, dbu)
        if kind == KIND_BOX:
            return box_polygon(pts[0], pts[1])
        # sect.4.2 / red line 9: discretization error always shrinks the
        # writable area -- include/clip inscribed, exclude circumscribed.
        return ellipse_polygon(
            pts[0], pts[1], npoints, circumscribed=(role == ROLE_EXCLUDE))
    except RegionGeomError as exc:
        raise RpcError(ErrorCode.BAD_PARAMS,
                       "ruler %d: %s" % (rid, exc),
                       hint=exc.hint)


def _live_ruler(view, ruler_id: int):
    ann = view.annotation(int(ruler_id))
    if ann is None or not ann.is_valid():
        raise RpcError(
            ErrorCode.NOT_FOUND,
            "no ruler with id %d" % int(ruler_id),
            hint="call region.claim_preview (or annotation.list) for the "
                 "live ruler ids",
        )
    return ann


def _ruler_polygon(view, ruler_id: int, role: str, npoints: int,
                   dbu: float) -> tuple[pya.Polygon, dict]:
    info = _ruler_info(_live_ruler(view, ruler_id), dbu)
    poly = _ruler_polygon_from_info(info, role, npoints, dbu)
    return poly, info


def _consumed_summary(info: dict, role: str) -> dict:
    out = {
        "id": info["id"],
        "role": role,
        "kind": info["kind"],
        "point_count": info["point_count"],
        "points_um": info["points_um"],
    }
    if "bbox_um" in info:
        out["bbox_um"] = info["bbox_um"]
    if info.get("label"):
        out["label"] = info["label"]
    return out


def _parse_ruler_items(rulers) -> list[tuple[int, str]]:
    if len(rulers) > _MAX_CLAIM_RULERS:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "%d rulers exceeds the claim limit of %d"
            % (len(rulers), _MAX_CLAIM_RULERS),
            hint="claim in smaller groups",
        )
    seen: set[int] = set()
    out: list[tuple[int, str]] = []
    for item in rulers:
        if not isinstance(item, dict) or "id" not in item:
            raise RpcError(
                ErrorCode.BAD_PARAMS,
                "each ruler item needs {id, role?}",
                hint="rulers: [{id: 3, role: 'include'}]",
            )
        role = str(item.get("role", ROLE_INCLUDE))
        if role not in ROLES:
            raise RpcError(
                ErrorCode.BAD_PARAMS,
                "unknown role %r" % role,
                hint="role is one of: %s" % ", ".join(ROLES),
            )
        rid = int(item["id"])
        if rid in seen:
            raise RpcError(
                ErrorCode.BAD_PARAMS,
                "ruler id %d listed twice" % rid,
                hint="each ruler appears once, with one role",
            )
        seen.add(rid)
        out.append((rid, role))
    return out


def _compose_rulers(view, items: list[tuple[int, str]], npoints: int,
                    dbu: float):
    """Validate + compose without touching anything. Returns
    (merged_polygon, consumed_summaries)."""
    by_role: dict[str, list[pya.Polygon]] = {r: [] for r in ROLES}
    consumed = []
    for rid, role in items:
        poly, info = _ruler_polygon(view, rid, role, npoints, dbu)
        by_role[role].append(poly)
        consumed.append(_consumed_summary(info, role))
    try:
        merged = compose(by_role[ROLE_INCLUDE], by_role[ROLE_CLIP],
                         by_role[ROLE_EXCLUDE])
    except RegionGeomError as exc:
        raise RpcError(ErrorCode.BAD_PARAMS, str(exc), hint=exc.hint)
    return merged, consumed


_CANDIDATE_ORDER_NOTE = (
    "newest first: KLayout allocates ruler ids as max(live id)+1, so among "
    "LIVE rulers a higher id was drawn later (verified empirically); an id "
    "can be reused after the newest ruler is deleted, so confirm by "
    "points/bbox, not by id alone"
)


@method(
    "region.claim_preview",
    description=(
        "Dry-run for region.claim, NO mutation: lists EVERY ruler in the "
        "current view as a claim candidate, newest first (recency_rank 1 = "
        "drawn last), each with how claim would read it (kind: box | "
        "ellipse | polygon for 3+ point rulers | line = not claimable), "
        "point count, bbox_um, label, selected, and labeled_role when the "
        "ruler's label is the express-lane token 'region' / "
        "'region:exclude'. Pass rulers:[{id, role}] to also compose that "
        "exact set and get the would-be result (bbox/area/holes) or the "
        "same errors claim would raise. Use it to NARRATE the candidates to "
        "the user and get a confirmation before claiming -- the view mixes "
        "this-moment intent with old measurement leftovers."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "rulers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {
                        "id": {"type": "integer"},
                        "role": {"type": "string", "enum": list(ROLES),
                                 "default": ROLE_INCLUDE},
                    },
                },
                "description": "Optional exact set to dry-run compose.",
            },
            "npoints": {"type": "integer", "default": DEFAULT_NPOINTS},
            "limit": {"type": "integer", "minimum": 1, "maximum": 5000,
                      "default": 500},
        },
    },
    returns_schema={"type": "object"},
    tags=["region", "read"],
)
def region_claim_preview(params, ctx):
    _, _, ly = _active_layout()
    dbu = ly.dbu
    view = _active_view()
    limit = int(params.get("limit", 500))
    if limit < 1 or limit > 5000:
        raise RpcError(ErrorCode.BAD_PARAMS, "limit must be 1..5000")
    npoints = int(params.get("npoints", DEFAULT_NPOINTS))

    selected = set()
    try:
        for a in view.each_annotation_selected():
            selected.add(int(a.id()))
    except Exception:
        pass
    infos = []
    for a in view.each_annotation():
        try:
            infos.append(_ruler_info(a, dbu))
        except Exception:
            continue
    infos.sort(key=lambda d: -int(d["id"]))
    truncated = len(infos) > limit
    infos = infos[:limit]
    candidates = []
    for rank, info in enumerate(infos, start=1):
        kind = info["kind"]
        cand = {
            "id": info["id"],
            "recency_rank": rank,
            "kind": kind,
            "outline": info["outline"],
            "point_count": info["point_count"],
            "label": info["label"],
            "category": info["category"],
            "selected": info["id"] in selected,
            "labeled_role": info["labeled_role"],
            "claimable": kind != KIND_LINE,
        }
        if "bbox_um" in info:
            cand["bbox_um"] = info["bbox_um"]
        if kind == KIND_LINE:
            cand["reason"] = ("2-point %s ruler is a line (measurement?); "
                              "only box/ellipse 2-point rulers and 3+ point "
                              "outlines are regions" % info["outline"])
        elif kind == KIND_POLYGON:
            try:
                polygon_from_points(info["points_dbu"], dbu)
            except RegionGeomError as exc:
                cand["claimable"] = False
                cand["reason"] = str(exc)
        candidates.append(cand)

    out = {
        "count": len(candidates),
        "truncated": truncated,
        "order": _CANDIDATE_ORDER_NOTE,
        "candidates": candidates,
        "labeled": [c["id"] for c in candidates if c["labeled_role"]],
        "sent_hint": (
            "if the user SENT rulers, prefer interaction.selection.latest "
            "-> rulers[].id (that gesture IS the intent window); otherwise "
            "narrate these candidates and claim only the set the user "
            "confirms"
        ),
    }
    rulers = params.get("rulers")
    if rulers:
        items = _parse_ruler_items(rulers)
        try:
            merged, consumed = _compose_rulers(view, items, npoints, dbu)
        except RpcError as exc:
            out["composed"] = None
            out["errors"] = [{"message": str(exc), "hint": exc.hint}]
        else:
            bbox = merged.bbox()
            region = pya.Region()
            region.insert(merged)
            out["composed"] = {
                "bbox_um": [bbox.left * dbu, bbox.bottom * dbu,
                            bbox.right * dbu, bbox.top * dbu],
                "area_um2": float(region.area()) * dbu * dbu,
                "holes": int(merged.holes()),
                "vertices": int(merged.num_points()),
                "would_consume": consumed,
            }
            out["errors"] = []
    return out


@method(
    "region.claim",
    description=(
        "Convert rulers into ONE klink_Region PCell (reserved layer, default "
        "999/10) and consume the rulers. Ruler kinds: 2-point box/ellipse "
        "rulers, and 3+ point rulers read as EXACT closed polygons "
        "(auto-closed first..last..first; self-intersecting outlines are "
        "refused naming the crossing segments). Roles: include (union), "
        "clip (intersect), exclude (subtract; holes are never writable). "
        "The result must be a single connected component; disconnected "
        "islands are rejected with per-island bboxes -- claim them "
        "separately. Ellipses are discretized safely: include/clip "
        "inscribed, exclude circumscribed. PICKING rulers is the hazard "
        "(the view mixes intent with old measurement lines): take ids from "
        "interaction.selection.latest.rulers when the user SENT them, else "
        "region.claim_preview + narrate + user confirmation; rulers labeled "
        "'region' may be taken without asking. The result echoes each "
        "consumed ruler (consumed[])."
    ),
    params_schema={
        "type": "object",
        "required": ["rulers"],
        "properties": {
            "rulers": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {
                        "id": {"type": "integer"},
                        "role": {
                            "type": "string",
                            "enum": list(ROLES),
                            "default": ROLE_INCLUDE,
                        },
                    },
                },
                "description": "Ruler ids (region.claim_preview / "
                               "interaction.selection.latest.rulers) with "
                               "roles.",
            },
            "cell": {"description": "Target cell name or cell_index "
                                    "(default: active cell)"},
            "layer": {"type": "string",
                      "description": "'L/D' region marker layer override"},
            "name": {"type": "string",
                     "description": "Region name (default auto R###)"},
            "npoints": {"type": "integer", "default": DEFAULT_NPOINTS},
            "keep_rulers": {"type": "boolean", "default": False},
        },
    },
    returns_schema={"type": "object"},
    mutates=True,
    tags=["region", "write"],
)
def region_claim(params, ctx):
    view, _, ly = _active_layout()
    dbu = ly.dbu

    if params.get("cell") is not None:
        parent = _resolve_cell(ly, params["cell"])
    else:
        try:
            cv = view.active_cellview()
            parent = cv.cell
        except Exception:
            parent = None
        if parent is None:
            raise RpcError(
                ErrorCode.BAD_PARAMS,
                "no active cell; pass cell explicitly",
                hint="region.claim {cell: 'TOP', rulers: [...]}",
            )

    rulers = params.get("rulers") or []
    if not rulers:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "rulers is empty",
            hint="region.claim_preview lists the candidate rulers newest "
                 "first; pass the confirmed set as rulers:[{id, role}]",
        )
    npoints = int(params.get("npoints", DEFAULT_NPOINTS))
    items = _parse_ruler_items(rulers)
    seen_ids = {rid for rid, _ in items}
    ruler_view = _active_view()  # rulers live on the view, not the layout
    # validate-before-mutate: every ruler is read and composed before any
    # PCell is created or any ruler consumed
    merged, consumed = _compose_rulers(ruler_view, items, npoints, dbu)

    existing = _collect_region_names(ly)
    name = params.get("name")
    if name:
        name = str(name)
        if name in existing:
            raise RpcError(
                ErrorCode.BAD_PARAMS,
                "Region name %r already exists" % name,
                hint="omit name for an auto R###, or pick a fresh one "
                     "(region.list shows the live names)",
            )
    else:
        name = _auto_region_name(existing)

    layer_str = _get_region_layer_str(params, ly)
    _resolve_region_layer_idx(ly, layer_str)
    layer_l, layer_d = _parse_layer_ld(layer_str)

    local, (ax, ay) = to_local(merged)
    try:
        contours = encode_contours(local)
    except RegionGeomError as exc:
        raise RpcError(ErrorCode.BAD_PARAMS, str(exc), hint=exc.hint)

    pcell_params = {
        "layer": pya.LayerInfo(layer_l, layer_d),
        "region_name": name,
        "contours": contours,
        "npoints": npoints,
    }
    try:
        variant = ly.create_cell("Region", "klink_region", pcell_params)
    except Exception as exc:
        raise RpcError(
            ErrorCode.EXEC,
            "failed to create klink_Region PCell: %s" % exc,
            hint="ensure the klink_region library is registered (reload the "
                 "klink plugin)",
        )
    if variant is None:
        raise RpcError(
            ErrorCode.NOT_FOUND,
            "klink_Region PCell not found",
            hint="region_pcell.py must register the klink_region library on "
                 "startup; reload the klink plugin",
        )

    klink_id = _KLINK_ID_PREFIX + name
    keep_rulers = bool(params.get("keep_rulers", False))
    with auto_txn(view, "klink: region.claim %s" % name):
        inst = parent.insert(
            pya.CellInstArray(variant.cell_index(), pya.Trans(ax, ay)))
        try:
            inst.set_property(KLINK_ID_PROPKEY, klink_id)
        except Exception:
            pass
        if not keep_rulers:
            for rid in sorted(seen_ids):
                try:
                    ruler_view.erase_annotation(rid)
                except Exception:
                    pass

    bbox = merged.bbox()
    region = pya.Region()
    region.insert(merged)
    return {
        "name": name,
        "klink_id": klink_id,
        "cell": parent.name,
        "layer": layer_str,
        "anchor_dbu": [ax, ay],
        "anchor_um": [ax * dbu, ay * dbu],
        "bbox_um": [bbox.left * dbu, bbox.bottom * dbu,
                    bbox.right * dbu, bbox.top * dbu],
        "area_um2": float(region.area()) * dbu * dbu,
        "holes": int(merged.holes()),
        "vertices": int(merged.num_points()),
        "consumed_rulers": [] if keep_rulers else sorted(seen_ids),
        # echo: what each ruler was read as (kind/points/bbox/label), so
        # the agent can show the user exactly what got claimed
        "consumed": consumed,
        "rulers_kept": keep_rulers,
        "next_action": (
            "region.get {name: '%s'} returns the composed polygon; feed "
            "hull_um to cell.fill_region to fill it, or bbox_um to "
            "view.zoom_box to navigate there" % name
        ),
    }


@method(
    "region.list",
    description=(
        "List every klink_Region PCell instance (name, cell, layer, bbox, "
        "area, klink_id). Regions are the durable claimed areas; rulers are "
        "only drafts."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "cell": {"description": "Restrict to one parent cell "
                                    "(name or index)"},
        },
    },
    returns_schema={"type": "object"},
    tags=["region", "read"],
)
def region_list(params, ctx):
    _, _, ly = _active_layout()
    only_cell = None
    if params.get("cell") is not None:
        only_cell = _resolve_cell(ly, params["cell"]).name
    regions = []
    for parent, inst in _iter_region_instances(ly):
        if only_cell is not None and parent.name != only_cell:
            continue
        regions.append(_region_dict(parent, inst, ly))
    return {"count": len(regions), "regions": regions}


@method(
    "region.get",
    description=(
        "One Region by name: composed polygon (instance transform x local "
        "contours) in target-cell DBU and um, area, holes. Refuses "
        "magnification != 1."
    ),
    params_schema={
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string"}},
    },
    returns_schema={"type": "object"},
    tags=["region", "read"],
)
def region_get(params, ctx):
    _, _, ly = _active_layout()
    parent, inst = _require_single_region(ly, str(params["name"]))
    return _region_dict(parent, inst, ly, include_polygon=True)


_OCCUPANCY_MAX_POLYGONS = 20000


def _canonical_hash(obj) -> str:
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _scope_hash(occ: dict) -> str:
    """Cheap TOCTOU fingerprint of an occupancy result (sect.9.3 design).

    Same code + same process + same world => same hash. Prepare stores it;
    intent.apply_managed_plan recomputes through this exact function."""
    trimmed = {k: v for k, v in occ.items() if k != "free_polygons"}
    return _canonical_hash(trimmed)


def _polygon_rings_dbu(poly: pya.Polygon) -> dict:
    return {
        "hull_dbu": [[int(p.x), int(p.y)] for p in poly.each_point_hull()],
        "holes_dbu": [
            [[int(p.x), int(p.y)] for p in poly.each_point_hole(h)]
            for h in range(poly.holes())
        ],
    }


def _layer_occupancy(ly: pya.Layout, parent: pya.Cell, idx: int,
                     exclude_cells: tuple[str, ...]) -> pya.Region:
    """Shapes of `parent` on layer idx, hierarchy expanded, SKIPPING the
    direct child instances whose target cell name is in exclude_cells.

    Path-based on purpose: excluding by cell (unselect_cells) would also
    drop the user's OTHER instances of a shared child cell, and subtracting
    the excluded subtree's region afterwards would erase user geometry that
    happens to overlap it. Exclusion is only exact when done per instance.
    """
    if not exclude_cells:
        return pya.Region(parent.begin_shapes_rec(idx))
    occ = pya.Region(parent.shapes(idx))
    for inst in parent.each_inst():
        try:
            child = inst.cell
            if child is None or child.name in exclude_cells:
                continue
        except Exception:
            continue
        sub = pya.Region(child.begin_shapes_rec(idx))
        if sub.is_empty():
            continue
        try:
            member_trans = list(inst.cell_inst.each_cplx_trans())
        except Exception:
            member_trans = [inst.cplx_trans]
        for trans in member_trans:
            occ += sub.transformed(pya.ICplxTrans(trans))
    return occ


def _member_transforms(inst) -> list:
    try:
        return list(inst.cell_inst.each_cplx_trans())
    except Exception:
        return [inst.cplx_trans]


def _cell_occurrences(parent: pya.Cell, targets: frozenset,
                      exclude_cells: tuple[str, ...],
                      trans: pya.ICplxTrans, out: list,
                      cap: int) -> None:
    """Collect (cell, composed_trans) for every occurrence of a target cell
    under `parent`, honoring the per-instance-path exclude list. Does not
    descend INTO a matched target (its bbox already covers its content)."""
    if len(out) > cap:
        return
    for inst in parent.each_inst():
        try:
            child = inst.cell
        except Exception:
            continue
        if child is None or child.name in exclude_cells:
            continue
        for member in _member_transforms(inst):
            composed = trans * pya.ICplxTrans(member)
            if child.name in targets:
                out.append((child, composed))
                if len(out) > cap:
                    return
            else:
                _cell_occurrences(child, targets, exclude_cells, composed,
                                  out, cap)


def _occupancy_for(ly: pya.Layout, parent: pya.Cell,
                   region_poly: pya.Polygon, layers: list[str],
                   exclude_cells: tuple[str, ...] = (),
                   obstacle_cells: tuple[str, ...] = ()) -> dict:
    """Recursive occupancy of the given layers clipped to the region.

    Pure mechanism: merged obstacle polygons per layer plus per-CELL
    obstacle footprints (every occurrence bbox of the named cells inside
    the region -- for custom devices/blackboxes whose layers the caller
    does not want to enumerate) plus the remaining free area. Truncation
    is an ERROR state for planning, reported not silently dropped.
    exclude_cells skips instance paths under the named child cells (an
    intent's own previous output container must not count as its own
    obstacle when regenerating) -- it applies to BOTH layer and cell scans.
    """
    clip = pya.Region()
    clip.insert(region_poly)
    dbu = ly.dbu
    exclude_cells = tuple(exclude_cells or ())
    obstacle_cells = tuple(obstacle_cells or ())

    per_layer = []
    union = pya.Region()
    truncated = False
    for layer_str in layers:
        layer, datatype = _parse_layer_ld(layer_str)
        idx = ly.find_layer(pya.LayerInfo(layer, datatype))
        if idx is None:
            per_layer.append({"layer": layer_str, "present": False,
                              "polygons": [], "area_um2": 0.0, "count": 0})
            continue
        occ = _layer_occupancy(ly, parent, idx, exclude_cells) & clip
        occ.merge()
        polys = [p.dup() for p in occ.each_merged()]
        if len(polys) > _OCCUPANCY_MAX_POLYGONS:
            truncated = True
            polys = polys[:_OCCUPANCY_MAX_POLYGONS]
        union += occ
        per_layer.append({
            "layer": layer_str,
            "present": True,
            "count": len(polys),
            "area_um2": float(occ.area()) * dbu * dbu,
            "polygons": [_polygon_rings_dbu(p) for p in polys],
        })
    cell_entry = None
    if obstacle_cells:
        occurrences: list = []
        _cell_occurrences(parent, frozenset(obstacle_cells), exclude_cells,
                          pya.ICplxTrans(), occurrences,
                          _OCCUPANCY_MAX_POLYGONS)
        if len(occurrences) > _OCCUPANCY_MAX_POLYGONS:
            truncated = True
            occurrences = occurrences[:_OCCUPANCY_MAX_POLYGONS]
        cell_region = pya.Region()
        for child, composed in occurrences:
            bbox = child.bbox()
            if bbox.empty():
                continue
            cell_region.insert(
                pya.Polygon(bbox).transformed(composed))
        cell_region &= clip
        cell_region.merge()
        union += cell_region
        cell_entry = {
            "cells": list(obstacle_cells),
            "occurrences": len(occurrences),
            "area_um2": float(cell_region.area()) * dbu * dbu,
            "polygons": [_polygon_rings_dbu(p.dup())
                         for p in cell_region.each_merged()],
        }

    union.merge()
    free = clip - union
    free.merge()
    free_polys = [p.dup() for p in free.each_merged()]
    if len(free_polys) > _OCCUPANCY_MAX_POLYGONS:
        truncated = True
        free_polys = free_polys[:_OCCUPANCY_MAX_POLYGONS]
    return {
        "layers": per_layer,
        "obstacle_cells": cell_entry,
        "occupied_area_um2": float(union.area()) * dbu * dbu,
        "free_area_um2": float(free.area()) * dbu * dbu,
        "free_polygons": [_polygon_rings_dbu(p) for p in free_polys],
        "truncated": truncated,
    }


@method(
    "region.occupancy",
    description=(
        "Recursive occupancy facts for one Region: merged obstacle polygons "
        "per requested layer AND/OR per named obstacle CELL (every "
        "occurrence bbox of those cells inside the region -- for custom "
        "devices/blackboxes), clipped to the region polygon, plus the "
        "remaining free area. Everything is explicit -- klink assumes "
        "nothing about which layers or cells are obstacles. "
        "`truncated: true` means the result is incomplete and MUST NOT be "
        "used for planning."
    ),
    params_schema={
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string"},
            "layers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Obstacle layers 'L/D' to scan (explicit).",
            },
            "obstacle_cells": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Cell names whose every instance occurrence "
                               "(recursive, bbox footprint) counts as an "
                               "obstacle -- custom devices/blackboxes.",
            },
            "exclude_cells": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Child cell names whose instance paths are "
                               "skipped in BOTH scans (an intent's own "
                               "output container when regenerating).",
            },
        },
    },
    returns_schema={"type": "object"},
    tags=["region", "read"],
)
def region_occupancy(params, ctx):
    _, _, ly = _active_layout()
    parent, inst = _require_single_region(ly, str(params["name"]))
    poly = _composed_polygon(inst)
    layers = [str(v) for v in (params.get("layers") or [])]
    exclude = tuple(str(v) for v in (params.get("exclude_cells") or []))
    obstacle_cells = tuple(str(v)
                           for v in (params.get("obstacle_cells") or []))
    out = _occupancy_for(ly, parent, poly, layers, exclude, obstacle_cells)
    out["scope_hash"] = _scope_hash(out)
    out["name"] = str(params["name"])
    out["cell"] = parent.name
    out["dbu_um"] = float(ly.dbu)
    bbox = poly.bbox()
    out["region_bbox_dbu"] = [bbox.left, bbox.bottom, bbox.right, bbox.top]
    return out


@method(
    "region.unclaim",
    description=(
        "Delete one Region PCell instance by name. Deletes ONLY the region "
        "marker -- never any generated output or user geometry."
    ),
    params_schema={
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string"}},
    },
    returns_schema={"type": "object"},
    mutates=True,
    tags=["region", "write", "delete"],
)
def region_unclaim(params, ctx):
    view, _, ly = _active_layout()
    name = str(params["name"])
    parent, inst = _require_single_region(ly, name)
    with auto_txn(view, "klink: region.unclaim %s" % name):
        parent.erase(inst)
    return {"deleted": name, "cell": parent.name}


@method(
    "region.repair_ids",
    description=(
        "Keeper repair for copy-paste duplicated Regions (same name / "
        "klink_id on several instances). The USER designates the keeper "
        "(parent cell + anchor position); the keeper retains the original "
        "name, klink_id, and any Intent binding untouched. Every other "
        "duplicate gets a fresh auto R### name + klink_id and NO intent -- "
        "bind one later with intent.rebind. Never guesses which copy is "
        "the original; ambiguous keeper specs are refused."
    ),
    params_schema={
        "type": "object",
        "required": ["name", "keeper"],
        "properties": {
            "name": {"type": "string",
                     "description": "The duplicated Region name."},
            "keeper": {
                "type": "object",
                "required": ["cell", "anchor_dbu"],
                "properties": {
                    "cell": {"type": "string"},
                    "anchor_dbu": {
                        "type": "array", "minItems": 2, "maxItems": 2,
                        "description": "Instance displacement of the copy "
                                       "to KEEP (region.list shows each "
                                       "copy's bbox; anchor = bbox "
                                       "lower-left).",
                    },
                },
            },
        },
    },
    returns_schema={"type": "object"},
    mutates=True,
    tags=["region", "write"],
)
def region_repair_ids(params, ctx):
    view, _, ly = _active_layout()
    name = str(params["name"])
    hits = _find_regions_by_name(ly, name)
    if len(hits) < 2:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "found %d instance(s) named %r; repair needs a duplicate "
            "(2 or more)" % (len(hits), name),
            hint="region.list shows the live regions",
        )

    keeper_spec = params["keeper"]
    k_cell = str(keeper_spec["cell"])
    k_anchor = [int(v) for v in keeper_spec["anchor_dbu"]]
    keeper_matches = []
    for parent, inst in hits:
        try:
            disp = inst.cplx_trans.disp
            anchor = [int(disp.x), int(disp.y)]
        except Exception:
            anchor = None
        if parent.name == k_cell and anchor == k_anchor:
            keeper_matches.append((parent, inst))
    if len(keeper_matches) != 1:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "keeper spec matched %d of the %d duplicates; need exactly one"
            % (len(keeper_matches), len(hits)),
            hint="use region.list to read each copy's cell and bbox; "
                 "anchor_dbu is the instance displacement (bbox lower-left "
                 "for unrotated copies)",
        )
    keeper_inst = keeper_matches[0][1]

    existing = _collect_region_names(ly)
    renamed = []
    with auto_txn(view, "klink: region.repair_ids %s" % name):
        for parent, inst in hits:
            if inst == keeper_inst:
                continue
            new_name = _auto_region_name(existing)
            existing[new_name] = 1
            try:
                inst = inst.change_pcell_parameter("region_name", new_name)
            except Exception as exc:
                raise RpcError(
                    ErrorCode.EXEC,
                    "failed to rename duplicate in %s: %s"
                    % (parent.name, exc),
                )
            try:
                inst.set_property(KLINK_ID_PROPKEY,
                                  _KLINK_ID_PREFIX + new_name)
            except Exception:
                pass
            renamed.append({"cell": parent.name, "new_name": new_name,
                            "new_klink_id": _KLINK_ID_PREFIX + new_name})

    return {
        "keeper": {"name": name, "cell": k_cell, "anchor_dbu": k_anchor},
        "renamed": renamed,
        "next_action": "duplicates now have fresh ids and NO intent; use "
                       "intent.rebind to attach one if needed",
    }


@method(
    "region.set_layer",
    description="Configure the default Region PCell marker layer for this "
                "layout (default %s)." % _DEFAULT_REGION_LAYER,
    params_schema={
        "type": "object",
        "required": ["layer"],
        "properties": {
            "layer": {"type": "string", "description": "'L/D', e.g. '999/10'"},
        },
    },
    returns_schema={"type": "object",
                    "properties": {"region_layer": {"type": "string"}}},
    mutates=True,
    tags=["region", "write"],
)
def region_set_layer(params, ctx):
    _, _, ly = _active_layout()
    layer_str = str(params["layer"])
    _resolve_region_layer_idx(ly, layer_str)
    ly.add_meta_info(pya.LayoutMetaInfo(_DEFAULT_REGION_LAYER_KEY, layer_str))
    return {"region_layer": layer_str}

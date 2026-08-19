"""Deterministic array_labeled planner (executor klink.array_labeled v2).

Pure geometry, offline-testable with the pip ``klayout`` package. No client,
no view, no process facts: the caller supplies the region polygon, the
obstacle polygons, the source-cell footprint, and every parameter.

Contract highlights (docs/REGION_INTENT_DESIGN.md §7.2/§11.3-v1):

* pitch is CENTER-to-center; the first footprint's lower-left corner sits on
  the region bbox lower-left anchor; candidate sites enumerate y-asc x-asc
  on that integer-DBU grid; no global-optimum claim.
* a site is accepted only if the instance footprint AND its label's actual
  polygon text lie fully inside the region (holes excluded) and clear of
  every obstacle polygon; otherwise the whole site is rejected with a
  reason.
* numbering traversal (order/start) is separate from enumeration and comes
  from klink.domains.fabrication.sites.number_sites.
* labels are REAL polygons (klayout TextGenerator default font) so preview
  and apply are byte-identical; the generator version is recorded.
* the plan carries a plan_hash over the canonical payload; validators are
  part of the result — a plan with problems must never be applied.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

try:  # klayout.db ships a `pya` compat module; prefer the explicit one
    import klayout.db as _db
except ImportError:  # pragma: no cover - inside KLayout the app provides pya
    import pya as _db  # type: ignore

from ..fabrication.sites import Site, number_sites, pattern_site_ids

# Nominal glyph height of KLayout's default TextGenerator font at
# magnification 1 (probed: 'S101' bbox height 0.7um * mag). Used only to
# derive the magnification for a REQUESTED text height; actual geometry is
# measured from the generated polygons, so this is a seed, not a truth.
_DEFAULT_FONT_UNIT_HEIGHT_UM = 0.7

REJECT_FOOTPRINT_OUTSIDE = "footprint_outside_region"
REJECT_FOOTPRINT_OBSTACLE = "footprint_hits_obstacle"
REJECT_LABEL_OUTSIDE = "label_outside_region"
REJECT_LABEL_OBSTACLE = "label_hits_obstacle"


class PlanError(ValueError):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


@dataclass
class ArrayLabeledPlan:
    source_cell: str
    label_layer: str
    instances: list = field(default_factory=list)   # {site_id,row,col,trans_dbu,center_dbu}
    labels: list = field(default_factory=list)      # {site_id,text,polygons_dbu}
    rejected: list = field(default_factory=list)    # {row,col,reason}
    problems: list = field(default_factory=list)
    rows: int = 0
    cols: int = 0
    text_generator: str = ""
    label_height_um: float = 0.0
    plan_hash: str = ""

    rotation_deg: int = 0
    mirror: bool = False

    def payload(self) -> dict:
        """The typed payload consumed by intent.apply_managed_plan.

        Container-local coordinates == target-cell coordinates (the root
        instance is placed at trans (0,0)), so the plan's DBU values pass
        through unchanged.
        """
        shapes = []
        for label in self.labels:
            for poly in label["polygons_dbu"]:
                shapes.append({"layer": self.label_layer,
                               "polygon_dbu": poly["hull"],
                               "holes_dbu": poly["holes"]})
        instances = [
            {"child": self.source_cell, "trans_dbu": list(inst["trans_dbu"]),
             "rotation_deg": self.rotation_deg, "mirror": self.mirror}
            for inst in self.instances
        ]
        return {"shapes": shapes, "instances": instances,
                "root_trans_dbu": [0, 0]}


def _region_from(hull_dbu, holes_dbu) -> "_db.Region":
    poly = _db.Polygon([_db.Point(int(x), int(y)) for x, y in hull_dbu])
    for hole in holes_dbu or []:
        poly.insert_hole([_db.Point(int(x), int(y)) for x, y in hole])
    region = _db.Region()
    region.insert(poly)
    return region


def _obstacle_region(obstacles) -> "_db.Region":
    region = _db.Region()
    for item in obstacles or []:
        poly = _db.Polygon(
            [_db.Point(int(x), int(y)) for x, y in item["hull_dbu"]])
        for hole in item.get("holes_dbu") or []:
            poly.insert_hole([_db.Point(int(x), int(y)) for x, y in hole])
        region.insert(poly)
    region.merge()
    return region


def _inside(region: "_db.Region", candidate: "_db.Region") -> bool:
    return (candidate - region).is_empty()


def _clear_of(obstacles: "_db.Region", candidate: "_db.Region") -> bool:
    return (candidate & obstacles).is_empty()


def _canonical_plan_hash(payload: dict, extra: dict) -> str:
    text = json.dumps({"payload": payload, "meta": extra},
                      sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _label_polygons(text: str, dbu_um: float, height_um: float):
    """Actual polygon text via KLayout's default TextGenerator.

    Returns (list of {hull, holes} point-list dicts in DBU relative to the
    text's own bbox lower-left, bbox (w, h) in DBU). Holes matter: glyphs
    like 0/8/9 are not filled blobs."""
    gen = _db.TextGenerator.default_generator()
    mag = float(height_um) / _DEFAULT_FONT_UNIT_HEIGHT_UM
    region = gen.text(str(text), float(dbu_um), mag)
    bbox = region.bbox()
    polys = []
    for poly in region.each_merged():
        moved = poly.dup()
        moved.move(-bbox.left, -bbox.bottom)
        polys.append({
            "hull": [[int(p.x), int(p.y)] for p in moved.each_point_hull()],
            "holes": [
                [[int(p.x), int(p.y)] for p in moved.each_point_hole(h)]
                for h in range(moved.holes())
            ],
        })
    return polys, (bbox.width(), bbox.height())


def plan_array_labeled(
    *,
    region_hull_dbu,
    region_holes_dbu,
    dbu_um: float,
    obstacles_dbu,
    source_cell: str,
    footprint_bbox_dbu,
    pitch_um,
    numbering: dict,
    label: dict,
    rotation_deg: int = 0,
    mirror: bool = False,
    clearance_um: float = 0.0,
    klayout_version: str = "",
) -> ArrayLabeledPlan:
    """Plan the array. Every parameter is required and explicit.

    numbering:    {prefix: str, width: int, start: int, order: "top_down"|"bottom_up"}
    label:        {layer: "L/D", height_um: float, offset_um: [dx, dy]}
                  offset is from the footprint center to the label center.
    rotation_deg: instance orientation (0/90/180/270), mirror: x-axis flip
                  before rotation — the footprint is the TRANSFORMED bbox.
    clearance_um: keep-away margin grown around every obstacle (>= 0).

    label SLOT mode (owner workflow: circle a spot INSIDE the unit cell
    that says "the number goes here"): instead of height_um/offset_um pass
      label: {layer, slot_bbox_dbu: [l,b,r,t]  (SOURCE-cell local),
              margin_um: 0.0, min_height_um: optional}
    The slot transforms with each placed instance; the text stays upright,
    centered in the transformed slot, auto-sized so the WIDEST id of the
    batch fits the slot minus margin (monospace font => exact for the
    whole batch). The slot must lie inside the source footprint bbox, so
    the footprint checks already cover the label — no separate label
    containment/obstacle rejects in slot mode.
    """
    dbu = float(dbu_um)
    if dbu <= 0:
        raise PlanError("dbu_um must be positive")
    px = int(round(float(pitch_um[0]) / dbu))
    py = int(round(float(pitch_um[1]) / dbu))
    if px <= 0 or py <= 0:
        raise PlanError("pitch_um values must be positive",
                        hint="pitch is the CENTER-to-center distance in um")

    rotation_deg = int(rotation_deg)
    if rotation_deg not in (0, 90, 180, 270):
        raise PlanError("rotation_deg must be one of 0/90/180/270",
                        hint="arbitrary-angle instance placement is not "
                             "part of array_labeled v2")
    mirror = bool(mirror)
    clearance_um = float(clearance_um)
    if clearance_um < 0:
        raise PlanError("clearance_um must be >= 0")

    raw = [int(v) for v in footprint_bbox_dbu]
    tbox = _db.Box(raw[0], raw[1], raw[2], raw[3]).transformed(
        _db.Trans(rotation_deg // 90, mirror, _db.Vector(0, 0)))
    fl, fb, fr, ft = tbox.left, tbox.bottom, tbox.right, tbox.top
    fw, fh = fr - fl, ft - fb
    if fw <= 0 or fh <= 0:
        raise PlanError(
            "source cell footprint bbox is empty",
            hint="the source cell %r has no geometry" % source_cell)

    if "layer" not in label:
        raise PlanError(
            "label.layer is required",
            hint="label = {layer: 'L/D', height_um+offset_um} or "
                 "{layer: 'L/D', slot_bbox_dbu} — klink ships no defaults")
    slot_mode = "slot_bbox_dbu" in label
    if slot_mode:
        sl, sb, sr, st = [int(v) for v in label["slot_bbox_dbu"]]
        if not (raw[0] <= sl and raw[1] <= sb and sr <= raw[2]
                and st <= raw[3]):
            raise PlanError(
                "label slot bbox %s lies outside the source cell footprint "
                "%s" % ([sl, sb, sr, st], raw),
                hint="the text slot region must be circled INSIDE the "
                     "source cell's geometry bbox")
        slot_margin_dbu = int(round(float(label.get("margin_um", 0.0)) / dbu))
        label_height_um = 0.0  # computed from the slot below
        label_dx = label_dy = 0
    else:
        for key in ("height_um", "offset_um"):
            if key not in label:
                raise PlanError(
                    "label.%s is required (offset mode)" % key,
                    hint="label = {layer: 'L/D', height_um: float, "
                         "offset_um: [dx, dy]} — or circle a text slot "
                         "inside the unit cell and pass slot_bbox_dbu")
        label_height_um = float(label["height_um"])
        if label_height_um <= 0:
            raise PlanError("label.height_um must be positive")
        label_dx = int(round(float(label["offset_um"][0]) / dbu))
        label_dy = int(round(float(label["offset_um"][1]) / dbu))

    # two numbering modes, both caller-defined (klink hardcodes no notation):
    # PREFIX  {prefix, width, start, order}          -> S001, S002, ...
    # PATTERN {pattern, row?, col?, index? axes}     -> any grid notation:
    #         "{row}+{col}" -> 1+2, "{row}-{col}" -> 1-3, "R{row}C{col}",
    #         "{row:A}{col}" -> A1, "S{index:03d}" -> S001
    pattern_mode = "pattern" in numbering
    if pattern_mode:
        if "{" not in str(numbering["pattern"]):
            raise PlanError(
                "numbering.pattern has no {field}; supported fields: "
                "{row}, {col}, {index}",
                hint="e.g. '{row}+{col}', 'R{row}C{col}', '{row:A}{col}', "
                     "'S{index:03d}'")
    else:
        for key in ("prefix", "start", "order"):
            if key not in numbering:
                raise PlanError(
                    "numbering.%s is required" % key,
                    hint="numbering = {prefix: 'S', width: 3, start: 1, "
                         "order: 'top_down'|'bottom_up'} — or pass a "
                         "pattern: {pattern: '{row}+{col}', ...}")

    region = _region_from(region_hull_dbu, region_holes_dbu)
    obstacles = _obstacle_region(obstacles_dbu)
    clearance_dbu = int(round(clearance_um / dbu))
    if clearance_dbu > 0 and not obstacles.is_empty():
        obstacles.size(clearance_dbu)
        obstacles.merge()
    rbox = region.bbox()

    # site grid: first footprint lower-left ON the region bbox lower-left
    anchor_x, anchor_y = rbox.left, rbox.bottom
    span_w = rbox.width()
    span_h = rbox.height()
    ncols = 0 if span_w < fw else (span_w - fw) // px + 1
    nrows = 0 if span_h < fh else (span_h - fh) // py + 1

    # Worst-case label test box, checked BEFORE numbering so rejected sites
    # never consume a number (no gaps in S001..S0NN). The default font is
    # monospace (probed: same char count => identical bbox), so for
    # zero-padded numbering this box is exact, and when an index overflows
    # the padding width it is conservative — errors only shrink placement.
    def _pattern_ids(sites_in):
        return pattern_site_ids(
            sites_in,
            pattern=str(numbering["pattern"]),
            rows_total=nrows, cols_total=ncols,
            row_axis=numbering.get("row"),
            col_axis=numbering.get("col"),
            index_axis=numbering.get("index"),
        )

    if pattern_mode:
        # worst-case sample: render EVERY grid position (any accepted
        # subset's positional ids and index range are covered by the full
        # grid) and take the longest string — exact under the monospace font
        try:
            full_grid = [Site("", r, c, (0.0, 0.0))
                         for r in range(nrows) for c in range(ncols)]
            all_ids = [s.site_id for s in _pattern_ids(full_grid)]
        except ValueError as exc:
            raise PlanError("numbering pattern invalid: %s" % exc,
                            hint="fields: {row}, {col}, {index}; specs: "
                                 "ints like 02d, or A for letters")
        sample_text = max(all_ids, key=len) if all_ids else "0"
    else:
        prefix = str(numbering["prefix"])
        width_digits = int(numbering.get("width", 3))
        start_num = int(numbering["start"])
        max_index = start_num + max(0, nrows * ncols - 1)
        digits = max(width_digits, len(str(max_index)))
        sample_text = prefix + "0" * digits

    if slot_mode:
        # auto-fit: size the WIDEST id of the batch into the TRANSFORMED
        # slot minus margin (monospace font => exact for every id)
        inst_op = _db.Trans(rotation_deg // 90, mirror, _db.Vector(0, 0))
        tslot = _db.Box(sl, sb, sr, st).transformed(inst_op)
        avail_w = tslot.width() - 2 * slot_margin_dbu
        avail_h = tslot.height() - 2 * slot_margin_dbu
        _, (tw1, th1) = _label_polygons(sample_text, dbu,
                                        _DEFAULT_FONT_UNIT_HEIGHT_UM)
        if avail_w <= 0 or avail_h <= 0 or tw1 <= 0 or th1 <= 0:
            raise PlanError(
                "label slot is too small for any text",
                hint="enlarge the circled slot region or reduce margin_um")
        scale = min(avail_w / tw1, avail_h / th1)
        label_height_um = _DEFAULT_FONT_UNIT_HEIGHT_UM * scale
        # integer glyph rendering can overshoot the analytic scale by a
        # DBU, and centering an odd-width text in an even-width slot costs
        # one more: verify the ACTUAL sample dims with a 1-DBU guard and
        # step the height down deterministically until it truly fits
        for _ in range(16):
            _, (tw_f, th_f) = _label_polygons(sample_text, dbu,
                                              label_height_um)
            if tw_f <= avail_w - 1 and th_f <= avail_h - 1:
                break
            label_height_um *= 0.995
        else:
            raise PlanError(
                "label slot is too small for any text",
                hint="enlarge the circled slot region or reduce margin_um")
        min_h = label.get("min_height_um")
        if min_h is not None and label_height_um < float(min_h):
            raise PlanError(
                "auto-fit label height %.3fum is below min_height_um %.3f"
                % (label_height_um, float(min_h)),
                hint="enlarge the slot, shorten the prefix/width, or lower "
                     "min_height_um")
        slot_center_local = ((sl + sr) // 2, (sb + st) // 2)
        lw = lh = 0  # no per-site label acceptance box in slot mode
    else:
        _, (lw, lh) = _label_polygons(sample_text, dbu, label_height_um)

    accepted_sites: list[Site] = []
    site_geo: dict[tuple[int, int], dict] = {}
    rejected = []
    for row in range(nrows):
        for col in range(ncols):
            # instance origin so that footprint lower-left = anchor + i*pitch
            tx = anchor_x - fl + col * px
            ty = anchor_y - fb + row * py
            foot = _db.Region()
            foot.insert(_db.Box(fl + tx, fb + ty, fr + tx, ft + ty))
            if not _inside(region, foot):
                rejected.append({"row": row, "col": col,
                                 "reason": REJECT_FOOTPRINT_OUTSIDE})
                continue
            if not _clear_of(obstacles, foot):
                rejected.append({"row": row, "col": col,
                                 "reason": REJECT_FOOTPRINT_OBSTACLE})
                continue
            cx = tx + (fl + fr) // 2
            cy = ty + (fb + ft) // 2
            if not slot_mode:
                # slot mode needs no per-site label checks: the slot lies
                # inside the footprint, which was just proven in-region and
                # obstacle-free
                lbox = _db.Region()
                lbox.insert(_db.Box(cx + label_dx - lw // 2,
                                    cy + label_dy - lh // 2,
                                    cx + label_dx - lw // 2 + lw,
                                    cy + label_dy - lh // 2 + lh))
                if not _inside(region, lbox):
                    rejected.append({"row": row, "col": col,
                                     "reason": REJECT_LABEL_OUTSIDE})
                    continue
                if not _clear_of(obstacles, lbox):
                    rejected.append({"row": row, "col": col,
                                     "reason": REJECT_LABEL_OBSTACLE})
                    continue
            accepted_sites.append(Site(
                site_id="", row=row, col=col,
                center_um=(cx * dbu, cy * dbu)))
            site_geo[(row, col)] = {"trans_dbu": (tx, ty),
                                    "center_dbu": (cx, cy)}

    if pattern_mode:
        numbered = _pattern_ids(accepted_sites)
    else:
        numbered = number_sites(
            accepted_sites,
            scheme="prefix",
            prefix=str(numbering["prefix"]),
            width=int(numbering.get("width", 3)),
            order=str(numbering["order"]),
            start=int(numbering["start"]),
        )

    plan = ArrayLabeledPlan(
        source_cell=str(source_cell),
        label_layer=str(label["layer"]),
        rows=nrows, cols=ncols,
        rotation_deg=rotation_deg, mirror=mirror,
        label_height_um=float(label_height_um),
        text_generator="klayout_default:%s" % (klayout_version or "unknown"),
    )

    # actual label geometry (site acceptance already settled above; the
    # monospace worst-case box guarantees these pass, asserted as problems)
    label_area = _db.Region()
    seen_ids: set[str] = set()
    for site in numbered:
        geo = site_geo[(site.row, site.col)]
        if site.site_id in seen_ids:
            plan.problems.append(
                "duplicate label id %r (numbering collision)" % site.site_id)
            continue
        seen_ids.add(site.site_id)
        polys, (tw, th) = _label_polygons(site.site_id, dbu, label_height_um)
        if slot_mode:
            # upright text centered in the slot as it moved with THIS
            # instance (translation + rotation/mirror)
            tx0, ty0 = geo["trans_dbu"]
            wc = _db.Trans(rotation_deg // 90, mirror,
                           _db.Vector(tx0, ty0)) * _db.Point(
                slot_center_local[0], slot_center_local[1])
            ox = wc.x - tw // 2
            oy = wc.y - th // 2
        else:
            # center the label bbox at footprint center + offset
            ox = geo["center_dbu"][0] + label_dx - tw // 2
            oy = geo["center_dbu"][1] + label_dy - th // 2
        placed = [
            {
                "hull": [[x + ox, y + oy] for x, y in poly["hull"]],
                "holes": [[[x + ox, y + oy] for x, y in hole]
                          for hole in poly["holes"]],
            }
            for poly in polys
        ]
        label_region = _db.Region()
        for poly in placed:
            lp = _db.Polygon([_db.Point(x, y) for x, y in poly["hull"]])
            for hole in poly["holes"]:
                lp.insert_hole([_db.Point(x, y) for x, y in hole])
            label_region.insert(lp)
        if not slot_mode and (
                not _inside(region, label_region)
                or not _clear_of(obstacles, label_region)):
            plan.problems.append(
                "internal: actual label %r escaped its worst-case test box"
                % site.site_id)
            continue
        label_area += label_region
        plan.instances.append({
            "site_id": site.site_id, "row": site.row, "col": site.col,
            "trans_dbu": list(geo["trans_dbu"]),
            "center_dbu": list(geo["center_dbu"]),
        })
        plan.labels.append({"site_id": site.site_id, "text": site.site_id,
                            "polygons_dbu": placed})

    plan.rejected = rejected

    # ---- plan-level validators (problems block apply) ----
    if plan.instances:
        foot_region = _db.Region()
        total = 0
        for inst in plan.instances:
            tx, ty = inst["trans_dbu"]
            box = _db.Box(fl + tx, fb + ty, fr + tx, ft + ty)
            foot_region.insert(box)
            total += box.area()
        foot_region.merge()
        if foot_region.area() != total:
            plan.problems.append(
                "instance footprints overlap (pitch smaller than footprint?)")
        label_area.merge()
        if not slot_mode and not (label_area & foot_region).is_empty():
            plan.problems.append(
                "labels overlap instance footprints; adjust label.offset_um")

    payload = plan.payload()
    plan.plan_hash = _canonical_plan_hash(payload, {
        "executor": {"name": "klink.array_labeled", "version": 2},
        "source_cell": plan.source_cell,
        "text_generator": plan.text_generator,
        "numbering": {k: numbering[k] for k in sorted(numbering)},
    })
    return plan

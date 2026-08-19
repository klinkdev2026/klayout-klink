"""Offline unit tests for `klink.domains.layoutintent.planner` (the
deterministic array_labeled planner, executor "klink.array_labeled" v2).

Pure geometry, offline-testable with the pip ``klayout`` package (no
KLayout app/client/view involved) -- same pattern as
`test_region_geom.py`. Each test guards ONE invariant from the module's own
contract list (docs/REGION_INTENT_DESIGN.md Sec7.2/Sec11.3-v1, restated in
`planner.py`'s module docstring):

* pitch is CENTER-to-center; the first footprint's lower-left corner sits on
  the region bbox lower-left anchor; candidate sites enumerate y-asc x-asc
  on that integer-DBU grid.
* a site is accepted only if the instance footprint AND its label's actual
  polygon text lie fully inside the region and clear of every obstacle
  polygon -- a partially-outside label REJECTS THE WHOLE SITE, it is never
  silently clipped or dropped on its own.
* numbering traversal (order/start) is delegated to
  klink.domains.fabrication.sites.number_sites: "top_down" numbers the
  highest row first (reading order), "bottom_up" numbers row 0 first; both
  honor an explicit `start` offset.
* labels are REAL polygons (klayout TextGenerator default font, holes
  included for glyphs like 0/8) so preview and apply are byte-identical.
* plan.payload() is the typed contract consumed by
  intent.apply_managed_plan: shapes carry polygon_dbu/holes_dbu, instances
  carry child/trans_dbu, root instance sits at [0, 0].
* plan.plan_hash is a canonical hash over that payload plus numbering/exec
  metadata -- deterministic for identical inputs, sensitive to numbering
  changes.
* plan-level validators (pitch-smaller-than-footprint overlap, label/
  footprint overlap) land in plan.problems, which callers must treat as an
  apply-blocking signal.
* every required key in `numbering`/`label` raises PlanError with a
  non-empty, instructive `.hint` instead of KeyError/crash.

Fixture note on the shared label offset: the region's bottom edge is, by
construction, flush with the FIRST (row 0) footprint's bottom edge (the
"first footprint anchored at region bbox lower-left" contract). A label
offset that pushes the label DOWN from the footprint center by more than
half the footprint height (e.g. offset_um=[0, -6.0] against an 8um-tall
footprint) therefore always pushes row 0's label below the region for ANY
region height -- there is no region big enough to save it. The shared
"full grid" fixture below instead offsets the label UPWARD
(offset_um=[0, 6.0]), which clears the footprint without ever leaving the
region for the parameters used here; `test_label_containment` below
deliberately reuses the DOWNWARD offset against a squeezed region to
exercise the label-outside-region rejection path on purpose.
"""

from __future__ import annotations

import pytest

pytest.importorskip("klayout.db", reason="klayout pip package not installed")

import klayout.db as db  # noqa: E402

from klink.domains.layoutintent.planner import (  # noqa: E402
    PlanError,
    plan_array_labeled,
)

# ---------------------------------------------------------------------
# shared fixture pieces
# ---------------------------------------------------------------------

DBU_UM = 0.001
REGION_HULL = [[0, 0], [100000, 0], [100000, 60000], [0, 60000]]  # 100 x 60 um
FOOTPRINT = [0, 0, 8000, 8000]  # 8 x 8 um, origin at its own lower-left
PITCH = [20, 20]


def _numbering(**overrides) -> dict:
    base = {"prefix": "S", "width": 3, "start": 1, "order": "top_down"}
    base.update(overrides)
    return base


def _label(**overrides) -> dict:
    # offset UPWARD -- see module docstring for why "downward" would break
    # the full-grid fixtures specifically (row 0 is flush with the region
    # bottom edge by the anchoring contract).
    base = {"layer": "20/0", "height_um": 2.0, "offset_um": [0.0, 6.0]}
    base.update(overrides)
    return base


def _plan(
    *,
    region_hull_dbu=None,
    region_holes_dbu=None,
    obstacles_dbu=None,
    footprint_bbox_dbu=None,
    pitch_um=None,
    numbering=None,
    label=None,
    source_cell="CELLX",
):
    return plan_array_labeled(
        region_hull_dbu=region_hull_dbu if region_hull_dbu is not None else REGION_HULL,
        region_holes_dbu=region_holes_dbu or [],
        dbu_um=DBU_UM,
        obstacles_dbu=obstacles_dbu or [],
        source_cell=source_cell,
        footprint_bbox_dbu=footprint_bbox_dbu if footprint_bbox_dbu is not None else FOOTPRINT,
        pitch_um=pitch_um if pitch_um is not None else PITCH,
        numbering=numbering if numbering is not None else _numbering(),
        label=label if label is not None else _label(),
    )


def _by_rowcol(plan) -> dict:
    return {(inst["row"], inst["col"]): inst["site_id"] for inst in plan.instances}


def _region_of_hull(hull_dbu) -> "db.Region":
    region = db.Region()
    region.insert(db.Polygon([db.Point(int(x), int(y)) for x, y in hull_dbu]))
    return region


def _labels_region(plan) -> "db.Region":
    region = db.Region()
    for label in plan.labels:
        for poly in label["polygons_dbu"]:
            p = db.Polygon([db.Point(x, y) for x, y in poly["hull"]])
            for hole in poly["holes"]:
                p.insert_hole([db.Point(x, y) for x, y in hole])
            region.insert(p)
    return region


# ---------------------------------------------------------------------
# 1. full grid enumeration
# ---------------------------------------------------------------------


def test_full_grid_counts():
    plan = _plan()
    # cols = (100000-8000)//20000 + 1 = 5, rows = (60000-8000)//20000 + 1 = 3
    assert plan.rows == 3
    assert plan.cols == 5
    assert len(plan.instances) == 15
    assert plan.rejected == []
    assert plan.problems == []


# ---------------------------------------------------------------------
# 2/3. numbering order + start semantics (delegated to
# klink.domains.fabrication.sites.number_sites)
# ---------------------------------------------------------------------


def test_numbering_top_down_reading_order():
    plan = _plan(numbering=_numbering(order="top_down", start=1))
    ids = _by_rowcol(plan)
    # top_down numbers the HIGHEST row first -> row 2 (top) is S001..S005
    assert ids[(2, 0)] == "S001"
    assert ids[(2, 4)] == "S005"
    # row 0 (bottom) is numbered last -> bottom-right is S015
    assert ids[(0, 4)] == "S015"


def test_numbering_bottom_up_and_start():
    plan = _plan(numbering=_numbering(order="bottom_up", start=101))
    ids = _by_rowcol(plan)
    assert ids[(0, 0)] == "S101"
    assert ids[(2, 4)] == "S115"


# ---------------------------------------------------------------------
# 4. first footprint anchored at region bbox lower-left
# ---------------------------------------------------------------------


def test_first_footprint_anchored_lower_left():
    plan = _plan()
    row0col0 = next(
        inst for inst in plan.instances if inst["row"] == 0 and inst["col"] == 0
    )
    assert row0col0["trans_dbu"] == [0, 0]

    fl, fb, fr, ft = FOOTPRINT
    tx, ty = row0col0["trans_dbu"]
    foot = _region_of_hull(
        [[fl + tx, fb + ty], [fr + tx, fb + ty], [fr + tx, ft + ty], [fl + tx, ft + ty]]
    )
    region = _region_of_hull(REGION_HULL)
    assert (foot - region).is_empty()


# ---------------------------------------------------------------------
# 5. SAFETY: an obstacle rejects only the site it actually hits, and no
# accepted label is allowed to overlap it.
# ---------------------------------------------------------------------


def test_obstacle_rejects_site():
    obstacle = {
        "hull_dbu": [[0, 0], [9000, 0], [9000, 9000], [0, 9000]],
        "holes_dbu": [],
    }
    plan = _plan(obstacles_dbu=[obstacle])

    assert len(plan.instances) == 14
    assert plan.rejected == [{"row": 0, "col": 0, "reason": "footprint_hits_obstacle"}]

    obstacle_region = _region_of_hull(obstacle["hull_dbu"])
    labels_region = _labels_region(plan)
    assert (labels_region & obstacle_region).is_empty()


# ---------------------------------------------------------------------
# 6. SAFETY: a label that would land partially outside the region rejects
# the whole site (it is never silently clipped).
# ---------------------------------------------------------------------


def test_label_containment():
    small_hull = [[0, 0], [8500, 0], [8500, 8500], [0, 8500]]
    # DOWNWARD offset on purpose: footprint bottom is flush with the region
    # bottom (anchoring contract), so shifting the label further down by
    # more than half the footprint height guarantees it exits below y=0.
    label = _label(offset_um=[0.0, -6.0])

    plan = _plan(region_hull_dbu=small_hull, label=label)

    assert plan.instances == []
    assert plan.rows == 1 and plan.cols == 1
    assert plan.rejected == [{"row": 0, "col": 0, "reason": "label_outside_region"}]


# ---------------------------------------------------------------------
# 7. labels are real polygons, not filled blobs -- glyphs like 0/8 carry
# holes.
# ---------------------------------------------------------------------


def test_labels_are_real_polygons_with_holes():
    plan = _plan()
    label = next(entry for entry in plan.labels if entry["site_id"] == "S008")

    for poly in label["polygons_dbu"]:
        assert "hull" in poly and "holes" in poly
        assert len(poly["hull"]) >= 3

    # verified interactively (klayout.db TextGenerator.default_generator()):
    # digits '0' and '8' in "S008" DO produce polygons with non-empty holes
    # (hole counts observed: 0,0,0,1,1,2 across the merged glyph polygons).
    assert any(poly["holes"] for poly in label["polygons_dbu"])


# ---------------------------------------------------------------------
# 8. site-id uniqueness / numbering range
# ---------------------------------------------------------------------


def test_label_uniqueness_and_range():
    plan = _plan(numbering=_numbering(order="top_down", start=1))
    ids = [inst["site_id"] for inst in plan.instances]

    assert len(ids) == len(plan.instances)
    assert len(set(ids)) == len(ids)
    assert set(ids) == {f"S{i:03d}" for i in range(1, 16)}


# ---------------------------------------------------------------------
# 9. plan.payload() -- the typed contract for intent.apply_managed_plan
# ---------------------------------------------------------------------


def test_payload_shape():
    plan = _plan()
    payload = plan.payload()

    assert set(payload.keys()) == {"shapes", "instances", "root_trans_dbu"}
    assert payload["root_trans_dbu"] == [0, 0]

    for shape in payload["shapes"]:
        assert shape["layer"] == "20/0"
        assert len(shape["polygon_dbu"]) >= 3
        assert isinstance(shape["holes_dbu"], list)

    assert len(payload["instances"]) == len(plan.instances)
    for inst in payload["instances"]:
        assert inst["child"] == plan.source_cell
        assert len(inst["trans_dbu"]) == 2
        assert all(isinstance(v, int) for v in inst["trans_dbu"])


# ---------------------------------------------------------------------
# 10. plan_hash determinism + sensitivity
# ---------------------------------------------------------------------


def test_plan_hash_deterministic_and_sensitive():
    plan_a = _plan()
    plan_b = _plan()
    assert plan_a.plan_hash == plan_b.plan_hash
    assert plan_a.plan_hash.startswith("sha256:")

    plan_c = _plan(numbering=_numbering(start=2))
    assert plan_a.plan_hash != plan_c.plan_hash


# ---------------------------------------------------------------------
# 11. pitch smaller than footprint flags overlap as a plan problem
# ---------------------------------------------------------------------


def test_pitch_smaller_than_footprint_flags_overlap():
    plan = _plan(pitch_um=[5, 5])
    assert any("overlap" in problem for problem in plan.problems)


# ---------------------------------------------------------------------
# 12. required params are instructive, not a crash
# ---------------------------------------------------------------------


def test_required_params_instructive():
    label_missing_height = {"layer": "20/0", "offset_um": [0.0, 6.0]}
    with pytest.raises(PlanError) as excinfo:
        _plan(label=label_missing_height)
    assert excinfo.value.hint

    numbering_missing_order = {"prefix": "S", "width": 3, "start": 1}
    with pytest.raises(PlanError) as excinfo:
        _plan(numbering=numbering_missing_order)
    assert excinfo.value.hint


# ---------------------------------------------------------------------------
# I1b follow-up: full instance support (rotation/mirror) + custom obstacles
# (clearance margin). Added after owner feedback: what gets arrayed is
# INSTANCES of arbitrary custom cells, and obstacles are whatever the user
# declares — nothing hardcoded.
# ---------------------------------------------------------------------------


def _full_kwargs(**overrides):
    kwargs = dict(
        region_hull_dbu=REGION_HULL,
        region_holes_dbu=[],
        dbu_um=DBU_UM,
        obstacles_dbu=[],
        source_cell="CELLX",
        footprint_bbox_dbu=FOOTPRINT,
        pitch_um=PITCH,
        numbering=_numbering(),
        label=_label(),
    )
    kwargs.update(overrides)
    return kwargs


def test_rotation_swaps_footprint_and_reaches_payload():
    """A 90-degree rotation of a non-square cell swaps the footprint's
    width/height (changing the grid) and must land verbatim on every
    payload instance."""
    # 36x8 um in a 100x60 region at pitch 20: flat = 3 rows x 4 cols,
    # rotated (8x36) = 2 rows x 5 cols — the swap must show up in the grid
    flat = plan_array_labeled(
        **_full_kwargs(footprint_bbox_dbu=[0, 0, 36000, 8000]))
    rotated = plan_array_labeled(
        **_full_kwargs(footprint_bbox_dbu=[0, 0, 36000, 8000],
                       rotation_deg=90))
    assert (flat.rows, flat.cols) == (3, 4)
    assert (rotated.rows, rotated.cols) == (2, 5)
    payload = rotated.payload()
    assert payload["instances"], "rotated plan placed nothing"
    for inst in payload["instances"]:
        assert inst["rotation_deg"] == 90
        assert inst["mirror"] is False


def test_rotation_rejects_arbitrary_angle():
    with pytest.raises(PlanError) as exc:
        plan_array_labeled(**_full_kwargs(rotation_deg=45))
    assert exc.value.hint


def test_clearance_grows_obstacles():
    """An obstacle that misses every footprint must start rejecting sites
    once the clearance margin makes it reach one."""
    sliver = [{
        "hull_dbu": [[9000, 0], [10000, 0], [10000, 8000], [9000, 8000]],
        "holes_dbu": [],
    }]
    no_clear = plan_array_labeled(**_full_kwargs(obstacles_dbu=sliver))
    with_clear = plan_array_labeled(
        **_full_kwargs(obstacles_dbu=sliver, clearance_um=2.0))
    assert len(no_clear.instances) > len(with_clear.instances)
    reasons = {r["reason"] for r in with_clear.rejected}
    assert "footprint_hits_obstacle" in reasons


# ---------------------------------------------------------------------------
# label SLOT mode: the user circles a text slot INSIDE the unit cell; every
# array copy gets its own number auto-fitted into that slot, which moves
# (and rotates) with the instance.
# ---------------------------------------------------------------------------

SLOT = [1000, 1000, 7000, 3000]  # 6 x 2 um slot near the cell bottom


def _slot_label(**overrides) -> dict:
    base = {"layer": "20/0", "slot_bbox_dbu": list(SLOT), "margin_um": 0.0}
    base.update(overrides)
    return base


def _label_bbox(label_entry):
    xs = [p[0] for poly in label_entry["polygons_dbu"] for p in poly["hull"]]
    ys = [p[1] for poly in label_entry["polygons_dbu"] for p in poly["hull"]]
    return min(xs), min(ys), max(xs), max(ys)


def test_slot_mode_autofit_inside_translated_slot():
    plan = plan_array_labeled(**_full_kwargs(label=_slot_label()))
    assert plan.instances and plan.problems == []
    assert plan.label_height_um > 0
    by_id = {i["site_id"]: i for i in plan.instances}
    for entry in plan.labels:
        tx, ty = by_id[entry["site_id"]]["trans_dbu"]
        world = (SLOT[0] + tx, SLOT[1] + ty, SLOT[2] + tx, SLOT[3] + ty)
        l, b, r, t = _label_bbox(entry)
        assert world[0] <= l and b >= world[1] and r <= world[2] \
            and t <= world[3], \
            "label %s bbox %s escaped its slot %s" % (
                entry["site_id"], (l, b, r, t), world)


def test_slot_mode_follows_rotation_with_upright_text():
    plan = plan_array_labeled(
        **_full_kwargs(label=_slot_label(), rotation_deg=90))
    assert plan.instances and plan.problems == []
    db_ = db
    by_id = {i["site_id"]: i for i in plan.instances}
    op = db_.Trans(1, False, db_.Vector(0, 0))
    for entry in plan.labels:
        tx, ty = by_id[entry["site_id"]]["trans_dbu"]
        world = db_.Box(*SLOT).transformed(op).moved(tx, ty)
        l, b, r, t = _label_bbox(entry)
        assert world.left <= l and b >= world.bottom \
            and r <= world.right and t <= world.top
        # upright text: taller-than-wide slot after rotation, but the text
        # bbox itself stays wider than tall (not rotated with the instance)
        assert (r - l) > (t - b)


def test_slot_outside_footprint_rejected():
    with pytest.raises(PlanError) as exc:
        plan_array_labeled(**_full_kwargs(
            label=_slot_label(slot_bbox_dbu=[6000, 6000, 12000, 9000])))
    assert "footprint" in str(exc.value)
    assert exc.value.hint


def test_slot_min_height_refuses_tiny_slot():
    with pytest.raises(PlanError) as exc:
        plan_array_labeled(**_full_kwargs(
            label=_slot_label(slot_bbox_dbu=[1000, 1000, 2000, 1400],
                              min_height_um=1.0)))
    assert "min_height_um" in str(exc.value)


# ---------------------------------------------------------------------------
# PATTERN numbering: arbitrary user-defined grid notations ("1+2", "1-3",
# "A1", ...). The engine lives in fabrication.sites.pattern_site_ids; the
# planner only delegates — the pattern TEXT is always caller-supplied.
# ---------------------------------------------------------------------------


def test_pattern_row_plus_col_top_down():
    plan = plan_array_labeled(**_full_kwargs(numbering={
        "pattern": "{row}+{col}",
        "row": {"start": 1, "order": "top_down"},
        "col": {"start": 1},
    }))
    assert plan.problems == []
    ids = _by_rowcol(plan)
    # 3 rows: top row (row index 2) displays as row 1
    assert ids[(2, 0)] == "1+1"
    assert ids[(2, 4)] == "1+5"
    assert ids[(0, 0)] == "3+1"


def test_pattern_alpha_rows():
    plan = plan_array_labeled(**_full_kwargs(numbering={
        "pattern": "{row:A}{col}",
        "row": {"start": 1, "order": "top_down"},
        "col": {"start": 1},
    }))
    ids = _by_rowcol(plan)
    assert ids[(2, 0)] == "A1"
    assert ids[(0, 4)] == "C5"


def test_pattern_duplicates_flagged():
    plan = plan_array_labeled(
        **_full_kwargs(numbering={"pattern": "{row}"}))
    assert any("duplicate" in p for p in plan.problems)


def test_pattern_unknown_field_instructive():
    with pytest.raises(PlanError) as exc:
        plan_array_labeled(
            **_full_kwargs(numbering={"pattern": "{wafer}-{col}"}))
    assert exc.value.hint


def test_pattern_slot_fit_uses_longest_id():
    # pattern ids vary in length (1+1 vs 10+10 style): the slot auto-fit
    # must key on the LONGEST id so every label fits
    plan = plan_array_labeled(**_full_kwargs(
        label=_slot_label(),
        pitch_um=[10, 10],  # denser grid -> two-digit row/col values
        numbering={"pattern": "{row}+{col}",
                   "row": {"start": 9, "order": "top_down"},
                   "col": {"start": 9}},
    ))
    assert plan.problems == []
    by_id = {i["site_id"]: i for i in plan.instances}
    for entry in plan.labels:
        tx, ty = by_id[entry["site_id"]]["trans_dbu"]
        world = (SLOT[0] + tx, SLOT[1] + ty, SLOT[2] + tx, SLOT[3] + ty)
        l, b, r, t = _label_bbox(entry)
        assert world[0] <= l and r <= world[2] and world[1] <= b \
            and t <= world[3]

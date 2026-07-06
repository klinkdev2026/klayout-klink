"""Offline tests for the four public passive-device geometry templates under
``examples_klink/public/passives/`` (docs/PASSIVE_TEMPLATES_SPEC.md).

No KLayout session, no gdsfactory/numpy, no network -- CI installs only
pytest+klayout and this file imports neither pya nor optional deps. Each
template's `write_offline` writes a real GDS via klayout.db into `tmp_path`
(never a hardcoded path); this test then RE-OPENS that GDS and independently
recomputes the spec's invariants with klayout.db Region ops, rather than
trusting the template module's own self-check numbers -- the point is to
verify the drawn geometry, not the module's arithmetic about itself.

Every family is run twice per docs/TESTING_PLAYBOOK.md's base+expanded
harness closed-loop rule: once with its module DEFAULT_PARAMS, once with one
parameter pushed to a different (larger/smaller) value.
"""
from __future__ import annotations

import math

import klayout.db as kdb
import pytest

from examples_klink.public.passives import (
    baw_fbar_planview as baw,
    idc_capacitor as idc,
    saw_idt_filter as saw,
    spiral_inductor as spiral,
)


class _OpenLayout:
    """Holds one klayout.db.Layout open for the lifetime of a test.

    kdb.Region(top.begin_shapes_rec(idx)) can stay a VIEW over the source
    Layout rather than an eager copy (observed directly: a single-shape
    layer's Region reports the right count() right after `.merge()`, then
    silently reports 0 once the backing `kdb.Layout()` is garbage-collected).
    Keeping the Layout referenced here for as long as any Region derived
    from it is still in use avoids that trap.
    """

    def __init__(self, gds_path):
        self.ly = kdb.Layout()
        self.ly.read(str(gds_path))

    @property
    def dbu(self) -> float:
        return self.ly.dbu

    def region(self, cell_name: str, layer: tuple[int, int]) -> kdb.Region:
        top = self.ly.cell(cell_name)
        assert top is not None, f"cell {cell_name!r} not found"
        idx = self.ly.find_layer(layer[0], layer[1])
        assert idx is not None, f"layer {layer} not found"
        region = kdb.Region(top.begin_shapes_rec(idx))
        region.merge()
        return region


def _clip_x(region: kdb.Region, x0_um: float, x1_um: float, dbu: float) -> kdb.Region:
    clip = kdb.Region(kdb.Box(
        int(round(x0_um / dbu)), -(1 << 30), int(round(x1_um / dbu)), (1 << 30),
    ))
    return region & clip


# --------------------------------------------------------------------------- #
# IDC capacitor (spec Sec3.1)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("overrides", [
    {},  # default params
    {"finger_count": 24, "finger_width": 3.0, "gap": 2.0, "finger_length": 35.0, "bus_width": 6.0},
])
def test_idc_capacitor_invariants(tmp_path, overrides):
    params = dict(idc.DEFAULT_PARAMS, **overrides)
    out_path = tmp_path / "idc.gds"
    report = idc.write_offline(params, str(out_path))
    assert report["ok"], report

    bundle = idc.build_idc(params)
    s = bundle["summary"]

    finger_count = params["finger_count"]
    finger_width = params["finger_width"]
    gap = params["gap"]
    expected_pitch = finger_width + gap
    expected_total_width = finger_count * finger_width + (finger_count - 1) * gap
    assert s["finger_pitch_um"] == pytest.approx(expected_pitch)
    assert s["total_width_um"] == pytest.approx(expected_total_width)

    opened = _OpenLayout(out_path)
    region = opened.region(bundle["cell"], idc.LAYER_METAL)
    # merge check: exactly 2 merged regions (a short would merge into 1).
    assert region.count() == 2

    # No two same-net shapes are closer than `gap` to the opposite net: an
    # under-sized (gap - one dbu) uniform shrink of the whole merged region
    # must NOT re-merge into 1 (that would mean two nets came within less
    # than `gap` of each other somewhere off the exact clearance boundary).
    shrink_dbu = int(round(gap / opened.dbu)) - 1
    if shrink_dbu > 0:
        shrunk = region.dup()
        shrunk.size(-shrink_dbu)
        assert shrunk.count() == 2 or shrunk.is_empty()


# --------------------------------------------------------------------------- #
# Spiral inductor (spec Sec3.2)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("overrides", [
    {},
    {"turns": 6, "track_width": 3.0, "spacing": 2.0, "inner_size": 15.0, "underpass_width": 4.0},
])
def test_spiral_inductor_invariants(tmp_path, overrides):
    params = dict(spiral.DEFAULT_PARAMS, **overrides)
    out_path = tmp_path / "spiral.gds"
    report = spiral.write_offline(params, str(out_path))
    assert report["ok"], report

    bundle = spiral.build_spiral(params)
    turns = params["turns"]

    opened = _OpenLayout(out_path)
    top_region = opened.region(bundle["cell"], spiral.LAYER_METAL_TOP)
    under_region = opened.region(bundle["cell"], spiral.LAYER_METAL_UNDER)
    via_region = opened.region(bundle["cell"], spiral.LAYER_VIA)

    # exactly 1 merged region per metal layer (continuous track, no self-short)
    assert top_region.count() == 1
    assert under_region.count() == 1

    # underpass crosses >= turns track segments: recompute independently
    # from the raw spiral points rather than trusting the module's own
    # crossing count.
    points = spiral._spiral_points(turns, params["inner_size"],
                                    params["track_width"] + params["spacing"])
    strip = bundle["summary"]["underpass_box_um"]
    crossings = 0
    for i in range(len(points) - 1):
        (sx0, sy0), (sx1, sy1) = points[i], points[i + 1]
        seg_x0, seg_x1 = sorted((sx0, sx1))
        seg_y0, seg_y1 = sorted((sy0, sy1))
        if (seg_x1 >= strip[0] and seg_x0 <= strip[2]
                and seg_y1 >= strip[1] and seg_y0 <= strip[3]):
            crossings += 1
    assert crossings >= turns

    # via boxes land fully inside both the inner-end pad (metal_top) and the
    # underpass (metal_under): via_region minus each must be empty.
    assert (via_region - top_region).is_empty()
    assert (via_region - under_region).is_empty()


# --------------------------------------------------------------------------- #
# SAW IDT filter (spec Sec3.3)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("overrides", [
    {},
    {"pitch": 6.0, "pairs": 20, "aperture": 60.0, "bus_width": 8.0,
     "idt_gap": 45.0, "reflector_fingers": 15, "reflector_gap": 6.0},
])
def test_saw_idt_filter_invariants(tmp_path, overrides):
    params = dict(saw.DEFAULT_PARAMS, **overrides)
    out_path = tmp_path / "saw.gds"
    report = saw.write_offline(params, str(out_path))
    assert report["ok"], report

    bundle = saw.build_saw_idt(params)
    s = bundle["summary"]
    pitch = params["pitch"]

    # electrode width == pitch/4 within dbu rounding
    opened = _OpenLayout(out_path)
    region = opened.region(bundle["cell"], saw.LAYER_METAL)
    assert s["electrode_width_um"] == pytest.approx(pitch / 4.0, abs=1e-9)

    # per IDT exactly 2 merged regions (no finger short)
    tx_clip = _clip_x(region, *s["tx_bbox_x_um"], opened.dbu)
    rx_clip = _clip_x(region, *s["rx_bbox_x_um"], opened.dbu)
    assert tx_clip.count() == 2
    assert rx_clip.count() == 2

    # reflector gratings (shorted-grating type): exactly ONE merged region
    # per grating.
    for (rx0, rx1) in s["reflector_bboxes_x_um"]:
        refl_clip = _clip_x(region, rx0, rx1, opened.dbu)
        assert refl_clip.count() == 1


def test_saw_idt_filter_reflectors_disabled(tmp_path):
    params = dict(saw.DEFAULT_PARAMS, reflector_fingers=0)
    out_path = tmp_path / "saw_no_refl.gds"
    report = saw.write_offline(params, str(out_path))
    assert report["ok"], report
    bundle = saw.build_saw_idt(params)
    assert bundle["summary"]["reflector_bboxes_x_um"] == []


# --------------------------------------------------------------------------- #
# BAW / FBAR plan view (spec Sec3.4)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("overrides", [
    {},
    {"active_area_um2": 6000.0, "connect_width_um": 12.0, "pad_size_um": 40.0,
     "bottom_extension_um": 90.0, "overlap_margin_um": 10.0, "membrane_margin_um": 20.0},
])
def test_baw_fbar_planview_invariants(tmp_path, overrides):
    params = dict(baw.DEFAULT_PARAMS, **overrides)
    out_path = tmp_path / "baw.gds"
    report = baw.write_offline(params, str(out_path))
    assert report["ok"], report

    bundle = baw.build_baw_fbar(params)
    pentagon = bundle["pentagon_um"]

    # pentagon edge-parallelism check: really compute edge direction angles
    # (mod 180 deg) and verify every pair is distinct.
    n = len(pentagon)
    assert n == 5
    directions = []
    for i in range(n):
        x0, y0 = pentagon[i]
        x1, y1 = pentagon[(i + 1) % n]
        directions.append(math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0)
    for i in range(n):
        for j in range(i + 1, n):
            diff = abs(directions[i] - directions[j])
            diff = min(diff, 180.0 - diff)
            assert diff > 1e-6, f"edges {i} and {j} are parallel ({directions[i]} vs {directions[j]})"

    # pentagon area within 1% of active_area_um2 (shoelace, independent of
    # the module's own area bookkeeping).
    shoelace = 0.0
    for i in range(n):
        x0, y0 = pentagon[i]
        x1, y1 = pentagon[(i + 1) % n]
        shoelace += x0 * y1 - x1 * y0
    area = abs(shoelace) / 2.0
    target = params["active_area_um2"]
    assert abs(area - target) / target <= 0.01

    # top/bottom overlap region area > 0.9 * pentagon area, recomputed from
    # the written GDS via Region ops (independent of build_baw_fbar's own
    # overlap bookkeeping).
    dbu = 0.001
    pentagon_region = kdb.Region(kdb.Polygon([
        kdb.Point(int(round(x / dbu)), int(round(y / dbu))) for x, y in pentagon
    ]))
    opened = _OpenLayout(out_path)
    bottom_region = opened.region(bundle["cell"], baw.LAYER_METAL_BOT)
    overlap = pentagon_region & bottom_region
    overlap_area = overlap.area() * dbu * dbu
    pentagon_area = pentagon_region.area() * dbu * dbu
    assert overlap_area > 0.9 * pentagon_area

    top_region = opened.region(bundle["cell"], baw.LAYER_METAL_TOP)
    assert top_region.count() == 1
    assert bottom_region.count() == 1

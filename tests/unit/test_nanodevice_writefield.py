from __future__ import annotations

import importlib.util

import pytest

from klink.domains.nanodevice.ebl.patching import generate_wf_patches
from klink.domains.nanodevice.ebl.writefield import CrossingWindow, plan_writefields

def _spec_present(name: str) -> bool:
    # find_spec("klayout.db") raises ModuleNotFoundError when the PARENT
    # package is absent, and finders may raise other errors in bare envs
    # (e.g. ValueError for a module with __spec__ unset), so guard broadly.
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


KDB_PRESENT = _spec_present("klayout.db") or _spec_present("pya")


def test_writefield_grid_obstacles_leave_crossing_window_gap():
    plan = plan_writefields(
        [0, 0, 200, 100],
        writefield_size_um=100,
        stitch_margin_um=1,
        crossing_windows=[CrossingWindow("x", 100, 50, 20, "X_MID", ("sig0",))],
    )

    assert plan.report["field_count"] == 2
    assert plan.report["boundary_count"] == 1
    assert plan.report["window_count"] == 1
    assert plan.obstacle_boxes_um == [[99.0, 0.0, 101.0, 40.0], [99.0, 60.0, 101.0, 100.0]]
    anchor = plan.corridor_anchor_specs[0]
    assert anchor["id"] == "X_MID"
    assert anchor["kind"] == "corridor"
    assert anchor["net"] == "sig0"


def test_writefield_auto_windows_report_small_field_warning():
    plan = plan_writefields(
        [0, 0, 200, 200],
        writefield_size_um=100,
        stitch_margin_um=1,
        auto_crossing_window_span_um=10,
    )

    assert plan.report["field_count"] == 4
    assert plan.report["boundary_count"] == 2
    assert plan.report["window_count"] == 2
    assert any("small_writefields" in w for w in plan.report["warnings"])


def test_patching_generates_boundary_centered_patch_without_crossing_other_boundaries():
    plan = plan_writefields([0, 0, 300, 100], writefield_size_um=100, stitch_margin_um=1)
    result = generate_wf_patches([[95, 40, 105, 60], [198, 40, 202, 60]], plan, patch_size_um=6, patch_layer="113/0")

    assert result["report"]["patch_count"] == 2
    assert result["patch_boxes_um"][0] == pytest.approx([97, 47, 103, 53])
    assert result["shape_items"][0]["layer"] == 113


def test_patching_skips_patch_that_would_cross_a_second_boundary():
    plan = plan_writefields([0, 0, 200, 200], writefield_size_um=100, stitch_margin_um=1)
    result = generate_wf_patches([[98, 98, 102, 102]], plan, patch_size_um=8)

    assert result["patch_boxes_um"] == []


@pytest.mark.skipif(not KDB_PRESENT, reason="klayout.db not installed")
def test_patching_reads_electrode_boxes_from_gds(tmp_path):
    import klayout.db as kdb

    gds_path = tmp_path / "electrodes.gds"
    layout = kdb.Layout()
    layout.dbu = 0.001
    top = layout.create_cell("TOP")
    layer_index = layout.layer(kdb.LayerInfo(10, 0))
    top.shapes(layer_index).insert(kdb.Box(95000, 40000, 105000, 60000))
    layout.write(str(gds_path))

    plan = plan_writefields([0, 0, 200, 100], writefield_size_um=100, stitch_margin_um=1)
    result = generate_wf_patches(gds_path, plan, patch_size_um=6, electrode_layer="10/0")

    assert result["report"]["electrode_count"] == 1
    assert len(result["patch_boxes_um"]) == 1
    assert result["patch_boxes_um"][0] == pytest.approx([97, 47, 103, 53])

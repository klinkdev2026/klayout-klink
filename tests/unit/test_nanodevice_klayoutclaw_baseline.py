from __future__ import annotations

import json
from pathlib import Path

import pytest

from klink.domains.nanodevice.flake import (
    LayoutPayload,
    StageOutput,
    layout_payload_from_traces,
    trace_bundle_from_path,
    trace_material_summary,
    traces_to_polygon_items,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRECOMPUTED_TRACES = PROJECT_ROOT / "KlayoutClaw-main" / "tests_resources" / "ml08" / "precomputed" / "traces_gds.json"
LOCAL_BASELINE_TRACES = PROJECT_ROOT / "test_outputs" / "klayoutclaw_baseline" / "traces.json"
STAGE_COMPARE_TRACES = PROJECT_ROOT / "test_outputs" / "klayoutclaw_stage_compare" / "combine" / "traces.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.skipif(not PRECOMPUTED_TRACES.exists(), reason="KlayoutClaw test resources not present")
def test_klayoutclaw_precomputed_traces_convert_to_polygon_items():
    traces = _load_json(PRECOMPUTED_TRACES)

    summary = trace_material_summary(traces)
    assert set(summary) == {"top_hBN", "graphene", "bottom_hBN", "graphite"}
    assert summary["top_hBN"]["count"] == 1
    assert summary["graphene"]["count"] == 1
    assert summary["bottom_hBN"]["area_um2"] == pytest.approx(6873.674)
    assert summary["graphite"]["point_counts"] == [16]

    items = traces_to_polygon_items(traces, coordinate="gds")
    assert len(items) == 4
    assert [(item["material"], item["layer"], item["datatype"]) for item in items] == [
        ("top_hBN", 10, 0),
        ("graphene", 11, 0),
        ("bottom_hBN", 12, 0),
        ("graphite", 13, 0),
    ]
    assert all(item["kind"] == "polygon" for item in items)
    assert all(len(item["points_um"]) >= 16 for item in items)


@pytest.mark.skipif(not LOCAL_BASELINE_TRACES.exists(), reason="local KlayoutClaw baseline has not been generated")
def test_local_klayoutclaw_pipeline_baseline_matches_expected_trace_contract():
    traces = _load_json(LOCAL_BASELINE_TRACES)

    summary = trace_material_summary(traces)
    assert summary["top_hBN"] == {"count": 1, "area_um2": 3154.381, "point_counts": [45]}
    assert summary["graphene"] == {"count": 1, "area_um2": 1001.568, "point_counts": [49]}
    assert summary["bottom_hBN"] == {"count": 1, "area_um2": 6860.617, "point_counts": [45]}
    assert summary["graphite"] == {"count": 1, "area_um2": 643.361, "point_counts": [18]}

    items = traces_to_polygon_items(traces)
    assert len(items) == 4
    assert [item["layer"] for item in items] == [10, 11, 12, 13]
    assert [item["source_id"] for item in items] == [1, 2, 3, 4]


@pytest.mark.skipif(not STAGE_COMPARE_TRACES.exists(), reason="local stage compare traces have not been generated")
def test_stage_compare_traces_are_ready_for_live_shape_insert_many():
    traces = _load_json(STAGE_COMPARE_TRACES)

    items = traces_to_polygon_items(traces, coordinate="um")

    assert len(items) == 4
    assert [(item["material"], item["layer"], item["datatype"]) for item in items] == [
        ("top_hBN", 10, 0),
        ("graphene", 11, 0),
        ("bottom_hBN", 12, 0),
        ("graphite", 13, 0),
    ]
    assert all(item["kind"] == "polygon" for item in items)
    assert all(item["points_um"] for item in items)
    assert trace_material_summary(traces)["graphene"] == {
        "count": 1,
        "area_um2": 781.711,
        "point_counts": [36],
    }


def test_flake_artifact_dataclasses_round_trip_without_optional_deps(tmp_path):
    stage = StageOutput(
        material="graphene",
        native_coordinate="top_part",
        mask_path=str(tmp_path / "graphene_mask.png"),
        result_path=str(tmp_path / "graphene_result.json"),
        contour_path=str(tmp_path / "graphene_contour.npy"),
        cmd=["python", "graphene.py"],
        stdout="ok",
        cache_key="abc",
        cache_hit=True,
    )

    assert StageOutput.from_dict(stage.to_dict()) == stage

    payload = LayoutPayload(
        source_path=str(tmp_path / "traces.json"),
        coordinate="um",
        shape_items=[{"kind": "polygon", "layer": 11, "datatype": 0, "points_um": [[0, 0], [1, 0], [1, 1]]}],
    )
    assert payload.to_dict()["shape_item_count"] == 1
    assert LayoutPayload.from_dict(payload.to_dict()) == payload


@pytest.mark.skipif(not STAGE_COMPARE_TRACES.exists(), reason="local stage compare traces have not been generated")
def test_trace_bundle_and_layout_payload_helpers_use_typed_contracts():
    traces = _load_json(STAGE_COMPARE_TRACES)

    bundle = trace_bundle_from_path(STAGE_COMPARE_TRACES)
    payload = layout_payload_from_traces(traces, source_path=STAGE_COMPARE_TRACES, coordinate="um")

    assert bundle.shape_item_count == 4
    assert bundle.summary["top_hBN"]["count"] == 1
    assert payload.coordinate == "um"
    assert payload.shape_item_count == 4
    assert payload.shape_items[1]["material"] == "graphene"

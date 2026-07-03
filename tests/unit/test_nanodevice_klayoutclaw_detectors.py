from __future__ import annotations

import importlib.util
import json
import os
import warnings
from pathlib import Path

import pytest

KLAYOUTCLAW_ROOT = Path(__file__).resolve().parents[2] / "KlayoutClaw-main"
CV2_PRESENT = importlib.util.find_spec("cv2") is not None
NUMPY_PRESENT = importlib.util.find_spec("numpy") is not None
SCIPY_PRESENT = importlib.util.find_spec("scipy") is not None
SKLEARN_PRESENT = importlib.util.find_spec("sklearn") is not None
SKIMAGE_PRESENT = importlib.util.find_spec("skimage") is not None
LOCAL_BASELINE = Path(__file__).resolve().parents[2] / "test_outputs" / "klayoutclaw_baseline"

pytestmark = pytest.mark.detectors


def test_klayoutclaw_engine_descriptors_are_explicit_and_lazy():
    from klink.domains.nanodevice.flake import (
        get_klayoutclaw_engine,
        get_klayoutclaw_stage_script,
        klayoutclaw_detector_path,
        klayoutclaw_stage_script_path,
        list_klayoutclaw_engines,
        list_klayoutclaw_stage_scripts,
    )

    descriptors = list_klayoutclaw_engines()
    assert {(d["engine"], d["material"]) for d in descriptors} == {
        ("b1", "graphene"),
        ("b2", "graphite"),
        ("b2", "graphene"),
        ("b3", "graphite"),
        ("b3", "graphene"),
    }
    assert get_klayoutclaw_engine("b2", "graphite").required_masks == ("host_mask",)
    assert get_klayoutclaw_stage_script("graphene").native_coordinate == "top_part"
    assert str(klayoutclaw_detector_path(KLAYOUTCLAW_ROOT, "b3", "graphene")).endswith(
        "b3_shapetemplate.py"
    )
    assert str(klayoutclaw_stage_script_path(KLAYOUTCLAW_ROOT, "graphene")).endswith("graphene.py")
    assert {d["material"] for d in list_klayoutclaw_stage_scripts()} == {
        "graphite",
        "graphene",
        "bottom_hbn",
        "top_hbn",
    }

    with pytest.raises(ValueError, match="unsupported KlayoutClaw detector"):
        get_klayoutclaw_engine("b1", "graphite")


@pytest.mark.skipif(not KLAYOUTCLAW_ROOT.exists(), reason="KlayoutClaw snapshot not present")
def test_klayoutclaw_wrapper_validates_required_masks_before_loading():
    np = pytest.importorskip("numpy")

    from klink.domains.nanodevice.flake import run_klayoutclaw_detector

    image = np.zeros((16, 16, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="requires mask"):
        run_klayoutclaw_detector(
            KLAYOUTCLAW_ROOT,
            "b2",
            "graphite",
            image,
            pixel_size_um=0.1,
        )


@pytest.mark.skipif(
    not (KLAYOUTCLAW_ROOT.exists() and CV2_PRESENT and NUMPY_PRESENT and SCIPY_PRESENT and SKLEARN_PRESENT),
    reason="KlayoutClaw B2 dependencies/resources not present",
)
def test_klayoutclaw_b2_graphene_smoke_normalizes_output():
    import cv2
    np = pytest.importorskip("numpy")

    from klink.domains.nanodevice.flake import run_klayoutclaw_detector

    image = np.zeros((80, 80, 3), dtype=np.uint8)
    image[:, :] = (70, 70, 70)
    image[20:60, 20:60] = (120, 150, 120)
    image[32:48, 30:55] = (180, 210, 180)
    flake_mask = np.zeros((80, 80), dtype=np.uint8)
    cv2.rectangle(flake_mask, (20, 20), (59, 59), 255, -1)

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Could not find the number of physical cores")
        warnings.filterwarnings("ignore", message="Number of distinct clusters.*")
        result = run_klayoutclaw_detector(
            KLAYOUTCLAW_ROOT,
            "b2",
            "graphene",
            image,
            pixel_size_um=0.2,
            flake_mask=flake_mask,
            layer="11/0",
            target_layer="20/0",
        )

    assert result["report"]["engine"] == "klayoutclaw:b2"
    assert result["report"]["metadata"]["source"] == "KlayoutClaw"
    assert "regions" in result
    assert "shape_items" in result
    assert "ports" in result


@pytest.mark.skipif(
    not (KLAYOUTCLAW_ROOT.exists() and CV2_PRESENT and NUMPY_PRESENT and SKIMAGE_PRESENT),
    reason="KlayoutClaw B3 dependencies/resources not present",
)
def test_klayoutclaw_b3_graphene_smoke_normalizes_output():
    import cv2
    np = pytest.importorskip("numpy")

    from klink.domains.nanodevice.flake import run_klayoutclaw_detector

    image = np.zeros((80, 80, 3), dtype=np.uint8)
    image[:, :] = (80, 80, 80)
    image[18:62, 18:62] = (110, 125, 110)
    image[30:52, 28:56] = (170, 190, 170)
    flake_mask = np.zeros((80, 80), dtype=np.uint8)
    cv2.rectangle(flake_mask, (18, 18), (61, 61), 255, -1)

    result = run_klayoutclaw_detector(
        KLAYOUTCLAW_ROOT,
        "b3",
        "graphene",
        image,
        pixel_size_um=0.2,
        flake_mask=flake_mask,
        layer="11/0",
        target_layer="20/0",
    )

    assert result["report"]["engine"] == "klayoutclaw:b3"
    assert result["report"]["metadata"]["function"] == "detect_graphene"
    assert "regions" in result
    assert "shape_items" in result
    assert "ports" in result


@pytest.mark.skipif(
    not (
        KLAYOUTCLAW_ROOT.exists()
        and LOCAL_BASELINE.exists()
        and CV2_PRESENT
        and NUMPY_PRESENT
        and SCIPY_PRESENT
        and SKLEARN_PRESENT
    ),
    reason="local KlayoutClaw baseline or detector dependencies not present",
)
def test_klayoutclaw_b2_graphite_matches_local_ml08_baseline_area():
    os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")

    import cv2

    from klink.domains.nanodevice.flake import (
        DetectorRunSpec,
        compare_detection_summary,
        normalize_detector_output,
        run_klayoutclaw_detector,
    )

    image = cv2.imread(str(KLAYOUTCLAW_ROOT / "tests_resources" / "ml08" / "full_stack_raw.jpg"))
    host = cv2.imread(str(LOCAL_BASELINE / "top_hbn_mask.png"), cv2.IMREAD_GRAYSCALE)
    reference_mask = cv2.imread(str(LOCAL_BASELINE / "graphite_full.png"), cv2.IMREAD_GRAYSCALE)
    if image is None or host is None or reference_mask is None:
        pytest.skip("ml08 baseline image/masks are incomplete")

    reference = normalize_detector_output(
        {"mask": reference_mask},
        DetectorRunSpec(material="graphite", pixel_size_um=0.087, layer="13/0"),
    )
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Could not find the number of physical cores")
        warnings.filterwarnings("ignore", message="Number of distinct clusters.*")
        result = run_klayoutclaw_detector(
            KLAYOUTCLAW_ROOT,
            "b2",
            "graphite",
            image,
            pixel_size_um=0.087,
            host_mask=host,
            layer="13/0",
        )

    comparison = compare_detection_summary(result, reference, area_rel_tol=0.25)
    assert comparison["ok"] is True
    assert comparison["area_rel_error"] == pytest.approx(0.0995040885)


@pytest.mark.skipif(
    not (KLAYOUTCLAW_ROOT.exists() and LOCAL_BASELINE.exists() and CV2_PRESENT and NUMPY_PRESENT and SKIMAGE_PRESENT),
    reason="local KlayoutClaw baseline or graphene stage dependencies not present",
)
def test_klayoutclaw_graphene_stage_script_matches_native_baseline_area(tmp_path):
    import cv2

    from klink.domains.nanodevice.flake import (
        DetectorRunSpec,
        compare_detection_summary,
        normalize_detector_output,
        run_klayoutclaw_stage_script,
    )

    reference_mask = cv2.imread(str(LOCAL_BASELINE / "graphene_mask.png"), cv2.IMREAD_GRAYSCALE)
    if reference_mask is None:
        pytest.skip("native graphene baseline mask is missing")
    reference = normalize_detector_output(
        {"mask": reference_mask},
        DetectorRunSpec(material="graphene", pixel_size_um=0.087, layer="11/0"),
    )

    result = run_klayoutclaw_stage_script(
        KLAYOUTCLAW_ROOT,
        "graphene",
        image=KLAYOUTCLAW_ROOT / "tests_resources" / "ml08" / "top_part.jpg",
        pixel_size_um=0.087,
        output_dir=tmp_path,
        mirror=True,
        footprint_mask=LOCAL_BASELINE / "footprint_mask.png",
    )

    comparison = compare_detection_summary(result, reference, area_rel_tol=0.25)
    assert result["report"]["engine"] == "klayoutclaw:stage:graphene"
    assert result["stage"]["native_coordinate"] == "top_part"
    assert comparison["ok"] is True
    assert comparison["area_rel_error"] == pytest.approx(0.2170369037)


@pytest.mark.skipif(
    not (
        KLAYOUTCLAW_ROOT.exists()
        and LOCAL_BASELINE.exists()
        and CV2_PRESENT
        and NUMPY_PRESENT
        and SCIPY_PRESENT
        and SKLEARN_PRESENT
        and SKIMAGE_PRESENT
    ),
    reason="local KlayoutClaw baseline or stage dependencies not present",
)
@pytest.mark.parametrize(
    ("material", "baseline_mask", "layer", "kwargs", "expected_rel_err", "count_sensitive"),
    [
        (
            "graphite",
            "graphite_mask.png",
            "13/0",
            {"image": KLAYOUTCLAW_ROOT / "tests_resources" / "ml08" / "bottom_part.jpg"},
            0.0923423165,
            True,
        ),
        (
            "bottom_hbn",
            "bottom_hbn_mask.png",
            "12/0",
            {
                "image": KLAYOUTCLAW_ROOT / "tests_resources" / "ml08" / "bottom_part.jpg",
                "target_image": KLAYOUTCLAW_ROOT / "tests_resources" / "ml08" / "full_stack_raw.jpg",
                "warp_matrix": LOCAL_BASELINE / "warp_sift_bottom.npy",
            },
            0.0204060366,
            False,
        ),
        (
            "top_hbn",
            "top_hbn_mask.png",
            "10/0",
            {
                "image": KLAYOUTCLAW_ROOT / "tests_resources" / "ml08" / "full_stack_raw.jpg",
                "footprint_mask": LOCAL_BASELINE / "footprint_mask.png",
            },
            0.0,
            True,
        ),
    ],
)
def test_klayoutclaw_stage_scripts_match_native_baseline_area(
    tmp_path,
    material,
    baseline_mask,
    layer,
    kwargs,
    expected_rel_err,
    count_sensitive,
):
    import cv2

    from klink.domains.nanodevice.flake import (
        DetectorRunSpec,
        compare_detection_summary,
        normalize_detector_output,
        run_klayoutclaw_stage_script,
    )

    reference_mask = cv2.imread(str(LOCAL_BASELINE / baseline_mask), cv2.IMREAD_GRAYSCALE)
    if reference_mask is None:
        pytest.skip(f"native {material} baseline mask is missing")
    reference = normalize_detector_output(
        {"mask": reference_mask},
        DetectorRunSpec(material=material, pixel_size_um=0.087, layer=layer),
    )

    result = run_klayoutclaw_stage_script(
        KLAYOUTCLAW_ROOT,
        material,
        pixel_size_um=0.087,
        output_dir=tmp_path / material,
        **kwargs,
    )

    comparison = compare_detection_summary(result, reference, area_rel_tol=0.25)
    assert result["report"]["engine"] == f"klayoutclaw:stage:{material}"
    assert comparison["area_ok"] is True
    if count_sensitive:
        assert comparison["count_ok"] is True
    assert comparison["area_rel_error"] == pytest.approx(expected_rel_err)


@pytest.mark.skipif(
    not (KLAYOUTCLAW_ROOT.exists() and LOCAL_BASELINE.exists() and CV2_PRESENT and NUMPY_PRESENT),
    reason="local KlayoutClaw baseline or transform dependencies not present",
)
def test_klayoutclaw_detections_transform_to_traces_and_shape_items(tmp_path):
    import shutil

    from klink.domains.nanodevice.flake import (
        build_klayoutclaw_detections_json,
        run_klayoutclaw_transform,
        trace_material_summary,
        traces_to_polygon_items,
    )

    detect_dir = tmp_path / "detect"
    detect_dir.mkdir()
    for name in [
        "graphite_mask.png",
        "graphite_contour.npy",
        "graphite_result.json",
        "graphene_mask.png",
        "graphene_contour.npy",
        "graphene_result.json",
        "bottom_hbn_mask.png",
        "bottom_hbn_contour.npy",
        "bottom_hbn_result.json",
        "top_hbn_mask.png",
        "top_hbn_contour.npy",
        "top_hbn_result.json",
    ]:
        shutil.copy2(LOCAL_BASELINE / name, detect_dir / name)

    detections = build_klayoutclaw_detections_json(detect_dir, pixel_size_um=0.087)
    info = run_klayoutclaw_transform(
        KLAYOUTCLAW_ROOT,
        detections=detections,
        align_dir=LOCAL_BASELINE,
        image=KLAYOUTCLAW_ROOT / "tests_resources" / "ml08" / "full_stack_raw.jpg",
        pixel_size_um=0.087,
        output_dir=tmp_path / "combine",
    )

    traces = json.loads(Path(info["traces_path"]).read_text(encoding="utf-8"))
    summary = trace_material_summary(traces)
    assert summary["top_hBN"]["count"] == 1
    assert summary["graphene"]["count"] == 1
    assert summary["bottom_hBN"]["count"] == 1
    assert summary["graphite"]["count"] == 1
    assert len(traces_to_polygon_items(traces)) == 4

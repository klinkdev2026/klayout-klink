from __future__ import annotations

import importlib.util

import pytest

CV2_PRESENT = importlib.util.find_spec("cv2") is not None
NUMPY_PRESENT = importlib.util.find_spec("numpy") is not None


def test_flake_region_commit_payloads_are_stdlib_only():
    from klink.domains.nanodevice.flake import regions_to_contact_ports, regions_to_polygon_items

    regions = [{
        "id": "flake_0",
        "material": "graphene",
        "area_um2": 12.0,
        "bbox_um": [1.0, 2.0, 5.0, 8.0],
        "polygon_um": [[1.0, 2.0], [5.0, 2.0], [5.0, 8.0], [1.0, 8.0]],
    }]

    items = regions_to_polygon_items(regions, layer="31/2")
    assert items == [{
        "kind": "polygon",
        "layer": 31,
        "datatype": 2,
        "points_um": regions[0]["polygon_um"],
    }]

    ports = regions_to_contact_ports(regions, prefix="FLK", target_layer="12/0")
    assert len(ports) == 2
    assert ports[0]["center_um"] == [1.0, 5.0]
    assert ports[1]["center_um"] == [5.0, 5.0]
    assert {p["net"] for p in ports} == {"flk_0"}


def test_physical_kernel_diameter_is_odd_and_scaled():
    from klink.domains.nanodevice.flake import physical_kernel_diameter

    assert physical_kernel_diameter(0.0, 0.1) == 1
    assert physical_kernel_diameter(0.5, 0.1) == 11
    assert physical_kernel_diameter(0.5, 0.2) == 7

    with pytest.raises(ValueError):
        physical_kernel_diameter(1.0, 0.0)


@pytest.mark.skipif(not (CV2_PRESENT and NUMPY_PRESENT), reason="nanodevice image deps not installed")
def test_detector_callable_normalizes_mask_regions_ports_and_report():
    import numpy as np

    from klink.domains.nanodevice.flake import DetectorRunSpec, run_detector_callable

    def fake_detector(*, image, pixel_size_um, host_mask=None):
        mask = np.zeros(image.shape[:2], dtype=np.uint8)
        mask[10:30, 20:50] = 255
        return {
            "mask": mask,
            "best_score": 0.75,
            "low_confidence": False,
            "diagnostics": {"host_pixels": int((host_mask > 0).sum()) if host_mask is not None else 0},
        }

    image = np.zeros((60, 80, 3), dtype=np.uint8)
    host = np.ones((60, 80), dtype=np.uint8) * 255
    result = run_detector_callable(
        fake_detector,
        image,
        DetectorRunSpec(
            material="graphene",
            pixel_size_um=0.2,
            layer="11/0",
            target_layer="20/0",
            engine="fake",
            min_area_um2=1.0,
        ),
        host_mask=host,
    )

    assert result["report"]["engine"] == "fake"
    assert result["report"]["best_score"] == pytest.approx(0.75)
    assert result["report"]["diagnostics"]["host_pixels"] == 4800
    assert len(result["regions"]) == 1
    assert result["shape_items"][0]["layer"] == 11
    assert [p["target_layer"] for p in result["ports"]] == ["20/0", "20/0"]


@pytest.mark.skipif(not (CV2_PRESENT and NUMPY_PRESENT), reason="nanodevice image deps not installed")
def test_compare_detection_summary_flags_area_drift():
    import numpy as np

    from klink.domains.nanodevice.flake import (
        DetectorRunSpec,
        compare_detection_summary,
        normalize_detector_output,
    )

    spec = DetectorRunSpec(material="flake", pixel_size_um=1.0)
    ref_mask = np.zeros((40, 40), dtype=np.uint8)
    ref_mask[10:30, 10:30] = 255
    cand_mask = np.zeros((40, 40), dtype=np.uint8)
    cand_mask[10:29, 10:30] = 255

    ref = normalize_detector_output({"mask": ref_mask}, spec)
    cand = normalize_detector_output({"mask": cand_mask}, spec)

    assert compare_detection_summary(cand, ref, area_rel_tol=0.10)["ok"] is True
    strict = compare_detection_summary(cand, ref, area_rel_tol=0.01)
    assert strict["ok"] is False
    assert strict["count_ok"] is True
    assert strict["area_ok"] is False


@pytest.mark.skipif(not (CV2_PRESENT and NUMPY_PRESENT), reason="nanodevice image deps not installed")
def test_detect_bright_flakes_returns_regions_shape_items_and_ports():
    import numpy as np

    from klink.domains.nanodevice.flake import (
        FlakeDetectionSpec,
        detect_bright_flakes,
        regions_to_contact_ports,
    )

    image = np.zeros((80, 100), dtype=np.uint8)
    image[20:50, 30:70] = 220

    result = detect_bright_flakes(
        image,
        FlakeDetectionSpec(
            pixel_size_um=0.5,
            threshold=100,
            min_area_um2=50.0,
            close_kernel_px=3,
            open_kernel_px=3,
            material="graphene",
            layer="31/0",
        ),
    )

    assert result["report"]["region_count"] == 1
    region = result["regions"][0]
    assert region["material"] == "graphene"
    assert region["area_um2"] > 250.0
    assert region["bbox_um"] == pytest.approx([15.0, 10.0, 34.5, 24.5])
    assert result["shape_items"] == [
        {
            "kind": "polygon",
            "layer": 31,
            "datatype": 0,
            "points_um": region["polygon_um"],
        }
    ]

    ports = regions_to_contact_ports(result["regions"], prefix="G", target_layer="12/0")
    assert [p["name"] for p in ports] == ["G_0_L", "G_0_R"]
    assert {p["net"] for p in ports} == {"g_0"}
    assert all(p["target_layer"] == "12/0" for p in ports)


@pytest.mark.skipif(not (CV2_PRESENT and NUMPY_PRESENT), reason="nanodevice image deps not installed")
def test_detect_bright_flakes_filters_small_regions():
    import numpy as np

    from klink.domains.nanodevice.flake import FlakeDetectionSpec, detect_bright_flakes

    image = np.zeros((40, 40), dtype=np.uint8)
    image[5:8, 5:8] = 255
    image[10:30, 10:30] = 255

    result = detect_bright_flakes(
        image,
        FlakeDetectionSpec(pixel_size_um=1.0, threshold=100, min_area_um2=100.0),
    )

    assert result["report"]["region_count"] == 1
    assert result["regions"][0]["area_um2"] > 300.0

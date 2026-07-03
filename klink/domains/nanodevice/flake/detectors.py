"""Generic detector contracts for nanodevice flake workflows.

This module does not implement a heavy detector engine.  It normalizes detector
outputs into the existing nanodevice contract so lightweight and upstream-style
engines can be compared stage by stage.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .detect import regions_from_mask, regions_to_contact_ports, regions_to_polygon_items


@dataclass(frozen=True)
class DetectorRunSpec:
    """Material detector run configuration shared by all detector engines."""

    material: str
    pixel_size_um: float
    layer: str = "30/0"
    target_layer: str = "12/0"
    port_prefix: str | None = None
    min_area_um2: float = 0.0
    engine: str = "external"
    metadata: dict[str, Any] = field(default_factory=dict)


def physical_kernel_diameter(radius_um: float, pixel_size_um: float, *, minimum: int = 1) -> int:
    """Return an odd pixel diameter for a physical morphology radius."""

    if pixel_size_um <= 0:
        raise ValueError("pixel_size_um must be positive")
    if radius_um < 0:
        raise ValueError("radius_um must be non-negative")
    diameter = max(int(minimum), int(round((2.0 * float(radius_um)) / float(pixel_size_um))) + 1)
    if diameter % 2 == 0:
        diameter += 1
    return diameter


def normalize_detector_output(output: dict, spec: DetectorRunSpec) -> dict:
    """Normalize a detector engine output into regions/items/ports/report."""

    if spec.pixel_size_um <= 0:
        raise ValueError("pixel_size_um must be positive")
    if "mask" not in output:
        raise ValueError("detector output must include a 'mask'")

    regions = regions_from_mask(
        output["mask"],
        pixel_size_um=spec.pixel_size_um,
        min_area_um2=spec.min_area_um2,
        material=spec.material,
    )
    shape_items = regions_to_polygon_items(regions, layer=spec.layer)
    prefix = spec.port_prefix if spec.port_prefix is not None else spec.material.upper()
    ports = regions_to_contact_ports(regions, prefix=prefix, target_layer=spec.target_layer)

    report = {
        "engine": spec.engine,
        "material": spec.material,
        "region_count": len(regions),
        "pixel_size_um": spec.pixel_size_um,
        "min_area_um2": spec.min_area_um2,
        "low_confidence": bool(output.get("low_confidence", False)),
    }
    if "best_score" in output:
        report["best_score"] = float(output["best_score"])
    if "score" in output:
        report["score"] = float(output["score"])
    if spec.metadata:
        report["metadata"] = dict(spec.metadata)
    for key in ("diagnostics", "stage_counts", "selected", "candidates"):
        if key in output:
            report[key] = output[key]

    return {
        "regions": regions,
        "shape_items": shape_items,
        "ports": ports,
        "report": report,
        "raw": {k: v for k, v in output.items() if k not in {"mask", "prob"}},
    }


def run_detector_callable(
    detector: Callable[..., dict],
    image,
    spec: DetectorRunSpec,
    **kwargs,
) -> dict:
    """Run an importable detector callable and normalize its mask output."""

    output = detector(image=image, pixel_size_um=spec.pixel_size_um, **kwargs)
    if not isinstance(output, dict):
        raise TypeError("detector callable must return a dict")
    return normalize_detector_output(output, spec)


def load_detector_callable(module_path: str | Path, function_name: str) -> Callable[..., dict]:
    """Load a detector function from a Python file without adding dependencies.

    Heavy modules still import their own dependencies at load/call time.  This
    helper only provides a stdlib adapter for reference implementations.
    """

    path = Path(module_path)
    if not path.exists():
        raise FileNotFoundError(path)
    module_name = f"_nanodevice_detector_{path.stem}_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load detector module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn = getattr(module, function_name)
    if not callable(fn):
        raise TypeError(f"{function_name!r} is not callable in {path}")
    return fn


def compare_detection_summary(candidate: dict, reference: dict, *, area_rel_tol: float = 0.05) -> dict:
    """Compare normalized detector summaries against a reference summary."""

    cand_regions = candidate.get("regions", [])
    ref_regions = reference.get("regions", [])
    cand_area = sum(float(r.get("area_um2", 0.0)) for r in cand_regions)
    ref_area = sum(float(r.get("area_um2", 0.0)) for r in ref_regions)
    if ref_area == 0:
        area_ok = cand_area == 0
        rel_err = 0.0 if cand_area == 0 else float("inf")
    else:
        rel_err = abs(cand_area - ref_area) / ref_area
        area_ok = rel_err <= area_rel_tol
    count_ok = len(cand_regions) == len(ref_regions)
    return {
        "ok": bool(area_ok and count_ok),
        "count_ok": count_ok,
        "area_ok": bool(area_ok),
        "candidate_count": len(cand_regions),
        "reference_count": len(ref_regions),
        "candidate_area_um2": round(cand_area, 6),
        "reference_area_um2": round(ref_area, 6),
        "area_rel_error": rel_err,
        "area_rel_tol": area_rel_tol,
    }

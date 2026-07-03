"""Lightweight flake utilities and priors loader."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .._deps import load_cv, load_np


@dataclass(frozen=True)
class FlakeDetectionSpec:
    """Simple cv2-based detector configuration.

    This is the low-dependency baseline for Phase 3.  More specialized
    KlayoutClaw detector variants can layer on top of this contract later.
    """

    pixel_size_um: float
    threshold: int = 128
    min_area_um2: float = 1.0
    close_kernel_px: int = 5
    open_kernel_px: int = 3
    material: str = "flake"
    layer: str = "30/0"


def load_priors(name: str) -> dict:
    """Load vendored KlayoutClaw prior JSON by basename."""

    if not name.endswith(".json"):
        name = f"{name}.json"
    with (Path(__file__).with_name("priors") / name).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def morph_clean(mask, close_k: int = 5, open_k: int = 3):
    """Morphological close then open, ported from KlayoutClaw core.py."""

    cv2 = load_cv()
    if close_k > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(close_k), int(close_k)))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    if open_k > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(open_k), int(open_k)))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    return mask


def flood_fill_holes(mask):
    """Fill interior holes in a binary mask, ported from KlayoutClaw core.py."""

    cv2 = load_cv()
    np = load_np()
    h, w = mask.shape[:2]
    flood = mask.copy()
    fill_mask = np.zeros((h + 2, w + 2), dtype=np.uint8)
    cv2.floodFill(flood, fill_mask, (0, 0), 255)
    holes = cv2.bitwise_not(flood)
    return cv2.bitwise_or(mask, holes)


def detect_bright_flakes(image, spec: FlakeDetectionSpec | dict) -> dict:
    """Detect bright flake-like regions and return klink-ready geometry.

    Output regions are polygons in microns plus a structured report.  No
    routing or KLayout writeback happens here.
    """

    cv2 = load_cv()
    np = load_np()
    if isinstance(spec, dict):
        spec = FlakeDetectionSpec(**spec)
    if spec.pixel_size_um <= 0:
        raise ValueError("pixel_size_um must be positive")
    gray = image
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = (gray >= int(spec.threshold)).astype(np.uint8) * 255
    mask = morph_clean(mask, close_k=spec.close_kernel_px, open_k=spec.open_kernel_px)
    mask = flood_fill_holes(mask)
    regions = regions_from_mask(
        mask,
        pixel_size_um=spec.pixel_size_um,
        min_area_um2=spec.min_area_um2,
        material=spec.material,
    )
    shape_items = regions_to_polygon_items(regions, layer=spec.layer)
    return {
        "regions": regions,
        "shape_items": shape_items,
        "report": {
            "material": spec.material,
            "region_count": len(regions),
            "pixel_size_um": spec.pixel_size_um,
            "threshold": spec.threshold,
            "min_area_um2": spec.min_area_um2,
        },
    }


def regions_from_mask(mask, *, pixel_size_um: float, min_area_um2: float = 0.0, material: str = "flake") -> list[dict]:
    """Convert a binary mask into polygon regions in microns."""

    cv2 = load_cv()
    contours, _hier = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    regions = []
    for idx, contour in enumerate(contours):
        area_px = float(cv2.contourArea(contour))
        area_um2 = area_px * float(pixel_size_um) * float(pixel_size_um)
        if area_um2 < float(min_area_um2):
            continue
        polygon_um = _contour_to_polygon_um(contour, pixel_size_um)
        if len(polygon_um) < 3:
            continue
        bbox = _polygon_bbox(polygon_um)
        regions.append({
            "id": f"{material}_{idx}",
            "material": material,
            "area_px": area_px,
            "area_um2": area_um2,
            "polygon_um": polygon_um,
            "bbox_um": bbox,
            "confidence": 1.0,
        })
    regions.sort(key=lambda r: float(r["area_um2"]), reverse=True)
    return regions


def regions_to_polygon_items(regions: list[dict], *, layer: str = "30/0") -> list[dict]:
    """Convert detected regions to ``shape.insert_many`` polygon items."""

    layer_num, datatype = _parse_layer(layer)
    return [
        {
            "kind": "polygon",
            "layer": layer_num,
            "datatype": datatype,
            "points_um": region["polygon_um"],
        }
        for region in regions
    ]


def regions_to_contact_ports(
    regions: list[dict],
    *,
    target_layer: str = "12/0",
    port_layer: str = "999/99",
    prefix: str = "FLAKE",
) -> list[dict]:
    """Emit candidate contact Ports at each region bbox midpoint."""

    ports = []
    for idx, region in enumerate(regions):
        x0, y0, x1, y1 = region["bbox_um"]
        width = max(0.2, min(x1 - x0, y1 - y0) / 4.0)
        for suffix, center, orientation in (
            ("L", [x0, (y0 + y1) / 2.0], 180.0),
            ("R", [x1, (y0 + y1) / 2.0], 0.0),
        ):
            ports.append({
                "layer": port_layer,
                "name": f"{prefix}_{idx}_{suffix}",
                "center_um": center,
                "orientation": orientation,
                "width_um": width,
                "port_type": "electrical",
                "net": f"{prefix.lower()}_{idx}",
                "target_layer": target_layer,
                "access_mode": "point",
                "show_label": True,
            })
    return ports


def threshold_regions(image, *, threshold: int = 128) -> dict:
    """Minimal cv2-backed detector used as a future porting anchor."""

    np = load_np()
    gray = image
    if len(image.shape) == 3:
        cv2 = load_cv()
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = (gray >= int(threshold)).astype(np.uint8) * 255
    polygons = [region["polygon_um"] for region in regions_from_mask(mask, pixel_size_um=1.0)]
    return {"polygons_px": polygons, "count": len(polygons)}


def _contour_to_polygon_um(contour, pixel_size_um: float) -> list[list[float]]:
    pts = contour.reshape(-1, 2)
    return [[float(x) * float(pixel_size_um), float(y) * float(pixel_size_um)] for x, y in pts]


def _polygon_bbox(polygon: list[list[float]]) -> list[float]:
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return [min(xs), min(ys), max(xs), max(ys)]


def _parse_layer(layer: str) -> tuple[int, int]:
    parts = str(layer).split("/")
    if len(parts) == 1:
        return int(parts[0]), 0
    if len(parts) == 2:
        return int(parts[0]), int(parts[1])
    raise ValueError(f"invalid layer string: {layer!r}")

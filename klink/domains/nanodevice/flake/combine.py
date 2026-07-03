"""Flake candidate combination namespace.

The helpers here intentionally stay stdlib-only.  They provide a stable
adapter for KlayoutClaw-style ``traces.json`` output without pulling the full
image alignment stack into the default nanodevice dependency set.
"""

from __future__ import annotations

import json
from pathlib import Path

from .artifacts import LayoutPayload, TraceBundle


def rank_regions_by_area(regions: list[dict]) -> list[dict]:
    return sorted(regions, key=lambda r: float(r.get("area_um2", 0.0)), reverse=True)


def trace_material_summary(traces: dict) -> dict:
    """Return counts, total areas, and contour point counts by material."""

    summary = {}
    for material, entries in traces.get("materials", {}).items():
        summary[material] = {
            "count": len(entries),
            "area_um2": round(sum(float(entry.get("area_um2", 0.0)) for entry in entries), 3),
            "point_counts": [int(entry.get("num_points", len(entry.get("contour_px", [])))) for entry in entries],
        }
    return summary


def traces_to_polygon_items(
    traces: dict,
    *,
    coordinate: str = "um",
    layer_map: dict[str, str] | None = None,
) -> list[dict]:
    """Convert KlayoutClaw trace contours into klink polygon shape items.

    Args:
        traces: Parsed KlayoutClaw ``traces.json``.
        coordinate: ``"um"`` for microscope-space microns or ``"gds"`` for
            pre-placed GDS coordinates when ``contour_gds`` is present.
        layer_map: Optional material-to-layer override.  Defaults to the
            trace file's ``layer_map``.
    """

    if coordinate not in {"um", "gds"}:
        raise ValueError("coordinate must be 'um' or 'gds'")
    layers = layer_map if layer_map is not None else traces.get("layer_map", {})
    contour_key = "contour_gds" if coordinate == "gds" else "contour_um"
    items = []
    for material in traces.get("stack", traces.get("materials", {}).keys()):
        layer = layers.get(material)
        if layer is None:
            continue
        layer_num, datatype = _parse_layer(layer)
        for entry in traces.get("materials", {}).get(material, []):
            points = entry.get(contour_key)
            if not points and coordinate == "um":
                pixel_size = float(traces["pixel_size_um"])
                points = [[float(x) * pixel_size, float(y) * pixel_size] for x, y in entry.get("contour_px", [])]
            if not points:
                continue
            items.append({
                "kind": "polygon",
                "layer": layer_num,
                "datatype": datatype,
                "points_um": [[float(x), float(y)] for x, y in points],
                "material": material,
                "source_id": entry.get("id"),
            })
    return items


def trace_bundle_from_path(path: str | Path) -> TraceBundle:
    """Load ``traces.json`` and return a typed summary artifact."""

    p = Path(path)
    traces = json.loads(p.read_text(encoding="utf-8"))
    return TraceBundle(
        traces_path=str(p),
        summary=trace_material_summary(traces),
        shape_item_count=len(traces_to_polygon_items(traces)),
    )


def layout_payload_from_traces(
    traces: dict,
    *,
    source_path: str | Path,
    coordinate: str = "um",
    layer_map: dict[str, str] | None = None,
) -> LayoutPayload:
    """Convert traces into a typed layout insertion payload."""

    return LayoutPayload(
        source_path=str(Path(source_path)),
        coordinate=coordinate,
        shape_items=traces_to_polygon_items(traces, coordinate=coordinate, layer_map=layer_map),
    )


def _parse_layer(layer: str) -> tuple[int, int]:
    parts = str(layer).split("/")
    if len(parts) == 1:
        return int(parts[0]), 0
    if len(parts) == 2:
        return int(parts[0]), int(parts[1])
    raise ValueError(f"invalid layer string: {layer!r}")

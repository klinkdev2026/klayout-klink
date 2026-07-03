"""Review helpers for detected flake regions."""

from __future__ import annotations


def summarize_regions(regions: list[dict]) -> dict:
    return {"count": len(regions), "total_area_um2": sum(float(r.get("area_um2", 0.0)) for r in regions)}

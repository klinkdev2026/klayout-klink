"""Fabrication site-map facts emission."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .sites import Site


@dataclass
class SiteMap:
    cell: str
    sites: list[Site]
    marks: list[dict]
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "cell": self.cell,
            "sites": [site.to_dict() for site in self.sites],
            "marks": [dict(mark) for mark in self.marks],
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SiteMap":
        return cls(
            cell=str(data["cell"]),
            sites=[Site.from_dict(item) for item in data.get("sites", [])],
            marks=[dict(item) for item in data.get("marks", [])],
            meta=dict(data.get("meta", {})),
        )


def write_sites_csv(site_map: SiteMap, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["site_id", "row", "col", "x_um", "y_um", "rotation_deg"])
        writer.writeheader()
        for site in site_map.sites:
            writer.writerow({
                "site_id": site.site_id,
                "row": site.row,
                "col": site.col,
                "x_um": site.center_um[0],
                "y_um": site.center_um[1],
                "rotation_deg": site.rotation_deg,
            })
    return p


def write_sites_json(site_map: SiteMap, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(site_map.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return p


def default_site_paths(state_dir: str | Path, cell: str) -> tuple[Path, Path]:
    root = Path(state_dir)
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in cell)
    return root / f"{safe}.sites.json", root / f"{safe}.sites.csv"

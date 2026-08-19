from __future__ import annotations

import csv
import json

from klink.domains.fabrication.mapping import SiteMap, write_sites_csv, write_sites_json
from klink.domains.fabrication.sites import Site


def test_site_map_csv_json_round_trip(tmp_path):
    site_map = SiteMap(
        cell="TOP",
        sites=[Site("R0C0", 0, 0, (1.0, 2.0), 90.0)],
        marks=[{"kind": "alignment_mark", "preset": "chip", "center_um": [0, 0], "layer": "6/0"}],
        meta={"pitch_x_um": 10},
    )

    json_path = write_sites_json(site_map, tmp_path / "sites.json")
    csv_path = write_sites_csv(site_map, tmp_path / "sites.csv")

    loaded = SiteMap.from_dict(json.loads(json_path.read_text(encoding="utf-8")))
    assert loaded == site_map
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert rows == [{"site_id": "R0C0", "row": "0", "col": "0", "x_um": "1.0", "y_um": "2.0", "rotation_deg": "90.0"}]

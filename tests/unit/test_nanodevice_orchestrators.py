from __future__ import annotations

import json
from pathlib import Path


class FakeClient:
    def __init__(self):
        self.calls = []

    def cell_delete(self, cell, recursive=True):
        self.calls.append(("cell_delete", cell, recursive))

    def cell_create(self, cell):
        self.calls.append(("cell_create", cell))

    def layer_ensure(self, layer, datatype, name=None):
        self.calls.append(("layer_ensure", layer, datatype, name))

    def shape_insert_many(self, cell, items):
        self.calls.append(("shape_insert_many", cell, len(items)))
        return {"cell": cell, "inserted": len(items), "requested": len(items)}

    def shape_insert_boxes(self, cell, *, layer, datatype, boxes_um):
        self.calls.append(("shape_insert_boxes", cell, layer, datatype, len(boxes_um)))
        return {"cell": cell, "inserted": len(boxes_um)}

    def shape_delete(self, cell, **kwargs):
        self.calls.append(("shape_delete", cell, kwargs))
        return {"cell": cell, "deleted": 0}

    def call(self, method, payload):
        self.calls.append(("call", method, payload.get("cell"), payload.get("name") or payload.get("id")))
        return {"ok": True}

    def show_cell(self, cell, zoom_fit=True):
        self.calls.append(("show_cell", cell, zoom_fit))


def _write_traces(path: Path) -> None:
    traces = {
        "pixel_size_um": 1.0,
        "stack": ["graphene"],
        "layer_map": {"graphene": "11/0"},
        "materials": {
            "graphene": [
                {
                    "id": 1,
                    "area_um2": 1.0,
                    "num_points": 4,
                    "contour_um": [[0, 0], [1, 0], [1, 1], [0, 1]],
                }
            ]
        },
    }
    path.write_text(json.dumps(traces), encoding="utf-8")


def test_build_and_route_hallbar_validates_before_mutating_on_bad_spec(tmp_path):
    from klink.domains.nanodevice import build_and_route_hallbar

    client = FakeClient()
    result = build_and_route_hallbar(
        client,
        spec={"contact_count": 3, "device_layer": "1/0", "metal_layer": "10/0",
              "label_layer": "6/0", "route_layer": "12/0"},
        state_dir=tmp_path,
    )

    assert result["ok"] is False
    assert result["committed"] is False
    assert "contact_count" in result["problems"][0]
    assert client.calls == []
    assert list(tmp_path.iterdir()) == []


def test_build_and_route_hallbar_commits_and_persists_twice(tmp_path):
    from klink.domains.nanodevice import build_and_route_hallbar

    client = FakeClient()
    kwargs = {
        "client": client,
        "cell": "ND_HB",
        "state_dir": tmp_path,
        "show": False,
        "spec": {"device_layer": "1/0", "metal_layer": "10/0",
                 "label_layer": "6/0", "route_layer": "12/0"},
        "writefield": {
            "chip_bbox_um": [-95.0, -45.0, 95.0, 45.0],
            "writefield_size_um": [70.0, 120.0],
            "origin_um": [10.0, 0.0],
            "stitch_margin_um": 1.2,
        },
    }

    first = build_and_route_hallbar(**kwargs)
    second = build_and_route_hallbar(**kwargs)

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["routing"]["route_count"] == 6
    assert second["routing"]["writefield_wall_crossings"] == 0
    assert client.calls.count(("cell_create", "ND_HB")) == 2
    state = json.loads((tmp_path / "ND_HB.hallbar.json").read_text(encoding="utf-8"))
    assert state["kind"] == "nanodevice.hallbar"
    assert state["routing"]["route_count"] == 6


def test_detect_and_commit_requires_trace_or_pipeline_before_mutating(tmp_path):
    from klink.domains.nanodevice import detect_and_commit

    client = FakeClient()
    result = detect_and_commit(client, state_dir=tmp_path)

    assert result["ok"] is False
    assert result["committed"] is False
    assert "traces_path" in result["next_action"]
    assert client.calls == []
    assert list(tmp_path.iterdir()) == []


def test_detect_and_commit_traces_commits_and_persists_twice(tmp_path):
    from klink.domains.nanodevice import detect_and_commit

    traces_path = tmp_path / "traces.json"
    _write_traces(traces_path)
    client = FakeClient()
    kwargs = {
        "client": client,
        "cell": "ND_FLK",
        "traces_path": traces_path,
        "state_dir": tmp_path / "state",
        "show": False,
    }

    first = detect_and_commit(**kwargs)
    second = detect_and_commit(**kwargs)

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["layout_payload"]["shape_item_count"] == 1
    assert client.calls.count(("cell_create", "ND_FLK")) == 2
    state = json.loads((tmp_path / "state" / "ND_FLK.flake.json").read_text(encoding="utf-8"))
    assert state["kind"] == "nanodevice.flake"
    assert state["trace_bundle"]["shape_item_count"] == 1

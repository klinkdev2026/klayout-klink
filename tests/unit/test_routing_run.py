from __future__ import annotations

from klink.routing.geom.run import route_cell


class FakeClient:
    def layout_info(self):
        return {"dbu": 0.001}

    def call(self, method, params=None):
        params = params or {}
        if method == "port.list":
            return {
                "ports": [
                    {
                        "name": "A",
                        "net": "sig",
                        "port_type": "electrical",
                        "center_um": [0, 0],
                        "orientation": 0,
                        "width_um": 4.0,
                        "target_layer": "10/0",
                    },
                    {
                        "name": "B",
                        "net": "sig",
                        "port_type": "electrical",
                        "center_um": [20, 0],
                        "orientation": 180,
                        "width_um": 4.0,
                        "target_layer": "10/0",
                    },
                ]
            }
        if method == "anchor.list":
            return {"anchors": []}
        if method == "shape.query":
            return {"shapes": []}
        raise AssertionError(method)

    def shape_query(self, cell, **kwargs):
        return self.call("shape.query", {"cell": cell, **kwargs})


def test_route_cell_dry_run_report_shape():
    report = route_cell(FakeClient(), "C", dry_run=True)

    assert report["cell"] == "C"
    assert report["dry_run"] is True
    assert report["committed"] is False
    assert report["algorithm"] == "deterministic_semantic_skeleton"
    assert report["backend"] == "simple_route_router"
    assert report["routable"] is True
    assert report["route_count"] == 1
    assert report["routes"][0]["source"] == "A"
    assert report["routes"][0]["target"] == "B"
    assert report["crossings"] == []
    assert report["obstacle_hits"] == []
    assert report["writeback"] is None

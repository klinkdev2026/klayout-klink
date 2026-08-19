from __future__ import annotations

import json


class FakeClient:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on
        self.layers = set()

    def cell_delete(self, cell, recursive=True):
        self.calls.append(("cell_delete", cell, recursive))

    def cell_create(self, cell):
        self.calls.append(("cell_create", cell))

    def layer_ensure(self, layer, datatype, name=None):
        self.calls.append(("layer_ensure", layer, datatype, name))
        self.layers.add((int(layer), int(datatype)))

    def shape_insert_many(self, cell, items, *, dry_run=False):
        self.calls.append(("shape_insert_many", cell, len(items), dry_run))
        if self.fail_on == "shape_insert_many":
            raise RuntimeError("scripted shape failure")
        missing = sorted({(int(item["layer"]), int(item.get("datatype", 0))) for item in items} - self.layers)
        if missing:
            raise RuntimeError(f"layer(s) not ensured before shape_insert_many: {missing}")
        return {"cell": cell, "inserted": len(items), "requested": len(items), "dry_run": dry_run}

    def instance_insert_many(self, parent, items, *, dry_run=False):
        self.calls.append(("instance_insert_many", parent, len(items), dry_run))
        return {"parent": parent, "inserted": len(items), "requested": len(items), "dry_run": dry_run}

    def show_cell(self, cell, zoom_fit=True):
        self.calls.append(("show_cell", cell, zoom_fit))


def _grid_layout():
    return {"kind": "grid", "region_bbox_um": [0, 0, 100, 0], "pitch_x_um": 50, "pitch_y_um": 50}


def test_place_site_array_dry_run_returns_full_table_without_mutation(tmp_path):
    from klink.domains.fabrication import place_site_array

    client = FakeClient()
    result = place_site_array(client, cell="FAB", site_layout=_grid_layout(), state_dir=tmp_path, dry_run=True)

    assert result["ok"] is True
    assert result["committed"] is False
    assert result["site_count"] == 3
    assert client.calls == []
    assert list(tmp_path.iterdir()) == []


def test_place_site_array_validation_failure_leaves_no_scars(tmp_path):
    from klink.domains.fabrication import place_site_array

    client = FakeClient()
    result = place_site_array(client, cell="", site_layout={"kind": "bad"}, state_dir=tmp_path)

    assert result["ok"] is False
    assert result["committed"] is False
    assert "cell must be" in result["problems"][0]
    assert "site_layout.kind" in result["problems"][1]
    assert client.calls == []
    assert list(tmp_path.iterdir()) == []


def test_place_site_array_live_failure_deletes_cell_and_writes_no_state(tmp_path):
    from klink.domains.fabrication import place_site_array

    client = FakeClient(fail_on="shape_insert_many")
    result = place_site_array(client, cell="FAB", site_layout=_grid_layout(), state_dir=tmp_path)

    assert result["ok"] is False
    assert result["reason"] == "live_commit_failed"
    assert ("cell_delete", "FAB", True) in client.calls
    assert client.calls.count(("cell_delete", "FAB", True)) == 2
    assert list(tmp_path.iterdir()) == []


def test_place_site_array_state_write_failure_removes_partial_state(monkeypatch, tmp_path):
    import klink.domains.fabrication.orchestrators as orch
    from klink.domains.fabrication import place_site_array

    def fail_csv(*_args, **_kwargs):
        raise RuntimeError("scripted csv failure")

    monkeypatch.setattr(orch, "write_sites_csv", fail_csv)
    client = FakeClient()
    result = place_site_array(client, cell="FAB", site_layout=_grid_layout(), state_dir=tmp_path)

    assert result["ok"] is False
    assert result["reason"] == "live_commit_failed"
    assert ("cell_delete", "FAB", True) in client.calls
    assert not (tmp_path / "FAB.sites.json").exists()
    assert not (tmp_path / "FAB.sites.csv").exists()


def test_place_site_array_commits_and_persists_twice_in_one_process(tmp_path):
    from klink.domains.fabrication import place_site_array

    client = FakeClient()
    kwargs = {
        "client": client,
        "cell": "FAB",
        "site_layout": _grid_layout(),
        "marks": {"placement": "corners", "preset": "chip"},
        "instance": {"child": "DUT"},
        "numbering": {"scheme": "prefix", "prefix": "D", "width": 2},
        "state_dir": tmp_path,
    }

    first = place_site_array(**kwargs)
    second = place_site_array(**kwargs)

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["site_count"] == 3
    assert client.calls.count(("cell_create", "FAB")) == 2
    assert any(call[0] == "layer_ensure" and call[1:3] == (6, 0) for call in client.calls)
    assert any(call[0] == "instance_insert_many" and call[2] == 3 for call in client.calls)
    data = json.loads((tmp_path / "FAB.sites.json").read_text(encoding="utf-8"))
    assert [site["site_id"] for site in data["sites"]] == ["D00", "D01", "D02"]
    assert (tmp_path / "FAB.sites.csv").exists()


def test_place_site_array_cell_marks_instance_four_distinct_corner_cells(tmp_path):
    from klink.domains.fabrication import place_site_array

    client = FakeClient()
    result = place_site_array(
        client,
        cell="FAB",
        site_layout=_grid_layout(),
        marks={
            "mode": "cells",
            "corners": {
                "leftdown": "align_leftdown",
                "leftup": "align_leftup",
                "rightdown": "align_rightdown",
                "rightup": "align_rightup",
            },
        },
        instance={"child": "DUT"},
        state_dir=tmp_path,
    )

    assert result["ok"] is True
    inst_calls = [call for call in client.calls if call[0] == "instance_insert_many"]
    assert inst_calls[0][2] == 7
    mark_records = [mark for mark in result["site_map"]["marks"] if mark["kind"] == "cell_alignment_mark"]
    assert [mark["corner"] for mark in mark_records] == ["leftdown", "leftup", "rightdown", "rightup"]
    assert len({mark["cell"] for mark in mark_records}) == 4


def test_place_site_array_rejects_reusing_one_cell_for_all_corners(tmp_path):
    from klink.domains.fabrication import place_site_array

    client = FakeClient()
    result = place_site_array(
        client,
        cell="FAB",
        site_layout=_grid_layout(),
        marks={
            "mode": "cells",
            "corners": {
                "leftdown": "align_mark",
                "leftup": "align_mark",
                "rightdown": "align_mark",
                "rightup": "align_mark",
            },
        },
        state_dir=tmp_path,
    )

    assert result["ok"] is False
    assert "four distinct" in result["problems"][0]
    assert client.calls == []

from __future__ import annotations

import pytest

from klink.transfer import (
    TransferError,
    build_flat_selection_package,
    build_shallow_instance_package,
    commit_flat_selection_package,
    commit_shallow_instance_package,
    review_deep_cell_tree,
    review_shallow_instance,
    review_flat_selection,
)


def _source_layers():
    return {
        "layers": [
            {"layer_index": 3, "layer": 1, "datatype": 0},
            {"layer_index": 4, "layer": 2, "datatype": 0},
        ]
    }


def _selection():
    return {
        "count": 2,
        "truncated": False,
        "objects": [
            {
                "kind": "shape",
                "shape": {
                    "type": "box",
                    "layer_index": 3,
                    "bbox_dbu": [0, 0, 1000, 500],
                },
            },
            {
                "kind": "shape",
                "shape": {
                    "type": "path",
                    "layer_index": 4,
                    "points_dbu": [[0, 0], [1000, 0]],
                    "width_dbu": 200,
                    "bbox_dbu": [0, -100, 1000, 100],
                },
            },
        ],
    }


def test_review_rejects_empty_selection():
    with pytest.raises(TransferError, match="empty selection"):
        review_flat_selection({"objects": []}, source_layers=_source_layers(), source_dbu_um=0.001)


def test_build_flat_selection_package_maps_layers_and_uses_um_coordinates():
    package = build_flat_selection_package(
        _selection(),
        source_layers=_source_layers(),
        source_dbu_um=0.001,
        source_session="klayout-8765",
        target_session="klayout-8767",
        target_cell="TOP",
        layer_map={"1/0": "10/0"},
        translate_um=[5.0, -2.0],
    )

    assert package["copy_mode"] == "flat_selection"
    assert package["source_session"] == "klayout-8765"
    assert package["target_session"] == "klayout-8767"
    assert package["target_cell"] == "TOP"
    assert package["review"]["shape_count"] == 2
    assert package["review"]["layers"] == ["1/0", "2/0"]
    assert package["review"]["target_layers"] == ["10/0", "2/0"]
    assert package["review"]["bbox_um"] == [0.0, -0.1, 1.0, 0.5]

    box, path = package["items"]
    assert box["kind"] == "box"
    assert box["layer"] == 10
    assert box["datatype"] == 0
    assert box["bbox_um"] == [5.0, -2.0, 6.0, -1.5]
    assert path["kind"] == "path"
    assert path["layer"] == 2
    assert path["datatype"] == 0
    assert path["points_um"] == [[5.0, -2.0], [6.0, -2.0]]
    assert path["width_um"] == 0.2


def test_build_flat_selection_package_rejects_instances_for_mvp():
    selection = {"objects": [{"kind": "instance", "target_cell": "CHILD"}]}

    with pytest.raises(TransferError, match="instances are not supported"):
        build_flat_selection_package(
            selection,
            source_layers=_source_layers(),
            source_dbu_um=0.001,
            source_session="klayout-8765",
            target_session="klayout-8767",
            target_cell="TOP",
        )


def test_build_flat_selection_package_rejects_same_session_by_default():
    with pytest.raises(TransferError, match="must be different"):
        build_flat_selection_package(
            _selection(),
            source_layers=_source_layers(),
            source_dbu_um=0.001,
            source_session="klayout-8765",
            target_session="klayout-8765",
            target_cell="TOP",
        )


def test_build_flat_selection_package_rejects_bad_layer_map():
    with pytest.raises(TransferError, match="layer key"):
        build_flat_selection_package(
            _selection(),
            source_layers=_source_layers(),
            source_dbu_um=0.001,
            source_session="klayout-8765",
            target_session="klayout-8767",
            target_cell="TOP",
            layer_map={"1/0": "bad"},
        )


def test_build_flat_selection_package_rejects_missing_source_layer_index():
    selection = _selection()
    selection["objects"][0]["shape"]["layer_index"] = 99

    with pytest.raises(TransferError, match="not in layer.list"):
        build_flat_selection_package(
            selection,
            source_layers=_source_layers(),
            source_dbu_um=0.001,
            source_session="klayout-8765",
            target_session="klayout-8767",
            target_cell="TOP",
        )


def test_commit_flat_selection_package_ensures_layers_then_inserts_many():
    calls = []

    class TargetClient:
        def layer_ensure(self, layer, datatype=0):
            calls.append(("layer.ensure", layer, datatype))
            return {"layer_index": 100 + layer}

        def shape_insert_many(self, cell, items, *, dry_run=False):
            calls.append(("shape.insert_many", cell, items, dry_run))
            return {"inserted": 1, "dry_run": dry_run}

    package = build_flat_selection_package(
        _selection(),
        source_layers=_source_layers(),
        source_dbu_um=0.001,
        source_session="klayout-8765",
        target_session="klayout-8767",
        target_cell="TOP",
        layer_map={"1/0": "10/0"},
    )

    result = commit_flat_selection_package(TargetClient(), package)

    assert result["ok"] is True
    assert calls[0] == ("layer.ensure", 10, 0)
    assert calls[1] == ("layer.ensure", 2, 0)
    assert calls[2][0] == "shape.insert_many"
    assert calls[2][1] == "TOP"
    assert calls[2][3] is False
    assert calls[2][2][0]["layer_index"] == 110
    assert calls[2][2][1]["layer_index"] == 102


def test_commit_flat_selection_package_creates_missing_layers_before_write():
    created = []

    class TargetClient:
        def layer_ensure(self, layer, datatype=0):
            created.append((layer, datatype))
            return {"layer_index": len(created)}

        def shape_insert_many(self, cell, items, *, dry_run=False):
            assert [item["layer_index"] for item in items] == [1, 2]
            return {"inserted": len(items)}

    package = build_flat_selection_package(
        _selection(),
        source_layers=_source_layers(),
        source_dbu_um=0.001,
        source_session="klayout-8765",
        target_session="klayout-8767",
        target_cell="TOP",
        layer_map={"1/0": "10/0", "2/0": "11/0"},
    )

    commit_flat_selection_package(TargetClient(), package)

    assert created == [(10, 0), (11, 0)]


def test_commit_flat_selection_package_dry_run_does_not_insert():
    calls = []

    class TargetClient:
        def layer_ensure(self, layer, datatype=0):
            calls.append(("layer.ensure", layer, datatype))
            return {"layer_index": 100 + layer}

        def shape_insert_many(self, cell, items, *, dry_run=False):
            calls.append(("shape.insert_many", cell, items, dry_run))
            return {"inserted": 0, "dry_run": dry_run}

    package = build_flat_selection_package(
        _selection(),
        source_layers=_source_layers(),
        source_dbu_um=0.001,
        source_session="klayout-8765",
        target_session="klayout-8767",
        target_cell="TOP",
    )

    result = commit_flat_selection_package(TargetClient(), package, dry_run=True)

    assert result["ok"] is True
    assert calls[-1][0] == "shape.insert_many"
    assert calls[-1][3] is True


@pytest.mark.parametrize("copy_mode", ["shallow_instance", "deep_cell_tree"])
def test_commit_rejects_deep_and_shallow_copy_modes_until_review_policy_exists(copy_mode):
    package = {
        "version": 1,
        "copy_mode": copy_mode,
        "target_cell": "TOP",
        "items": [{"kind": "box", "layer": 1, "datatype": 0, "bbox_um": [0, 0, 1, 1]}],
    }

    with pytest.raises(TransferError, match="only flat_selection"):
        commit_flat_selection_package(object(), package)


def test_review_shallow_instance_blocks_missing_target_child():
    source_instances = {
        "instances": [
            {
                "child": "MZI",
                "bbox_dbu": [0, 0, 1000, 1000],
                "trans": {"x_dbu": 100, "y_dbu": 200},
            }
        ]
    }
    target_cells = {"cells": [{"name": "TOP"}]}

    review = review_shallow_instance(
        source_instances,
        target_cells=target_cells,
        source_session="klayout-8765",
        target_session="klayout-8767",
    )

    assert review["copy_mode"] == "shallow_instance"
    assert review["ok_to_commit"] is False
    assert review["missing_child_cells"] == ["MZI"]
    assert review["warnings"]


def test_review_shallow_instance_reports_reused_target_cells():
    source_instances = {
        "instances": [
            {
                "child": "MZI",
                "bbox_dbu": [0, 0, 1000, 1000],
                "trans": {"x_dbu": 100, "y_dbu": 200},
            }
        ]
    }
    target_cells = {"cells": [{"name": "TOP"}, {"name": "MZI"}]}

    review = review_shallow_instance(
        source_instances,
        target_cells=target_cells,
        source_session="klayout-8765",
        target_session="klayout-8767",
    )

    assert review["ok_to_commit"] is True
    assert review["reused_target_cells"] == ["MZI"]
    assert review["missing_child_cells"] == []


def test_build_shallow_instance_package_preserves_instance_transform():
    source_instances = {
        "instances": [
            {
                "child": "MZI",
                "bbox_dbu": [100, 200, 300, 400],
                "trans": {
                    "dx_dbu": 1000,
                    "dy_dbu": 2000,
                    "rotation_deg": 90.0,
                    "mirror": True,
                    "magnification": 1.0,
                },
            }
        ]
    }
    target_cells = {"cells": [{"name": "TOP"}, {"name": "MZI"}]}

    package = build_shallow_instance_package(
        source_instances,
        target_cells=target_cells,
        source_dbu_um=0.001,
        source_session="klayout-8765",
        target_session="klayout-8767",
        target_cell="TOP",
        translate_um=[10, 5],
    )

    assert package["copy_mode"] == "shallow_instance"
    assert package["review"]["ok_to_commit"] is True
    assert package["items"] == [
        {
            "child": "MZI",
            "position_um": [11.0, 7.0],
            "rotation": 90.0,
            "mirror": True,
            "magnification": 1.0,
        }
    ]


def test_build_shallow_instance_package_rejects_missing_target_child():
    source_instances = {"instances": [{"child": "MZI", "trans": {"dx_dbu": 0, "dy_dbu": 0}}]}
    target_cells = {"cells": [{"name": "TOP"}]}

    with pytest.raises(TransferError, match="missing target child cells"):
        build_shallow_instance_package(
            source_instances,
            target_cells=target_cells,
            source_dbu_um=0.001,
            source_session="klayout-8765",
            target_session="klayout-8767",
            target_cell="TOP",
        )


def test_commit_shallow_instance_package_uses_instance_insert_many():
    calls = []

    class TargetClient:
        def instance_insert_many(self, parent, items, *, dry_run=False):
            calls.append((parent, items, dry_run))
            return {"inserted": 0 if dry_run else len(items), "dry_run": dry_run}

    package = {
        "version": 1,
        "package_id": "xfer_shallow",
        "copy_mode": "shallow_instance",
        "target_cell": "TOP",
        "items": [{"child": "MZI", "position_um": [1, 2]}],
        "review": {"ok_to_commit": True},
    }

    result = commit_shallow_instance_package(TargetClient(), package)

    assert result["ok"] is True
    assert calls == [("TOP", [{"child": "MZI", "position_um": [1, 2]}], False)]


def test_review_deep_cell_tree_reports_conflicts_and_native_rename_policy():
    source_tree = {
        "cells": [
            {"name": "MZI", "child_cells": ["LEAF"]},
            {"name": "LEAF", "child_cells": []},
        ],
        "top_cell": "MZI",
    }
    target_cells = {"cells": [{"name": "TOP"}, {"name": "MZI"}, {"name": "LEAF"}]}

    review = review_deep_cell_tree(
        source_tree,
        target_cells=target_cells,
        source_session="klayout-8765",
        target_session="klayout-8767",
    )

    assert review["copy_mode"] == "deep_cell_tree"
    assert review["ok_to_commit"] is True
    assert review["rename_policy"] == "klayout_native_dollar_suffix"
    assert review["source_cells"] == ["MZI", "LEAF"]
    assert review["target_conflicts"] == ["MZI", "LEAF"]
    assert review["expected_behavior"] == "KLayout creates unique variants such as CELL$1"

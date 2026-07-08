from __future__ import annotations

import pytest

kdb = pytest.importorskip(
    "klayout.db", reason="klayout pip package not installed (bare env)")


def test_klayout_cell_copy_tree_uses_native_dollar_suffix_for_conflicts():
    source = kdb.Layout()
    source.dbu = 0.001
    source_layer = source.layer(kdb.LayerInfo(1, 0))
    source_leaf = source.create_cell("LEAF")
    source_leaf.shapes(source_layer).insert(kdb.Box(0, 0, 1000, 1000))
    source_parent = source.create_cell("MZI")
    source_parent.insert(kdb.CellInstArray(source_leaf.cell_index(), kdb.Trans(1000, 2000)))

    target = kdb.Layout()
    target.dbu = 0.002
    target_layer = target.layer(kdb.LayerInfo(1, 0))
    target_leaf = target.create_cell("LEAF")
    target_leaf.shapes(target_layer).insert(kdb.Box(0, 0, 2000, 2000))
    target.create_cell("MZI")

    copied_parent = target.create_cell(source_parent.name)
    copied_parent.copy_tree(source_parent)

    names = {cell.name for cell in target.each_cell()}
    assert {"MZI", "MZI$1", "LEAF", "LEAF$1"}.issubset(names)
    assert copied_parent.name == "MZI$1"
    assert [target.cell(index).name for index in copied_parent.each_child_cell()] == ["LEAF$1"]
    # DBU conversion happened while copying from 0.001 um to 0.002 um target.
    assert target.cell("LEAF$1").bbox() == kdb.Box(0, 0, 500, 500)
    assert copied_parent.bbox() == kdb.Box(500, 1000, 1000, 1500)


def test_shallow_instance_requires_existing_target_child_cell():
    target = kdb.Layout()
    parent = target.create_cell("TOP")

    assert target.cell("MZI") is None

    existing_child = target.create_cell("MZI")
    parent.insert(kdb.CellInstArray(existing_child.cell_index(), kdb.Trans(100, 200)))

    assert parent.child_instances() == 1
    assert [target.cell(index).name for index in parent.each_child_cell()] == ["MZI"]

from __future__ import annotations

import json
import math
from pathlib import Path

from klink.domains.fabrication import DieTemplate, WaferTemplate, compose_complete_mask


class StrictFakeClient:
    def __init__(self, cells=None, fail_on=None):
        self.cells = set(cells or [])
        self.layers = set()
        self.calls = []
        self.fail_on = fail_on

    def cell_list(self, **kwargs):
        self.calls.append(("cell_list", kwargs))
        cells = [{"name": name} for name in sorted(self.cells)]
        return {"total": len(cells), "returned": len(cells), "cells": cells}

    def cell_create(self, name=None):
        self.calls.append(("cell_create", name))
        if self.fail_on == "cell_create":
            raise RuntimeError("scripted create failure")
        if name in self.cells:
            effective = f"{name}$1"
        else:
            effective = str(name)
        self.cells.add(effective)
        return {"name": effective, "requested_name": name, "renamed": effective != name}

    def cell_delete(self, cell, recursive=True):
        self.calls.append(("cell_delete", cell, recursive))
        self.cells.discard(str(cell))
        return {"deleted_name": str(cell), "recursive": bool(recursive)}

    def layer_ensure(self, layer, datatype=0, name=None):
        self.calls.append(("layer_ensure", int(layer), int(datatype), name))
        if self.fail_on == "layer_ensure":
            raise RuntimeError("scripted layer failure")
        self.layers.add((int(layer), int(datatype)))
        return {"layer": int(layer), "datatype": int(datatype)}

    def shape_insert_many(self, cell, items, *, dry_run=False):
        self.calls.append(("shape_insert_many", cell, list(items), dry_run))
        if self.fail_on == "shape_insert_many":
            raise RuntimeError("scripted shape failure")
        if cell not in self.cells:
            raise RuntimeError(f"parent cell {cell!r} does not exist")
        missing_layers = sorted(
            {(int(item["layer"]), int(item.get("datatype", 0))) for item in items} - self.layers
        )
        if missing_layers:
            raise RuntimeError(f"shape_insert_many used unensured layer(s): {missing_layers}")
        return {"parent": cell, "requested": len(items), "inserted": len(items)}

    def instance_insert_many(self, parent, items, *, dry_run=False):
        self.calls.append(("instance_insert_many", parent, list(items), dry_run))
        if self.fail_on == "instance_insert_many":
            raise RuntimeError("scripted instance failure")
        if parent not in self.cells:
            raise RuntimeError(f"parent cell {parent!r} does not exist")
        missing = sorted({str(item["child"]) for item in items} - self.cells)
        if missing:
            raise RuntimeError(f"instance_insert_many child cell(s) missing: {missing}")
        return {"parent": parent, "requested": len(items), "inserted": len(items)}


def _base_cells():
    return {
        "PAYLOAD",
        "BOND",
        "LD",
        "LU",
        "RD",
        "RU",
        "GLOBAL",
        *(f"N{i}" for i in range(10)),
    }


def _die(*, bonding_positions=None, labels=None):
    return DieTemplate(
        payload_position_um=(50.0, 50.0),
        bonding={
            "cell": "BOND",
            "positions_um": bonding_positions
            if bonding_positions is not None
            else [[10.0, 20.0], [90.0, 20.0]],
        },
        corner_marks={
            "mode": "cells",
            "corners": {
                "leftdown": "LD",
                "leftup": "LU",
                "rightdown": "RD",
                "rightup": "RU",
            },
        },
        global_marks=[{"cell": "GLOBAL", "position_um": [50.0, 90.0]}],
        labels=labels or [],
        die_size_um=(100.0, 100.0),
    )


def _single_wafer(*, numbering_positions=None):
    return WaferTemplate(
        mode="single",
        position_um=(0.0, 0.0),
        numbering={
            "cells": {str(i): f"N{i}" for i in range(10)},
            "positions_um": numbering_positions or [],
        },
    )


def _compose(client, tmp_path, *, name="MASK", die=None, wafer=None, payload="PAYLOAD"):
    return compose_complete_mask(
        client,
        payload,
        name=name,
        die=die or _die(),
        wafer=wafer or _single_wafer(),
        facts_dir=str(tmp_path),
    )


def test_p2_strict_fake_requires_existing_cells_and_ensured_label_layers(tmp_path):
    client = StrictFakeClient(_base_cells())
    die = _die(labels=[{"text": "A", "position_um": [25.0, 25.0], "layer": "41/7", "size_um": 12.0}])

    result = _compose(client, tmp_path, die=die)

    assert result["ok"] is True
    assert ("layer_ensure", 41, 7, "FAB_COMPOSE_41_7") in client.calls
    shape_calls = [call for call in client.calls if call[0] == "shape_insert_many"]
    assert len(shape_calls) == 1
    assert shape_calls[0][2][0]["layer"] == 41
    assert "PAYLOAD" in client.cells


def test_p6_missing_corner_cell_fails_before_mutation(tmp_path):
    cells = _base_cells() - {"LD"}
    client = StrictFakeClient(cells)

    result = _compose(client, tmp_path)

    assert result["ok"] is False
    assert "missing cell 'LD'" in result["problems"][0]
    assert client.calls == [("cell_list", {"limit": 5000})]
    assert not list(Path(tmp_path).iterdir())


def test_p6_identical_corners_fail_before_cell_catalog_or_mutation(tmp_path):
    client = StrictFakeClient(_base_cells())
    die = DieTemplate(
        payload_position_um=(50.0, 50.0),
        bonding={"cell": "BOND", "positions_um": []},
        corner_marks={"mode": "cells", "corners": {key: "LD" for key in ("leftdown", "leftup", "rightdown", "rightup")}},
        die_size_um=(100.0, 100.0),
    )

    result = _compose(client, tmp_path, die=die)

    assert result["ok"] is False
    assert "four distinct" in result["problems"][0]
    assert client.calls == []
    assert not list(Path(tmp_path).iterdir())


def test_p6_payload_missing_and_name_collision_are_zero_scar_failures(tmp_path):
    client = StrictFakeClient(_base_cells() - {"PAYLOAD"})
    missing = _compose(client, tmp_path)

    assert missing["ok"] is False
    assert "missing cell 'PAYLOAD'" in missing["problems"][0]
    assert client.calls == [("cell_list", {"limit": 5000})]

    client = StrictFakeClient(_base_cells() | {"MASK_die"})
    collision = _compose(client, tmp_path)

    assert collision["ok"] is False
    assert "already exists" in "\n".join(collision["problems"])
    assert client.calls == [("cell_list", {"limit": 5000})]
    assert "MASK_wafer" not in client.cells


def test_p6_live_failure_deletes_created_cells_and_writes_no_facts(tmp_path):
    client = StrictFakeClient(_base_cells(), fail_on="instance_insert_many")

    result = _compose(client, tmp_path)

    assert result["ok"] is False
    assert "MASK_die" not in client.cells
    assert "MASK_wafer" not in client.cells
    assert ("cell_delete", "MASK_wafer", True) in client.calls
    assert ("cell_delete", "MASK_die", True) in client.calls
    assert not list(Path(tmp_path).iterdir())


def test_p7_boundary_exact_bonding_pad_on_die_edge_and_circular_site_kept(tmp_path):
    radius = 10.0
    die = DieTemplate(
        payload_position_um=(1.0, 2.0),
        bonding={"cell": "BOND", "positions_um": [[2.0, 2.0]]},
        corner_marks=_die().corner_marks,
        global_marks=[],
        die_size_um=(2.0, 4.0),
    )
    pitch_x = math.sqrt(radius**2 - 2.0**2) - 1.0
    wafer = WaferTemplate(
        mode="circular",
        wafer_diameter_um=20.0,
        exclusion_margin_um=0.0,
        die_pitch_um=(pitch_x, 99.0),
        center_um=(0.0, 0.0),
    )

    result = _compose(StrictFakeClient(_base_cells()), tmp_path, die=die, wafer=wafer)

    assert result["ok"] is True
    facts = json.loads(Path(result["facts_path"]).read_text(encoding="utf-8"))
    centers = [site["center_um"] for site in facts["wafer_sites"]]
    assert any(abs(center[0] - pitch_x) < 1e-9 and center[1] == 0.0 for center in centers)
    assert any(p["source_template_field"] == "die.bonding.positions_um[0]" for p in facts["placements"])


def test_p8_two_composes_in_one_process_have_identical_facts_modulo_name(tmp_path):
    client = StrictFakeClient(_base_cells())
    first = _compose(client, tmp_path, name="A")
    second = _compose(client, tmp_path, name="B")

    assert first["ok"] is True
    assert second["ok"] is True
    a = _normalize_facts(json.loads(Path(first["facts_path"]).read_text(encoding="utf-8")))
    b = _normalize_facts(json.loads(Path(second["facts_path"]).read_text(encoding="utf-8")))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_p10_facts_are_deterministic_and_run_meta_is_separate(tmp_path):
    first = _compose(StrictFakeClient(_base_cells()), tmp_path / "a", name="MASK")
    second = _compose(StrictFakeClient(_base_cells()), tmp_path / "b", name="MASK")

    assert first["ok"] is True
    assert second["ok"] is True
    first_bytes = Path(first["facts_path"]).read_bytes()
    second_bytes = Path(second["facts_path"]).read_bytes()
    assert first_bytes == second_bytes
    assert b"timestamp" not in first_bytes
    assert (tmp_path / "a" / "MASK.run_meta.json").exists()


def test_single_wll_structural_counts_with_synthetic_cells(tmp_path):
    bonding_positions = [[float(i), 10.0] for i in range(1, 13)]
    numbering_positions = [[float(i), 200.0] for i in range(10)]
    client = StrictFakeClient(_base_cells())

    result = _compose(
        client,
        tmp_path,
        die=_die(bonding_positions=bonding_positions),
        wafer=_single_wafer(numbering_positions=numbering_positions),
    )

    assert result["ok"] is True
    inst_calls = [call for call in client.calls if call[0] == "instance_insert_many"]
    assert inst_calls[0][1] == "MASK_die"
    assert len(inst_calls[0][2]) == 18
    assert inst_calls[1][1] == "MASK_wafer"
    assert len(inst_calls[1][2]) == 11


def _normalize_facts(payload):
    text = json.dumps(payload, sort_keys=True)
    text = text.replace("A_die", "NAME_die").replace("A_wafer", "NAME_wafer").replace('"A"', '"NAME"')
    text = text.replace("B_die", "NAME_die").replace("B_wafer", "NAME_wafer").replace('"B"', '"NAME"')
    return json.loads(text)

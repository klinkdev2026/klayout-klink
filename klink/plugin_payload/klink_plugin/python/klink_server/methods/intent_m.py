"""intent.apply_managed_plan -- the single mechanical commit primitive.

Design contract: docs/REGION_INTENT_DESIGN.md sect.8/sect.9. All planning, AI, and
validation happen BEFORE this call; the payload is fully materialized typed
geometry. Inside the transaction this method only writes -- it never runs
business logic that can fail halfway (KLayout exposes no transaction abort,
proven by the I0 probe, so validate-before-mutate is the only safe shape).

Ownership model (I0 ruling): revisioned container child + root swap. Each
apply creates a FRESH container cell; `mode=replace` finds the old root
instance by its klink_id property, erases it, inserts the new root, and
deletes the old container cell (delete_cell, NOT prune_cell -- child cells
such as the user's source cell must survive).

The managed digest is a same-process comparison value (canonicalized
content of a container subtree), used to detect user edits (diverged)
between prepare and apply. It is NOT a portable cross-machine contract.
"""

from __future__ import annotations

import pya

from ..errors import ErrorCode, RpcError
from ..registry import method
from ..txn import auto_txn
from .cell_m import _active_layout, _resolve_cell
from .instance_m import KLINK_ID_PROPKEY
from .region_m import (
    _canonical_hash,
    _composed_polygon,
    _occupancy_for,
    _parse_layer_ld,
    _require_single_region,
    _scope_hash,
)

_MAX_PAYLOAD_SHAPES = 200_000
_MAX_PAYLOAD_INSTANCES = 100_000


def managed_digest(ly: pya.Layout, cell: pya.Cell) -> str:
    """Canonical digest of a container cell's own content.

    Shapes (all layers, sorted) + child instances (name + trans, sorted).
    Container cells hold only managed output, so one level is the whole
    truth (child cells are user cells whose identity, not content, is what
    the container references).
    """
    shape_rows = []
    for idx in ly.layer_indexes():
        info = ly.get_info(idx)
        for shape in cell.each_shape(idx):
            try:
                poly = shape.polygon
                if poly is None:
                    row = str(shape)
                else:
                    row = poly.to_s()
            except Exception:
                row = str(shape)
            shape_rows.append("%d/%d:%s" % (info.layer, info.datatype, row))
    inst_rows = []
    for inst in cell.each_inst():
        try:
            child = inst.cell.name
        except Exception:
            child = "?"
        try:
            trans = str(inst.cplx_trans)
        except Exception:
            trans = str(inst.trans)
        inst_rows.append("%s@%s" % (child, trans))
    return _canonical_hash({"shapes": sorted(shape_rows),
                            "instances": sorted(inst_rows)})


def scope_check_hash(ly: pya.Layout, parent: pya.Cell, region_poly,
                     layers: list[str],
                     exclude_cells: tuple[str, ...] = (),
                     obstacle_cells: tuple[str, ...] = ()) -> str:
    """Re-run the occupancy query and fingerprint it (same code path as
    region.occupancy's returned scope_hash)."""
    return _scope_hash(
        _occupancy_for(ly, parent, region_poly, layers, exclude_cells,
                       obstacle_cells))


def _find_roots_by_klink_id(parent: pya.Cell, klink_id: str) -> list:
    hits = []
    for inst in parent.each_inst():
        try:
            if str(inst.property(KLINK_ID_PROPKEY) or "") == klink_id:
                hits.append(inst)
        except Exception:
            pass
    return hits


def _validate_payload(ly: pya.Layout, payload: dict) -> None:
    shapes = payload.get("shapes") or []
    instances = payload.get("instances") or []
    if not shapes and not instances:
        raise RpcError(ErrorCode.BAD_PARAMS, "payload is empty",
                       hint="a plan must place at least one shape or instance")
    if len(shapes) > _MAX_PAYLOAD_SHAPES:
        raise RpcError(ErrorCode.BAD_PARAMS,
                       "payload has %d shapes; limit %d"
                       % (len(shapes), _MAX_PAYLOAD_SHAPES))
    if len(instances) > _MAX_PAYLOAD_INSTANCES:
        raise RpcError(ErrorCode.BAD_PARAMS,
                       "payload has %d instances; limit %d"
                       % (len(instances), _MAX_PAYLOAD_INSTANCES))
    for i, item in enumerate(shapes):
        _parse_layer_ld(str(item.get("layer", "")))
        pts = item.get("polygon_dbu")
        if not isinstance(pts, list) or len(pts) < 3:
            raise RpcError(ErrorCode.BAD_PARAMS,
                           "payload shape %d needs polygon_dbu with >=3 "
                           "points" % i)
    for i, item in enumerate(instances):
        child = str(item.get("child", ""))
        if ly.cell(child) is None:
            raise RpcError(
                ErrorCode.NOT_FOUND,
                "payload instance %d references unknown cell %r" % (i, child),
                hint="the plan's source cell must exist before apply",
            )
        t = item.get("trans_dbu")
        if not isinstance(t, list) or len(t) != 2:
            raise RpcError(ErrorCode.BAD_PARAMS,
                           "payload instance %d needs trans_dbu [x, y]" % i)
        rot = int(item.get("rotation_deg", 0))
        if rot not in (0, 90, 180, 270):
            raise RpcError(ErrorCode.BAD_PARAMS,
                           "payload instance %d rotation_deg must be one of "
                           "0/90/180/270" % i)


def _payload_polygon(item: dict) -> pya.Polygon:
    poly = pya.Polygon([pya.Point(int(x), int(y))
                        for x, y in item["polygon_dbu"]])
    for hole in item.get("holes_dbu") or []:
        poly.insert_hole([pya.Point(int(x), int(y)) for x, y in hole])
    return poly


@method(
    "intent.remove_managed_output",
    description=(
        "Delete ONE managed output (root instance + container cell) after "
        "verifying identity and digest. Refuses 0/multiple root matches "
        "and diverged containers (digest mismatch) -- hand-edited output is "
        "never silently destroyed. One transaction, one undo step."
    ),
    params_schema={
        "type": "object",
        "required": ["parent_cell", "root_klink_id",
                     "expected_managed_digest"],
        "properties": {
            "parent_cell": {"description": "name or cell_index"},
            "root_klink_id": {"type": "string"},
            "expected_managed_digest": {"type": "string"},
        },
    },
    returns_schema={"type": "object"},
    mutates=True,
    tags=["intent", "write", "delete"],
)
def intent_remove_managed_output(params, ctx):
    view, _, ly = _active_layout()
    parent = _resolve_cell(ly, params["parent_cell"])
    root_id = str(params["root_klink_id"])
    roots = _find_roots_by_klink_id(parent, root_id)
    if len(roots) != 1:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "found %d root instances with klink_id %r; removal needs "
            "exactly one" % (len(roots), root_id),
            hint="0 = already gone (nothing to do); >1 = copy-paste "
                 "duplicate, repair first",
        )
    root = roots[0]
    container = root.cell
    expected = str(params["expected_managed_digest"])
    current = managed_digest(ly, container)
    if current != expected:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "managed output diverged (digest mismatch); refusing to delete "
            "hand-edited content",
            hint="inspect the container %r; delete it manually if you "
                 "really mean it" % container.name,
        )
    container_name = container.name
    container_idx = container.cell_index()
    with auto_txn(view, "klink: intent.remove_managed_output %s"
                        % container_name):
        parent.erase(root)
        try:
            ly.delete_cell(container_idx)
        except Exception:
            pass
    return {"removed_container": container_name, "parent_cell": parent.name}


@method(
    "intent.managed_digest",
    description=(
        "Managed digest of a container cell's own content (canonicalized "
        "shapes + child instances). Used for the lazy undone/diverged check: "
        "compare against the digest recorded by the last apply."
    ),
    params_schema={
        "type": "object",
        "required": ["container_cell"],
        "properties": {"container_cell": {"description": "name or index"}},
    },
    returns_schema={"type": "object"},
    tags=["intent", "read"],
)
def intent_managed_digest(params, ctx):
    _, _, ly = _active_layout()
    cell = _resolve_cell(ly, params["container_cell"])
    return {"container_cell": cell.name,
            "managed_digest": managed_digest(ly, cell)}


@method(
    "intent.apply_managed_plan",
    description=(
        "Mechanically commit a fully-validated managed plan in ONE "
        "transaction: create a fresh container cell from the typed payload, "
        "place its root instance (klink_id-stamped) in the parent, and on "
        "mode=replace swap out the previous container. All business "
        "validation must happen BEFORE this call; TOCTOU is guarded by "
        "scope_check (re-run + compare) and expected_managed_digest. "
        "Refuses 0 or multiple root matches, digest mismatches, and stale "
        "scope. One undo step reverts the whole apply."
    ),
    params_schema={
        "type": "object",
        "required": ["mode", "parent_cell", "container_cell", "root_klink_id",
                     "payload", "plan_hash"],
        "properties": {
            "mode": {"type": "string", "enum": ["create", "replace"]},
            "parent_cell": {"description": "name or cell_index"},
            "container_cell": {
                "type": "string",
                "description": "Fresh container cell name (revisioned, e.g. "
                               "KLINK_I_ab12_r2); must not exist yet.",
            },
            "root_klink_id": {"type": "string"},
            "expected_root_klink_id": {
                "type": "string",
                "description": "replace mode: klink_id of the CURRENT root "
                               "instance to swap out.",
            },
            "expected_managed_digest": {
                "type": "string",
                "description": "replace mode: managed digest of the current "
                               "container (from the last apply); mismatch = "
                               "diverged = refuse.",
            },
            "scope_check": {
                "type": "object",
                "description": "{region_name, layers[], expected_hash}: the "
                               "occupancy fingerprint from prepare time; "
                               "re-run and compared before writing.",
                "properties": {
                    "region_name": {"type": "string"},
                    "layers": {"type": "array", "items": {"type": "string"}},
                    "exclude_cells": {"type": "array",
                                      "items": {"type": "string"}},
                    "obstacle_cells": {"type": "array",
                                       "items": {"type": "string"}},
                    "expected_hash": {"type": "string"},
                },
                "required": ["region_name", "layers", "expected_hash"],
            },
            "payload": {
                "type": "object",
                "properties": {
                    "shapes": {"type": "array"},
                    "instances": {"type": "array"},
                    "root_trans_dbu": {"type": "array", "minItems": 2,
                                       "maxItems": 2},
                },
            },
            "plan_hash": {"type": "string"},
        },
    },
    returns_schema={"type": "object"},
    mutates=True,
    tags=["intent", "write"],
)
def intent_apply_managed_plan(params, ctx):
    view, _, ly = _active_layout()
    parent = _resolve_cell(ly, params["parent_cell"])
    mode = str(params["mode"])
    container_name = str(params["container_cell"])
    root_klink_id = str(params["root_klink_id"])
    payload = params.get("payload") or {}

    # ---- validate EVERYTHING before the transaction ----
    if ly.cell(container_name) is not None:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "container cell %r already exists" % container_name,
            hint="container names are revisioned; bump the revision suffix",
        )
    _validate_payload(ly, payload)

    old_root = None
    old_container = None
    if mode == "replace":
        expected_id = str(params.get("expected_root_klink_id", ""))
        if not expected_id:
            raise RpcError(ErrorCode.BAD_PARAMS,
                           "replace mode requires expected_root_klink_id")
        roots = _find_roots_by_klink_id(parent, expected_id)
        if len(roots) != 1:
            raise RpcError(
                ErrorCode.BAD_PARAMS,
                "found %d root instances with klink_id %r; replace needs "
                "exactly one" % (len(roots), expected_id),
                hint="0 = output was deleted or id stripped (rebind or use "
                     "mode=create); >1 = copy-paste duplicate (repair first)",
            )
        old_root = roots[0]
        old_container = old_root.cell
        expected_digest = str(params.get("expected_managed_digest", ""))
        if not expected_digest:
            raise RpcError(ErrorCode.BAD_PARAMS,
                           "replace mode requires expected_managed_digest")
        current = managed_digest(ly, old_container)
        if current != expected_digest:
            raise RpcError(
                ErrorCode.BAD_PARAMS,
                "managed output diverged: current digest %s != expected %s"
                % (current[:18], expected_digest[:18]),
                hint="the container was edited (or undone) since the last "
                     "apply; choose overwrite/preserve explicitly by "
                     "re-preparing against the current state",
            )
    else:
        roots = _find_roots_by_klink_id(parent, root_klink_id)
        if roots:
            raise RpcError(
                ErrorCode.BAD_PARAMS,
                "root klink_id %r already present in %s"
                % (root_klink_id, parent.name),
                hint="use mode=replace with expected_root_klink_id to "
                     "regenerate",
            )

    scope = params.get("scope_check")
    if scope:
        _, region_inst = _require_single_region(ly, str(scope["region_name"]))
        region_poly = _composed_polygon(region_inst)
        current_hash = scope_check_hash(
            ly, parent, region_poly,
            [str(v) for v in (scope.get("layers") or [])],
            tuple(str(v) for v in (scope.get("exclude_cells") or [])),
            tuple(str(v) for v in (scope.get("obstacle_cells") or [])))
        if current_hash != str(scope["expected_hash"]):
            raise RpcError(
                ErrorCode.BAD_PARAMS,
                "layout changed between prepare and apply (scope hash "
                "mismatch)",
                hint="re-run intent.prepare against the current layout",
            )

    # resolve layers up front (insert_layer mutates layer table, do before txn)
    layer_indexes: dict[str, int] = {}
    for item in payload.get("shapes") or []:
        layer_str = str(item["layer"])
        if layer_str not in layer_indexes:
            layer, datatype = _parse_layer_ld(layer_str)
            info = pya.LayerInfo(layer, datatype)
            idx = ly.find_layer(info)
            if idx is None:
                idx = ly.insert_layer(info)
            layer_indexes[layer_str] = int(idx)

    root_trans = payload.get("root_trans_dbu") or [0, 0]
    replaced_name = old_container.name if old_container is not None else None

    # ---- single mechanical transaction ----
    with auto_txn(view, "klink: intent.apply_managed_plan %s" % container_name):
        container = ly.create_cell(container_name)
        for item in payload.get("shapes") or []:
            container.shapes(layer_indexes[str(item["layer"])]).insert(
                _payload_polygon(item))
        for item in payload.get("instances") or []:
            child = ly.cell(str(item["child"]))
            rot = int(item.get("rotation_deg", 0))
            mirror = bool(item.get("mirror", False))
            trans = pya.Trans(rot // 90, mirror,
                              pya.Vector(int(item["trans_dbu"][0]),
                                         int(item["trans_dbu"][1])))
            container.insert(pya.CellInstArray(child.cell_index(), trans))
        root_inst = parent.insert(pya.CellInstArray(
            container.cell_index(),
            pya.Trans(int(root_trans[0]), int(root_trans[1]))))
        try:
            root_inst.set_property(KLINK_ID_PROPKEY, root_klink_id)
        except Exception:
            pass
        if old_root is not None:
            old_container_idx = old_container.cell_index()
            parent.erase(old_root)
            try:
                ly.delete_cell(old_container_idx)
            except Exception:
                pass

    digest_after = managed_digest(ly, container)
    return {
        "mode": mode,
        "parent_cell": parent.name,
        "container_cell": container_name,
        "root_klink_id": root_klink_id,
        "shapes_written": len(payload.get("shapes") or []),
        "instances_written": len(payload.get("instances") or []),
        "managed_digest": digest_after,
        "replaced_container": replaced_name,
        "plan_hash": str(params["plan_hash"]),
    }

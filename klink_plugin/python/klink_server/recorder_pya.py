"""
pya code renderer for klink recorder.

Generates bare pya snippets for every recorded action. These snippets are
assembled into a dual-mode .py file by ``Recorder._write_file``: inside
KLayout the pya runs directly; outside KLayout the whole block is pushed
through ``pyexec``.
"""

from __future__ import annotations

import textwrap
from typing import Optional


# ---------------------------------------------------------------------------
# angle helpers
# ---------------------------------------------------------------------------
_INT_ROT = {0: 0, 90: 1, 180: 2, 270: 3, -90: 3, -180: 2, -270: 1}


def _trans_str(trans: dict) -> str:
    """Return a ``pya.Trans(...)`` or ``pya.ICplxTrans(...)`` expression."""
    disp = trans.get("disp") or [0, 0]
    x, y = int(disp[0]), int(disp[1])
    angle = float(trans.get("angle", 0.0))
    mirror = bool(trans.get("mirror", False))
    mag = float(trans.get("mag", 1.0))

    is_simple = abs(mag - 1.0) < 1e-12 and abs(angle - round(angle)) < 1e-9
    if is_simple:
        r = int(round(angle)) % 360
        rot = _INT_ROT.get(r, 0)
        return f"pya.Trans({rot}, {mirror}, {x}, {y})"
    return f"pya.ICplxTrans({mag!r}, {angle!r}, {mirror}, {x}, {y})"


# ---------------------------------------------------------------------------
# shape insert
# ---------------------------------------------------------------------------
def shape_insert(cell_name: str, L: int, D: int, s: dict) -> Optional[str]:
    """Return pya code that inserts *one* shape into *cell_name* on L/D."""
    kind = s.get("type")
    if kind == "box":
        b = s["bbox_dbu"]
        return (
            f"cell = _C(ly, {cell_name!r})\n"
            f"li = _LI(ly, {int(L)}, {int(D)})\n"
            f"cell.shapes(li).insert(pya.Box("
            f"{int(b[0])}, {int(b[1])}, {int(b[2])}, {int(b[3])}))"
        )

    if kind == "polygon":
        pts = s.get("points_dbu") or []
        if len(pts) < 3:
            return None
        pts_str = ", ".join(f"pya.Point({int(p[0])}, {int(p[1])})" for p in pts)
        return (
            f"cell = _C(ly, {cell_name!r})\n"
            f"li = _LI(ly, {int(L)}, {int(D)})\n"
            f"cell.shapes(li).insert(pya.Polygon([{pts_str}]))"
        )

    if kind == "path":
        pts = s.get("points_dbu") or []
        if len(pts) < 2:
            return None
        w = int(s.get("width_dbu", 0))
        bext = int(s.get("begin_ext_dbu", 0))
        eext = int(s.get("end_ext_dbu", 0))
        rnd = bool(s.get("round_ends", False))
        pts_str = ", ".join(f"pya.Point({int(p[0])}, {int(p[1])})" for p in pts)
        return (
            f"cell = _C(ly, {cell_name!r})\n"
            f"li = _LI(ly, {int(L)}, {int(D)})\n"
            f"cell.shapes(li).insert(pya.Path([{pts_str}], "
            f"{w}, {bext}, {eext}, {rnd}))"
        )

    if kind == "text":
        pos = s.get("position_dbu")
        txt = s.get("string", "")
        if not pos or len(pos) < 2:
            return None
        return (
            f"cell = _C(ly, {cell_name!r})\n"
            f"li = _LI(ly, {int(L)}, {int(D)})\n"
            f"cell.shapes(li).insert(pya.Text({txt!r}, "
            f"pya.Trans({int(pos[0])}, {int(pos[1])})))"
        )

    return None  # unsupported kind (edges, user objects, etc.)


# ---------------------------------------------------------------------------
# shape delete
# ---------------------------------------------------------------------------
def shape_delete(cell_name: str, L: int, D: int, s: dict) -> Optional[str]:
    """Return pya code that deletes shapes matching *s* in *cell_name* on L/D.

    Uses ``each_touching(bbox)`` + kind filter, same approach as the RPC
    ``shape.delete``.  Not bullet-proof against overlapping shapes of the
    same kind, but neither is the RPC version.
    """
    kind = s.get("type")
    bbox = s.get("bbox_dbu")
    if (not bbox or len(bbox) != 4) and kind in ("polygon", "path"):
        pts = s.get("points_dbu") or []
        if pts:
            px = int(pts[0][0])
            py = int(pts[0][1])
            pad = 2 + (int(s.get("width_dbu", 0)) // 2 if kind == "path" else 0)
            bbox = [px - pad, py - pad, px + pad, py + pad]
    if not bbox or len(bbox) != 4:
        return None
    kind_checks = {
        "box": "_s.is_box()",
        "polygon": "_s.is_polygon() or _s.is_simple_polygon()",
        "path": "_s.is_path()",
        "text": "_s.is_text()",
    }
    kind_test = kind_checks.get(kind)
    if kind_test is None:
        return None

    return (
        f"cell = _C(ly, {cell_name!r})\n"
        f"li = _LI(ly, {int(L)}, {int(D)})\n"
        f"_shapes = cell.shapes(li)\n"
        f"for _s in list(_shapes.each_touching(pya.Box("
        f"{int(bbox[0])}, {int(bbox[1])}, {int(bbox[2])}, {int(bbox[3])}))):\n"
        f"    if {kind_test}:\n"
        f"        _shapes.erase(_s)"
    )


# ---------------------------------------------------------------------------
# instance insert
# ---------------------------------------------------------------------------
def instance_insert(parent_name: str, inst: dict) -> Optional[str]:
    """Return pya code to insert one instance into *parent_name*."""
    pcell = inst.get("pcell")
    target = inst.get("target_cell", "")

    if pcell:
        # PCell variant: ly.create_cell first, then insert.
        lib = pcell.get("lib", "Basic")
        pname = pcell.get("pcell_name") or pcell.get("name")
        if not pname:
            return None
        params = pcell.get("params") or {}
        params_str = _pcell_params_str(params)
        trans_str = _trans_str(inst.get("trans_dbu") or {})
        return (
            f"cell = _C(ly, {parent_name!r})\n"
            f"variant = ly.create_cell({pname!r}, {lib!r}, {params_str})\n"
            f"cell.insert(pya.CellInstArray("
            f"variant.cell_index(), {trans_str}))"
        )

    # Regular instance.
    if not target:
        return None
    trans_str = _trans_str(inst.get("trans_dbu") or {})

    arr = inst.get("array")
    if arr:
        na = int(arr.get("na", 1))
        nb = int(arr.get("nb", 1))
        a = arr.get("a_dbu", [0, 0])
        b = arr.get("b_dbu", [0, 0])
        return (
            f"cell = _C(ly, {parent_name!r})\n"
            f"ci = _C(ly, {target!r}).cell_index()\n"
            f"cell.insert(pya.CellInstArray("
            f"ci, {trans_str}, "
            f"pya.Vector({int(a[0])}, {int(a[1])}), "
            f"pya.Vector({int(b[0])}, {int(b[1])}), {na}, {nb}))"
        )

    return (
        f"cell = _C(ly, {parent_name!r})\n"
        f"ci = _C(ly, {target!r}).cell_index()\n"
        f"cell.insert(pya.CellInstArray(ci, {trans_str}))"
    )


def _pcell_params_str(params: dict) -> str:
    """Render a PCell params dict as a pya-friendly dict literal.

    Layer-like dicts (``{"layer": 1, "datatype": 0}``) are converted to
    ``pya.LayerInfo(1, 0)``.  Everything else is passed through as-is with
    ``repr()``.
    """
    items = []
    for k, v in params.items():
        val_str = _adapt_value(v)
        items.append(f"{k!r}: {val_str}")
    return "{" + ", ".join(items) + "}"


def _adapt_value(v) -> str:
    """Convert a JSON-safe PCell value back to a pya expression string."""
    if isinstance(v, dict):
        if "layer" in v and ("datatype" in v or len(v) <= 2):
            return f"pya.LayerInfo({int(v['layer'])}, {int(v.get('datatype', 0))})"
        if "bbox_um" in v:
            b = v["bbox_um"]
            return f"pya.DBox({float(b[0])!r}, {float(b[1])!r}, {float(b[2])!r}, {float(b[3])!r})"
        if "points_um" in v:
            pts = v["points_um"]
            pts_s = ", ".join(
                f"pya.DPoint({float(p[0])!r}, {float(p[1])!r})" for p in pts
            )
            return f"pya.DPolygon([{pts_s}])"
        if "point_um" in v:
            p = v["point_um"]
            return f"pya.DPoint({float(p[0])!r}, {float(p[1])!r})"
        # nested unknown dict: repr as-is
        return repr(v)
    if isinstance(v, str):
        return repr(v)
    return repr(v)


# ---------------------------------------------------------------------------
# instance delete
# ---------------------------------------------------------------------------
def instance_delete(parent_name: str, inst: dict) -> Optional[str]:
    """Return pya code to delete one instance from *parent_name*."""
    target = inst.get("target_cell")
    trans = inst.get("trans_dbu") or {}
    disp = trans.get("disp") or [0, 0]
    if not target:
        return None
    x, y = int(disp[0]), int(disp[1])
    return (
        f"cell = _C(ly, {parent_name!r})\n"
        f"for _inst in list(cell.each_inst()):\n"
        f"    if _inst.cell.name == {target!r}:\n"
        f"        _bb = _inst.bbox()\n"
        f"        if _bb.touches(pya.Box({x - 1}, {y - 1}, {x + 1}, {y + 1})):\n"
        f"            cell.erase(_inst)"
    )


# ---------------------------------------------------------------------------
# cell / layer helpers
# ---------------------------------------------------------------------------
def cell_create(name: str) -> str:
    return f"_C(ly, {name!r})"


def cell_delete(name: str) -> str:
    return (
        f"_c = ly.cell({name!r})\n"
        f"if _c is not None:\n"
        f"    ly.delete_cell_rec(_c.cell_index())"
    )


def cell_rename(old_name: str, new_name: str) -> str:
    return (
        f"_c = ly.cell({old_name!r})\n"
        f"if _c is not None:\n"
        f"    _c.name = {new_name!r}"
    )


def layer_ensure(L: int, D: int) -> str:
    return f"_LI(ly, {int(L)}, {int(D)})"


def selection_clear() -> str:
    return (
        "mw = pya.Application.instance().main_window()\n"
        "if mw is not None:\n"
        "    _v = mw.current_view()\n"
        "    if _v is not None:\n"
        "        _v.clear_object_selection()"
    )


def selection_comment(count: int, items: list) -> str:
    preview = ", ".join(
        _item_brief(it) for it in items[:3]
    )
    more = "" if len(items) <= 3 else f" (+{len(items) - 3} more)"
    return f"# selection -> {count} item(s): {preview}{more}"


def _item_brief(it: dict) -> str:
    if it.get("is_cell_inst"):
        return f"inst->{it.get('target_cell')!r}"
    return (
        f"{it.get('shape_type', 'shape')}("
        f"{it.get('layer')}/{it.get('datatype', 0)})"
    )


# ---------------------------------------------------------------------------
# main renderer
# ---------------------------------------------------------------------------
def render_file(path: str, elapsed: float, *,
                event_count: int = 0,
                initial_cells: list = (),
                initial_layers: list = (),
                initial_top_cell: str = "",
                actions: list = (),
                ) -> None:
    """Write a dual-mode pya recording to *path*."""
    import os
    import time as _time

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    stamp = _time.strftime("%Y-%m-%d %H:%M:%S")
    n = len(actions)

    lines: list[str] = []
    _emit_header(lines, stamp, elapsed, event_count, n,
                 initial_cells, initial_top_cell)

    # -- helpers --
    lines.append("def _C(ly, name):")
    lines.append("    c = ly.cell(name)")
    lines.append("    return c if c is not None else ly.create_cell(name)")
    lines.append("")
    lines.append("def _LI(ly, L, D):")
    lines.append("    li = ly.find_layer(pya.LayerInfo(L, D))")
    lines.append("    return li if li is not None else ly.insert_layer(pya.LayerInfo(L, D))")
    lines.append("")

    # -- bootstrap --
    _emit_bootstrap(lines, initial_cells, initial_layers)
    lines.append("")

    # -- actions wrapper --
    lines.append("def _replay(ly, pya):")
    if n == 0:
        lines.append("    pass  # (no actions recorded)")
    else:
        last_cause = None
        for a in actions:
            caused = a.get("caused") or ""
            code = a.get("pya") or ""
            if not code:
                continue
            if caused and caused != last_cause:
                lines.append(f"    # -- {caused} --")
                last_cause = caused
            ts = f"+{a['t']:6.2f}s"
            for i, line in enumerate(code.splitlines()):
                if i == 0 and not line.strip().startswith("#"):
                    lines.append(f"    {line}  # {ts}")
                else:
                    lines.append(f"    {line}")
    lines.append("")
    lines.append("")

    # -- dual-mode main --
    lines.append("if __name__ == '__main__':")
    lines.append("    import os, sys")
    lines.append("    _IN_KLAYOUT = False")
    lines.append("    try:")
    lines.append("        import pya")
    lines.append("        pya.Application.instance()  # must be real KLayout pya")
    lines.append("        _IN_KLAYOUT = True")
    lines.append("    except (ImportError, AttributeError):")
    lines.append("        pass")
    lines.append("    if not _IN_KLAYOUT:")
    lines.append("        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))")
    lines.append("        from klink import KLinkClient")
    lines.append("        _kl = KLinkClient().connect()")
    lines.append("        _kl.pyexec(")
    # collect all pya code into one pyexec call
    all_pya = _collect_pya_blocks(initial_cells, initial_layers, actions)
    _append_multiline_string(lines, all_pya, indent="        ")
    lines.append("        )")
    lines.append("        _kl.close()")
    lines.append("    else:")
    lines.append("        mw = pya.Application.instance().main_window()")
    lines.append("        if mw is not None:")
    lines.append("            ly = mw.current_view().active_cellview().layout()")
    lines.append("            _replay(ly, pya)")
    lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _emit_header(lines: list, stamp: str, elapsed: float,
                 event_count: int, n: int,
                 initial_cells: list, initial_top_cell: str) -> None:
    lines.append(f'"""klink recording (pya), {stamp}.')
    lines.append(f"{n} action(s) over {elapsed:.1f}s, "
                 f"{event_count} event(s) observed.")
    if initial_cells:
        top_names = [e["name"] for e in initial_cells if e["is_top"]]
        lines.append(
            f"Started from a layout with {len(initial_cells)} "
            f"cell(s), tops={top_names!r}, "
            f"active={initial_top_cell!r}."
        )
    lines.append("")
    lines.append("Dual-mode:")
    lines.append("  python <this-file>.py      -> replays via klink pyexec")
    lines.append("  KLayout Macro IDE          -> runs bare pya directly")
    lines.append('"""')
    lines.append("")


def _emit_bootstrap(lines: list, initial_cells: list,
                    initial_layers: list) -> None:
    """Emit a _bootstrap function that ensures cells + layers exist."""
    lines.append("def _bootstrap(ly, pya):")
    if not initial_cells and not initial_layers:
        lines.append("    pass  # nothing to bootstrap")
        return
    if initial_cells:
        names = [e["name"] for e in initial_cells]
        lines.append(f"    for nm in {names!r}:")
        lines.append("        _C(ly, nm)")
    if initial_layers:
        lines.append(f"    for _L, _D, _name in {initial_layers!r}:")
        lines.append("        _LI(ly, _L, _D)")
    lines.append("")


def _collect_pya_blocks(initial_cells: list, initial_layers: list,
                        actions: list) -> str:
    """Assemble all pya code (bootstrap + actions) into one big string."""
    parts = []
    parts.append("import pya")
    parts.append("mw = pya.Application.instance().main_window()")
    parts.append("ly = mw.current_view().active_cellview().layout()")
    parts.append("")
    parts.append("def _C(ly, name):")
    parts.append("    c = ly.cell(name)")
    parts.append("    return c if c is not None else ly.create_cell(name)")
    parts.append("")
    parts.append("def _LI(ly, L, D):")
    parts.append("    li = ly.find_layer(pya.LayerInfo(L, D))")
    parts.append("    return li if li is not None else ly.insert_layer(pya.LayerInfo(L, D))")
    parts.append("")

    # bootstrap
    for entry in initial_cells:
        parts.append(f"_C(ly, {entry['name']!r})")
    for L, D, _name in initial_layers:
        parts.append(f"_LI(ly, {L}, {D})")
    if initial_cells or initial_layers:
        parts.append("")

    # actions
    for a in actions:
        code = a.get("pya")
        if code:
            parts.append(code)

    return "\n".join(parts)


def _append_multiline_string(lines: list, content: str, indent: str = "") -> None:
    """Append a multi-line Python string literal to *lines*."""
    for i, line in enumerate(content.splitlines()):
        escaped = line.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{indent}"{escaped}\\n"')

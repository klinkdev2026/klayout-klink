"""
2.5d view methods — KLayout's native extruded-3D stack viewer.

`view.show_25d` : open the Tools > 2.5d View window and feed it a display
                  list (one entry per material: layer, z range in microns,
                  optional name/colors). Geometry is read from a cell of the
                  active layout.

Kept separate from view_m (2D canvas control): the 2.5d viewer is its own
subsystem — a distinct dialog created only by KLayout's internal factory,
the official `D25View` API (since 0.28), and its own failure mode (builds
without OpenGL don't have it). The RPC keeps the `view.` prefix for
discoverability (it is still "look at the layout", and the MCP catalog
groups view.* under connection & view).

z heights are process facts the CALLER owns — klink ships none; derive the
display list with klink.stack25d.stack_displays from your StackSpec + z
table.
"""

from __future__ import annotations

import pya

from ..registry import method
from ..errors import RpcError, ErrorCode
from .cell_m import _active_layout


# ---------------------------------------------------------------------------
# view.show_25d — feed KLayout's native 2.5d viewer from a display list
# ---------------------------------------------------------------------------

def _find_d25_view():
    """Locate (or have KLayout create) the factory-made D25View dialog.

    pya.D25View cannot be instantiated directly (its `begin` refuses objects
    not created by KLayout's own factory: "Object cannot be created here").
    The documented creation path is the Tools > 2.5d View menu; triggering
    the `open_window` action makes KLayout build the dialog, after which the
    instance is discoverable among the top-level widgets. Verified live on
    KLayout 0.30.x; the D25View API itself is official since 0.28.
    """
    if not hasattr(pya, "D25View"):
        raise RpcError(
            ErrorCode.INTERNAL,
            "this KLayout build has no D25View (2.5d view needs KLayout >= "
            "0.28 compiled with OpenGL support)",
            hint="install a standard desktop build from klayout.de")

    def _live_instances():
        app = pya.QApplication.instance()
        return [w for w in app.topLevelWidgets()
                if isinstance(w, pya.D25View) and w.isVisible()]

    views = _live_instances()
    if not views:
        mw = pya.Application.instance().main_window()
        try:
            mw.menu().action("tools_menu.d25.open_window").trigger()
        except Exception as exc:
            raise RpcError(
                ErrorCode.INTERNAL,
                f"cannot open the 2.5d window: {exc}",
                hint="Tools > 2.5d View is unavailable; this KLayout build "
                     "may lack OpenGL support")
        views = _live_instances()
    if not views:
        raise RpcError(
            ErrorCode.INTERNAL,
            "the 2.5d window did not appear after triggering "
            "Tools > 2.5d View > Open Window",
            hint="this KLayout build may lack OpenGL support")
    return views[0]


@method(
    "view.show_25d",
    description=(
        "Open KLayout's native 2.5d (extruded 3D) viewer and feed it a "
        "display list: one entry per material with a layer, a z range in "
        "microns, and optional name/colors. Layers are read from a cell of "
        "the active layout (default: the current cell). klink ships no "
        "z heights - thickness/elevation are process facts the caller owns "
        "(derive the list with klink.stack25d.stack_displays from your "
        "StackSpec + z table). Requires a KLayout build with OpenGL."
    ),
    params_schema={
        "type": "object",
        "required": ["displays"],
        "properties": {
            "cell": {"type": "string",
                     "description": "cell to read geometry from (default: "
                                    "current cell)"},
            "generator": {"type": "string",
                          "description": "label shown by the 2.5d window "
                                         "(default 'klink')"},
            "displays": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["layer", "zstart_um", "zstop_um"],
                    "properties": {
                        "layer": {"type": "string",
                                  "description": "'L/D' source layer"},
                        "zstart_um": {"type": "number"},
                        "zstop_um": {"type": "number"},
                        "name": {"type": "string"},
                        "color": {"type": "integer",
                                  "description": "0xRRGGBB fill+frame"},
                        "frame_color": {"type": "integer"},
                        "fill_color": {"type": "integer"},
                    },
                },
            },
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "cell": {"type": "string"},
            "displays": {"type": "integer"},
            "empty_layers": {"type": "array", "items": {"type": "string"}},
        },
    },
    mutates=False,
    tags=["view", "read"],
)
def view_show_25d(params, ctx):
    _view, cv, layout = _active_layout()
    displays = params.get("displays") or []

    cell_name = params.get("cell")
    if cell_name:
        cell = layout.cell(str(cell_name))
        if cell is None:
            raise RpcError(ErrorCode.NOT_FOUND,
                           f"cell {cell_name!r} not in the active layout",
                           hint="check cell.list, or omit 'cell' for the "
                                "current cell")
    else:
        cell = cv.cell
        if cell is None:
            raise RpcError(ErrorCode.NO_VIEW, "no current cell",
                           hint="pass 'cell' explicitly")

    # validate BEFORE touching the window (validate-before-mutate)
    parsed = []
    for i, d in enumerate(displays):
        if not isinstance(d, dict):
            raise RpcError(ErrorCode.BAD_PARAMS,
                           f"displays[{i}] must be an object")
        try:
            lay_s, dt_s = str(d["layer"]).split("/")
            li_layer, li_dt = int(lay_s), int(dt_s)
        except (KeyError, ValueError):
            raise RpcError(ErrorCode.BAD_PARAMS,
                           f"displays[{i}].layer must be 'L/D', got "
                           f"{d.get('layer')!r}") from None
        try:
            z0 = float(d["zstart_um"]); z1 = float(d["zstop_um"])
        except (KeyError, TypeError, ValueError):
            raise RpcError(ErrorCode.BAD_PARAMS,
                           f"displays[{i}] needs numeric zstart_um/zstop_um "
                           f"(microns)") from None
        if not z1 > z0:
            raise RpcError(ErrorCode.BAD_PARAMS,
                           f"displays[{i}]: zstop_um ({z1}) must be > "
                           f"zstart_um ({z0})")
        color = d.get("color")
        frame = d.get("frame_color", color)
        fill = d.get("fill_color", color)
        parsed.append({
            "li": pya.LayerInfo(li_layer, li_dt),
            "layer_s": f"{li_layer}/{li_dt}",
            "z0": z0, "z1": z1,
            "name": str(d.get("name") or f"{li_layer}/{li_dt}"),
            "frame": None if frame is None else int(frame),
            "fill": None if fill is None else int(fill),
        })

    view = _find_d25_view()
    dbu = layout.dbu
    empty = []
    view.clear()
    view.begin(str(params.get("generator") or "klink"))
    try:
        for p in parsed:
            region = pya.Region(
                cell.begin_shapes_rec(layout.layer(p["li"])))
            if region.is_empty():
                empty.append(p["layer_s"])
            view.open_display(p["frame"], p["fill"], p["li"], p["name"])
            view.entry(region, dbu, p["z0"], p["z1"])
            view.close_display()
    finally:
        view.finish()
    view.show()
    view.raise_()

    return {"ok": True, "cell": cell.name, "displays": len(parsed),
            "empty_layers": empty}

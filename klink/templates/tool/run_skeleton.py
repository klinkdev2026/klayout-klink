"""__TITLE__ -- a klink tool (``run(ctx, params)`` form).

Scaffolded by ``klink tool new``. As written it counts the selected shapes per layer and
changes nothing; replace the body of ``run`` with your own logic.

THE CONTRACT (docs/TOOL_AUTHORING.md has the full text)

* klink calls ``run(ctx, params)`` once per click / ``tool run``, inside ONE undo
  transaction: if ``run`` raises, every edit it made is rolled back. Do NOT open your own
  ``view.transaction()``.
* ``ctx`` fields:
    ctx.pya        the pya module (also available as the global ``pya``)
    ctx.view       the current LayoutView
    ctx.cellview   its active CellView
    ctx.layout     that cellview's Layout
    ctx.cell       the cell shown in the view (insert your result here)
    ctx.dbu        layout.dbu (um per database unit)
    ctx.selection  selected SHAPES as ObjectInstPath (instances excluded)
    ctx.selected   EVERY selected object, instances included
    ctx.tool_dir   Path of the installed tool folder (data files next to run.py)
    ctx.log        a logging.Logger writing to klink_server.log
    ctx.layers(scope="region", skip_selection_layers=None)
                   layers the tool sees: "region" (under the selection), "selection",
                   "layout"; each {"layer": "1/0", "index", "name", "shapes", "capped"}
* ``params`` arrive already validated and coerced from tool.json: int / float / bool /
  str / enum choice; a layer param is a "L/D" string such as "1/0" (or "same" when the
  param has "allow_same": true). Declare every param in tool.json; klink asks for missing
  ones in ONE KLayout form, so never pop your own dialogs (no InputDialog / MessageBox).
* Geometry is in database units (integers): ``int(round(um / ctx.dbu))``.
* Layer index for "L/D": ``ln, dt = (int(v) for v in params["layer"].split("/"))`` then
  ``li = ctx.layout.layer(pya.LayerInfo(ln, dt))`` (creates the layer when missing).
  Insert with ``ctx.cell.shapes(li).insert(pya.Box(x1, y1, x2, y2))`` (or a Polygon /
  Path / Region).
* A selected shape: ``obj.shape`` (``obj.layer`` is its layer index, ``obj.trans()`` maps
  it into the current cell). Check the type first: ``shape.is_box()`` -> ``shape.box``;
  ``shape.is_polygon()`` / ``is_path()`` / ``is_simple_polygon()`` -> ``shape.polygon``;
  ``shape.is_text()`` -> ``shape.text``.
* Booleans / sizing: ``pya.Region(shape.polygon.transformed(obj.trans()))`` then
  ``a & b``, ``a - b``, ``a + b``, ``a ^ b``, ``r.sized(d)``, ``r.merged()``.
* Validate EVERYTHING before the first edit. For a user error raise
  ``ValueError("<what is wrong>; <what to do>")`` -- that one sentence is what the user
  sees, and nothing was changed.
* ``print`` ONE summary line (the last printed line goes to KLayout's status bar; all
  output is kept in the run journal: ``klink tool log <id>``).
* Return a small JSON-serialisable dict (counts, layers used); it is the run's result.
"""

TITLE = __TITLE_REPR__


def run(ctx, params):
    # 1. read + validate (no edits yet)
    counts = {}
    for obj in ctx.selection:
        info = ctx.layout.get_info(obj.layer)
        key = f"{info.layer}/{info.datatype}"
        counts[key] = counts.get(key, 0) + 1

    # 2. edit (none in this skeleton). Example:
    # pya = ctx.pya
    # li = ctx.layout.layer(pya.LayerInfo(100, 0))
    # d = int(round(1.0 / ctx.dbu))
    # ctx.cell.shapes(li).insert(pya.Box(0, 0, d, d))

    # 3. one summary line + a small result dict
    total = sum(counts.values())
    if total:
        per_layer = ", ".join(f"{k}: {n}" for k, n in sorted(counts.items()))
        print(f"{TITLE}: {total} selected shape(s) ({per_layer})")
    else:
        print(f"{TITLE}: nothing selected")
    return {"shapes": total, "per_layer": counts}

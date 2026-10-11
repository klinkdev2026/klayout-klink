"""Boolean on selection: or / and / not / xor of the selected shapes into one result.

Runs inside KLayout (klink tool runtime, one undo step). Texts are skipped. ``not`` is the
first selected shape minus the union of the rest; ``xor`` folds in selection order (the
order KLayout reports the selection in). Source shapes are deleted unless ``keep``; only
shapes that live directly in the current cell can be deleted.
"""


def run(ctx, params):
    pya = ctx.pya
    cell = ctx.cell
    regions, sources = [], []
    for obj in ctx.selection:
        shape = obj.shape
        if not (shape.is_box() or shape.is_polygon() or shape.is_simple_polygon() or shape.is_path()):
            continue  # texts, edges, points
        regions.append(pya.Region(shape.polygon.transformed(obj.trans())))
        sources.append(obj)
    if not regions:
        raise ValueError("the selection has no boxes, polygons or paths (texts are skipped); "
                         "select at least two of them")
    op = params["op"]
    if op != "or" and len(regions) < 2:
        raise ValueError(f"operation {op!r} needs at least 2 polygon shapes; {len(regions)} selected")

    if op == "or":
        result = pya.Region()
        for r in regions:
            result += r
    elif op == "and":
        result = regions[0]
        for r in regions[1:]:
            result = result & r
    elif op == "not":
        rest = pya.Region()
        for r in regions[1:]:
            rest += r
        result = regions[0] - rest
    else:  # xor
        result = regions[0]
        for r in regions[1:]:
            result = result ^ r
    result = result.merged()

    # Validate everything BEFORE the first edit.
    if not params["keep"]:
        nested = [o for o in sources if o.cell_index() != cell.cell_index()]
        if nested:
            raise ValueError(f"{len(nested)} selected shape(s) sit inside instances; "
                             "run with keep=true")
    if result.is_empty():
        raise ValueError(f"the {op!r} result is empty (the shapes do not overlap that way); "
                         "nothing was changed")

    layout = ctx.layout
    if params["target"] == "same":
        target = sources[0].layer
    else:
        ln, dt = (int(v) for v in params["target"].split("/"))
        target = layout.layer(ln, dt)
    info = layout.get_info(target)

    removed = 0
    if not params["keep"]:
        ctx.view.clear_object_selection()  # the selection refers to shapes about to vanish
        for obj in sources:
            cell.shapes(obj.layer).erase(obj.shape)
            removed += 1
    cell.shapes(target).insert(result)
    return {"op": op, "inputs": len(sources), "result_polygons": result.count(),
            "target_layer": f"{info.layer}/{info.datatype}", "removed": removed}

"""Boolean of two layers inside the selection: (A & clip) op (B & clip) -> output layer.

Runs inside KLayout (klink tool runtime, one undo step). The selection only DEFINES the
clip region; it is never edited:

* a selected shape contributes its polygon (boxes, polygons, paths; anything else its
  bounding box), transformed into the current cell;
* a selected instance contributes the geometry of layer A and layer B inside it
  (hierarchy included), transformed into the current cell (every member of an array).

Layers A and B are read from the current cell with its whole hierarchy
(``begin_shapes_rec``), each clipped to the region, then combined with ``op``
(``not`` = A minus B). The result is inserted into the output layer of the current cell
(created when it does not exist). ``keep_output = false`` first removes what the current
cell already has on the output layer inside the region (texts are left alone).
"""


def _layer_index(layout, spec, role):
    ln, dt = (int(v) for v in spec.split("/"))
    li = layout.find_layer(ln, dt)
    if li is None:
        raise ValueError(f"{role} {spec} does not exist in this layout; pick an existing layer "
                         "(nothing was changed)")
    return li


def _clip_region(pya, selected, layers):
    clip = pya.Region()
    for obj in selected:
        if obj.is_cell_inst():
            inst = obj.inst()
            # obj.trans() maps the instance's cell into the current cell for the first array
            # member; strip that member to get the parent path, then place every member.
            parent = obj.trans() * inst.cplx_trans.inverted()
            for li in layers:
                inner = pya.Region(inst.cell.begin_shapes_rec(li))
                if inner.is_empty():
                    continue
                for member in inst.cell_inst.each_cplx_trans():
                    clip.insert(inner.transformed(parent * member))
        else:
            shape, trans = obj.shape, obj.trans()
            if shape.is_polygon() or shape.is_simple_polygon():
                clip.insert(shape.polygon.transformed(trans))
            elif shape.is_box():
                clip.insert(pya.Polygon(shape.box).transformed(trans))
            elif shape.is_path():
                clip.insert(shape.path.polygon().transformed(trans))
            else:
                clip.insert(pya.Polygon(shape.bbox()).transformed(trans))
    return clip.merged()


def run(ctx, params):
    pya, layout, cell = ctx.pya, ctx.layout, ctx.cell
    # Validate everything BEFORE the first edit.
    la = _layer_index(layout, params["layer_a"], "layer_a")
    lb = _layer_index(layout, params["layer_b"], "layer_b")
    clip = _clip_region(pya, ctx.selected, (la, lb))
    if clip.is_empty() or clip.area() == 0:  # e.g. only texts selected: zero-area outlines
        raise ValueError("the selection defines no region: select shapes with an area, or "
                         f"instances that contain {params['layer_a']} or {params['layer_b']} "
                         "geometry (nothing was changed)")

    a = pya.Region(cell.begin_shapes_rec(la)) & clip
    b = pya.Region(cell.begin_shapes_rec(lb)) & clip
    op = params["op"]
    result = {"and": a & b, "or": a | b, "not": a - b, "xor": a ^ b}[op].merged()

    ln, dt = (int(v) for v in params["output"].split("/"))
    out_li = layout.layer(ln, dt)  # may create the layer (inside the undo step)
    out_shapes = cell.shapes(out_li)
    if not params["keep_output"]:
        hit = [s for s in out_shapes.each_touching(clip.bbox())
               if s.is_box() or s.is_polygon() or s.is_simple_polygon() or s.is_path()]
        old = pya.Region()
        for s in hit:
            old.insert(s.polygon)
        if not (old & clip).is_empty():  # rewrite only shapes that really reach into the region
            for s in hit:
                out_shapes.erase(s)
            out_shapes.insert(old - clip)
    out_shapes.insert(result)
    return {"op": op, "clip_objects": len(ctx.selected), "result_polygons": result.count(),
            "result_area_um2": round(result.area() * layout.dbu * layout.dbu, 6),
            "output": f"{ln}/{dt}"}

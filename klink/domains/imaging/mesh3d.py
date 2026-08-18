"""GLB (glTF-binary) builders — the 3D exit of the imaging family.

Two modes, one VisualStack declaration:

- ``fast``: extrude each declared layer's plan polygons between its
  ``z0_um``/``z1_um`` — quick, box-profile geometry (what a stackup
  extrusion can honestly claim).
- ``process``: sweep the xsection ENGINE across N parallel cut lines
  and stack each section's material polygons as thin slabs
  (tomography).  Curvature (LOCOS, conformal layers, CMP) comes from
  the engine, not hand-modeling.  Honesty limits, recorded in the
  sidecar: geometry between cuts is a slab (stair-step of the slice
  pitch) and profiles are exact only perpendicular to the cut
  direction.  Engine materials are styled by matching their recipe
  variable name to ``VisualStack.recipe_symbol``; unmatched materials
  render neutral grey and are listed as ``unstyled``.

Optional deps: trimesh + shapely (+ klayout_pyxs for ``process``);
missing ones raise instructive errors naming the pip command.
Determinism: identical inputs produce byte-identical GLB files
(golden-test contract, verified by test).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .visual_stack import VisualStack, VisualLayer
from ._util import kdb as _kdb

#: Placeholder only — the real fallback colour is the caller's
#: (viewer_style.material.undeclared_color). Nothing here picks a
#: colour on its own.
_UNSTYLED = object()


class Mesh3DError(ValueError):
    """Bad input; the message says what to fix."""


def _deps():
    try:
        import trimesh
        from shapely.geometry import Polygon as ShPoly
    except ImportError as exc:
        raise Mesh3DError(
            f"3D building needs trimesh, shapely and a triangulation "
            f"engine in THIS interpreter ({exc.name} is missing). "
            f"Install with: pip install trimesh shapely mapbox-earcut"
        ) from exc
    # trimesh's polygon extrusion needs a triangulation engine that
    # trimesh does NOT depend on itself — without this probe the failure
    # surfaces as trimesh's own 'No available triangulation engine!'
    # (a shipped 0.3.1 wheel walked into exactly that)
    try:
        import mapbox_earcut  # noqa: F401
    except ImportError:
        try:
            import manifold3d  # noqa: F401
        except ImportError as exc:
            raise Mesh3DError(
                "trimesh needs a triangulation engine for polygon "
                "extrusion and none is installed. Install with: "
                "pip install mapbox-earcut") from exc
    return trimesh, ShPoly


def _hex_rgba(color: str, alpha: float) -> List[float]:
    c = color.lstrip("#")
    if len(c) != 6:
        raise Mesh3DError(f"color must be #RRGGBB, got {color!r}")
    return [int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4)] + [alpha]


def _material(trimesh, name: str, color: str, alpha: float,
              metallic: float, roughness: float):
    mat = trimesh.visual.material.PBRMaterial(
        name=name,
        baseColorFactor=_hex_rgba(color, alpha),
        metallicFactor=float(metallic),
        roughnessFactor=float(roughness))
    if alpha < 1.0:
        mat.alphaMode = "BLEND"
    return mat


def _add_geometry(scene, trimesh, name, meshes, color, alpha,
                  metallic, roughness):
    mesh = trimesh.util.concatenate(meshes)
    mesh.visual = trimesh.visual.TextureVisuals(
        material=_material(trimesh, name, color, alpha, metallic,
                           roughness))
    scene.add_geometry(mesh, geom_name=name, node_name=name)
    return len(mesh.faces)


def _layer_shapely(layout, top, layer: str, ShPoly):
    kdb = _kdb(Mesh3DError)
    l, d = (int(v) for v in layer.split("/"))
    li = layout.find_layer(kdb.LayerInfo(l, d))
    if li is None:
        return []
    region = kdb.Region(top.begin_shapes_rec(li))
    region.merge()
    dbu = layout.dbu
    out = []
    for poly in region.each():
        hull = [(p.x * dbu, p.y * dbu) for p in poly.each_point_hull()]
        holes = [[(p.x * dbu, p.y * dbu)
                  for p in poly.each_point_hole(hi)]
                 for hi in range(poly.holes())]
        sp = ShPoly(hull, holes)
        if sp.area > 0:
            out.append(sp)
    return out


def build_glb_fast(
    gds_path: str,
    stack: VisualStack,
    out_glb: str,
    style,
    *,
    cell: Optional[str] = None,
) -> Dict[str, Any]:
    """Extrude every VisualStack layer between its z0_um/z1_um."""
    trimesh, ShPoly = _deps()
    kdb = _kdb(Mesh3DError)
    layout = kdb.Layout()
    layout.read(gds_path)
    from ._util import top_cell_of
    top = top_cell_of(layout, cell, Mesh3DError, gds_path)

    scene = trimesh.Scene()
    report: List[Dict[str, Any]] = []
    tris = 0
    for vl in stack.layers:
        polys = _layer_shapely(layout, top, vl.layer, ShPoly)
        meshes = []
        for sp in polys:
            m = trimesh.creation.extrude_polygon(
                sp, height=vl.z1_um - vl.z0_um)
            m.apply_translation([0, 0, vl.z0_um])
            meshes.append(m)
        if not meshes:
            report.append({"name": vl.name, "layer": vl.layer,
                           "solids": 0, "triangles": 0})
            continue
        n = _add_geometry(scene, trimesh, vl.name, meshes, vl.color,
                          vl.alpha, vl.metallic, style.roughness)
        tris += n
        report.append({"name": vl.name, "layer": vl.layer,
                       "solids": len(meshes), "triangles": n})
    scene.export(out_glb)
    return {"mode": "fast", "materials": report, "triangles": tris,
            "unstyled": []}


def build_glb_process(
    gds_path: str,
    stack: VisualStack,
    recipe_path: str,
    out_glb: str,
    style,
    *,
    cell: Optional[str] = None,
    slices: int = 36,
    fraction: float = 1.0,
    exclude: Sequence[str] = (),
    simplify_um: float = 0.004,
    height_um: float = 2.0,
    depth_um: float = 2.0,
    below_um: float = 2.0,
    extend_um: float = 2.0,
    delta_dbu: int = 10,
) -> Dict[str, Any]:
    """Sweep the xsection engine across the die; stack sections as
    slabs. ``fraction < 1`` stops the sweep mid-die — the exposed face
    is a true cross-section (cutaway)."""
    trimesh, ShPoly = _deps()
    from .xsection_driver import run_cut_polygons
    kdb = _kdb(Mesh3DError)
    if slices < 2:
        raise Mesh3DError("slices must be >= 2")
    if not 0.0 < fraction <= 1.0:
        raise Mesh3DError("fraction must be in (0, 1]")
    with open(recipe_path, encoding="utf-8") as fh:
        recipe_text = fh.read()
    layout = kdb.Layout()
    layout.read(gds_path)
    from ._util import top_cell_of
    top = top_cell_of(layout, cell, Mesh3DError, gds_path)
    bb = top.dbbox()
    y_hi = bb.bottom + (bb.top - bb.bottom) * fraction
    margin = 0.05 * (y_hi - bb.bottom)
    ys = [bb.bottom + margin
          + i * (y_hi - bb.bottom - 2 * margin) / (slices - 1)
          for i in range(slices)]
    pitch = ys[1] - ys[0]

    name_to_layer: Dict[str, str] = {}
    slabs: Dict[str, List[Any]] = {}
    for y in ys:
        polys_by_name = run_cut_polygons(
            layout, top.cell_index(), recipe_text, recipe_path,
            [[bb.left - 1.0, y], [bb.right + 1.0, y]],
            name_to_layer=name_to_layer, exclude=exclude,
            height_um=height_um, depth_um=depth_um, below_um=below_um,
            extend_um=extend_um, delta_dbu=delta_dbu)
        for name, polys in polys_by_name.items():
            for rings in polys:
                sp = ShPoly(rings[0], rings[1:])
                if sp.area <= 0:
                    continue
                sp = sp.simplify(simplify_um)
                m = trimesh.creation.extrude_polygon(sp, height=pitch)
                # section plane (x, z) extruded along the sweep axis y
                m.vertices = m.vertices[:, [0, 2, 1]]
                m.vertices[:, 1] += y - pitch / 2
                m.invert()
                slabs.setdefault(name, []).append(m)

    scene = trimesh.Scene()
    report: List[Dict[str, Any]] = []
    unstyled: List[str] = []
    undeclared_color = style.undeclared_color
    tris = 0
    for name in sorted(slabs):
        vl = stack.by_recipe_symbol(name)
        recipe_style = stack.recipe_style(name)
        if vl is not None:
            color, alpha, metallic = vl.color, vl.alpha, vl.metallic
            display = vl.name
        elif recipe_style is not None:
            color = str(recipe_style.get("color") or undeclared_color)
            alpha = float(recipe_style.get("alpha", 1.0))
            metallic = float(recipe_style.get("metallic", 0.0))
            display = str(recipe_style["name"])
        else:
            unstyled.append(name)
            color, alpha, metallic = undeclared_color, 1.0, 0.0
            display = name
        n = _add_geometry(scene, trimesh, display, slabs[name], color,
                          alpha, metallic, style.roughness)
        tris += n
        report.append({"name": display, "recipe_symbol": name,
                       "solids": len(slabs[name]), "triangles": n})
    scene.export(out_glb)
    return {"mode": "process", "materials": report, "triangles": tris,
            "unstyled": unstyled, "slices": slices, "fraction": fraction,
            "slice_pitch_um": pitch}

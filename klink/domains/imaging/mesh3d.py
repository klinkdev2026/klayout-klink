"""GLB (glTF-binary) builder — the 3D exit of the imaging family.

One path, one VisualStack declaration: extrude each declared layer's
plan polygons between its ``z0_um``/``z1_um``. The model IS the
layout — a circle stays a smooth polygon prism — which is what a 3D
drawing of the masks can honestly claim. Process TRUTH (etch
profiles, bird's beaks, conformal films) belongs to the 2D
cross-section exit (``xsection_run``): the engine computes sections
along a line, and stacking those into a 3D body was tried and
retired — the sweep's stair-step made the model ugly exactly where it
claimed value (one release note tells the story; a real 3D process
simulator is out of klink's honest scope).

Optional deps: trimesh + shapely (+ a triangulation engine); missing
ones raise instructive errors naming the pip command. Determinism:
identical inputs produce byte-identical GLB files (golden-test
contract, verified by test).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .visual_stack import VisualStack
from ._util import DEFAULT_WELD_DBU, kdb as _kdb

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


#: Layout space is Z-up but glTF mandates +Y-up: rotate -90° about X
#: ((x, y, z) -> (x, z, -y)) so spec-compliant viewers (the html page,
#: Blender's importer, ...) show the die lying flat, not on its side.
_GLTF_YUP = [[1.0, 0.0, 0.0, 0.0],
             [0.0, 0.0, 1.0, 0.0],
             [0.0, -1.0, 0.0, 0.0],
             [0.0, 0.0, 0.0, 1.0]]


def _add_geometry(scene, trimesh, name, meshes, color, alpha,
                  metallic, roughness):
    mesh = trimesh.util.concatenate(meshes)
    mesh.apply_transform(_GLTF_YUP)
    mesh.visual = trimesh.visual.TextureVisuals(
        material=_material(trimesh, name, color, alpha, metallic,
                           roughness))
    scene.add_geometry(mesh, geom_name=name, node_name=name)
    return len(mesh.faces)


def _layer_shapely(layout, top, layer: str, ShPoly,
                   weld_dbu: int = DEFAULT_WELD_DBU,
                   warnings: Optional[List[dict]] = None):
    from ._util import layer_region
    region = layer_region(layout, top, layer, Mesh3DError,
                          weld_dbu=weld_dbu, warnings=warnings)
    if region is None:
        return []
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


# --------------------------------------------------------------------- #
# tapered extrusion (sidewall_deg): the top face is the bottom face
# offset inward by thickness*tan(angle), lofted vertex-for-vertex —
# a circle stays a smooth prism, only tilted. NO slicing anywhere.
# --------------------------------------------------------------------- #

def _shift_left(ring, d):
    """Offset a closed ring's vertices by ``d`` to the LEFT of travel
    (miter): every new vertex is the intersection of its two edges,
    each shifted left by ``d``. 1:1 vertex correspondence by
    construction — that is what makes the loft trivially watertight."""
    import math
    n = len(ring)
    out = []
    for i in range(n):
        p0 = ring[i - 1]
        p1 = ring[i]
        p2 = ring[(i + 1) % n]
        ax, ay = p1[0] - p0[0], p1[1] - p0[1]
        bx, by = p2[0] - p1[0], p2[1] - p1[1]
        la = math.hypot(ax, ay) or 1.0
        lb = math.hypot(bx, by) or 1.0
        # left normals of the two edges
        n1 = (-ay / la, ax / la)
        n2 = (-by / lb, bx / lb)
        cross = ax * by - ay * bx
        if abs(cross) < 1e-12 * la * lb:      # collinear: plain shift
            out.append((p1[0] + n1[0] * d, p1[1] + n1[1] * d))
            continue
        # intersect line(p0+n1*d, dir a) with line(p1+n2*d, dir b)
        qx, qy = p0[0] + n1[0] * d, p0[1] + n1[1] * d
        rx, ry = p1[0] + n2[0] * d, p1[1] + n2[1] * d
        t = ((rx - qx) * by - (ry - qy) * bx) / cross
        out.append((qx + ax * t, qy + ay * t))
    return out


def _inset_polygon(ShPoly, sp, d):
    """The polygon's top face under an inward miter offset of ``d``,
    or ``None`` when the shape does not survive it 1:1 (something
    narrower than 2*d collapses, splits, or loses a hole).

    The manual offset keeps vertex correspondence; a GEOS erosion of
    the same polygon is the truth oracle — if the two disagree in
    validity, topology or area, the honest answer is "this shape does
    not taper cleanly", not a guessed mesh."""
    from shapely.geometry.polygon import orient
    sp = orient(sp)                       # exterior CCW, holes CW
    ext = _shift_left(list(sp.exterior.coords)[:-1], d)
    holes = [_shift_left(list(h.coords)[:-1], d)
             for h in sp.interiors]
    top = ShPoly(ext, holes)
    oracle = sp.buffer(-d, join_style=2, mitre_limit=8.0)
    if (not top.is_valid or oracle.is_empty
            or oracle.geom_type != "Polygon"
            or len(oracle.interiors) != len(sp.interiors)
            or abs(top.area - oracle.area)
            > max(0.02 * oracle.area, 1e-9)):
        return None
    return orient(top)


def _loft_mesh(trimesh, ShPoly, sp_bot, sp_top, z0, z1):
    """Solid between two 1:1-corresponding faces (walls + both caps)."""
    import numpy as np
    from shapely.geometry.polygon import orient
    sp_bot = orient(sp_bot)
    sp_top = orient(sp_top)
    rings_b = ([list(sp_bot.exterior.coords)[:-1]]
               + [list(h.coords)[:-1] for h in sp_bot.interiors])
    rings_t = ([list(sp_top.exterior.coords)[:-1]]
               + [list(h.coords)[:-1] for h in sp_top.interiors])
    verts = []
    faces = []
    for rb, rt in zip(rings_b, rings_t):
        base = len(verts)
        n = len(rb)
        verts += [(x, y, z0) for x, y in rb]
        verts += [(x, y, z1) for x, y in rt]
        for i in range(n):
            a, b = base + i, base + (i + 1) % n
            faces.append((a, b, b + n))
            faces.append((a, b + n, a + n))
    wall = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    vb, fb = trimesh.creation.triangulate_polygon(sp_bot)
    bot = trimesh.Trimesh(
        vertices=np.column_stack([vb, np.full(len(vb), z0)]),
        faces=fb[:, ::-1], process=False)          # cap faces down
    vt, ft = trimesh.creation.triangulate_polygon(sp_top)
    topc = trimesh.Trimesh(
        vertices=np.column_stack([vt, np.full(len(vt), z1)]),
        faces=ft, process=False)                   # cap faces up
    mesh = trimesh.util.concatenate([wall, bot, topc])
    mesh.merge_vertices()
    if mesh.volume < 0:
        mesh.invert()
    return mesh


def _taper_warning(vl, d_um: float, count: int) -> dict:
    return {
        "kind": "taper_collapse",
        "layer": vl.layer,
        "note": (
            f"layer {vl.layer} ({vl.name}): {count} polygon(s) do not "
            f"survive the declared sidewall_deg={vl.sidewall_deg:g} — "
            f"anything narrower than 2*thickness*tan(angle) "
            f"(= {2 * d_um:.3f} um here) collapses or splits before "
            f"the top face. Those polygons are drawn with VERTICAL "
            f"walls instead so the layout shape stays visible; to "
            f"model them tapered, lower sidewall_deg or thin the "
            f"layer."),
    }


def _cut_meshes(trimesh, meshes, cutaway_um, z_lo, z_hi):
    """Boolean-keep the region inside ``cutaway_um`` — the model is
    built whole first, then cut, so the cut shows only on the cut
    faces (a real cross-section look, not a sliced model)."""
    try:
        import manifold3d  # noqa: F401
    except ImportError as exc:
        raise Mesh3DError(
            "cutaway needs the boolean engine in THIS interpreter. "
            "Install with: pip install manifold3d") from exc
    x0, y0, x1, y1 = (float(v) for v in cutaway_um)
    if not (x1 > x0 and y1 > y0):
        raise Mesh3DError(
            f"cutaway_um must be [x0, y0, x1, y1] with x1>x0 and "
            f"y1>y0 (the region to KEEP, in um), got {cutaway_um!r}")
    box = trimesh.creation.box(
        bounds=[[x0, y0, z_lo - 1.0], [x1, y1, z_hi + 1.0]])
    out = []
    for m in meshes:
        cut = trimesh.boolean.intersection([m, box], engine="manifold")
        if len(cut.faces):
            out.append(cut)
    return out


def build_glb_fast(
    gds_path: str,
    stack: VisualStack,
    out_glb: str,
    style,
    *,
    cell: Optional[str] = None,
    weld_slits_dbu: int = DEFAULT_WELD_DBU,
    cutaway_um: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """Extrude every VisualStack layer between its z0_um/z1_um.

    A layer with ``sidewall_deg > 0`` lofts to a top face inset by
    thickness*tan(angle) — smooth tilted walls, no slicing. With
    ``cutaway_um=[x0, y0, x1, y1]`` the finished model is boolean-cut
    to that region, so only the cut faces show a section."""
    import math
    trimesh, ShPoly = _deps()
    kdb = _kdb(Mesh3DError)
    layout = kdb.Layout()
    layout.read(gds_path)
    from ._util import top_cell_of
    top = top_cell_of(layout, cell, Mesh3DError, gds_path)

    scene = trimesh.Scene()
    report: List[Dict[str, Any]] = []
    warnings: List[dict] = []
    tris = 0
    z_lo = min(vl.z0_um for vl in stack.layers)
    z_hi = max(vl.z1_um for vl in stack.layers)
    for vl in stack.layers:
        polys = _layer_shapely(layout, top, vl.layer, ShPoly,
                               weld_dbu=weld_slits_dbu,
                               warnings=warnings)
        thickness = vl.z1_um - vl.z0_um
        d_um = thickness * math.tan(math.radians(vl.sidewall_deg))
        meshes = []
        collapsed = 0
        for sp in polys:
            sp_top = (_inset_polygon(ShPoly, sp, d_um)
                      if d_um > 0 else None)
            if d_um > 0 and sp_top is not None:
                m = _loft_mesh(trimesh, ShPoly, sp, sp_top,
                               vl.z0_um, vl.z1_um)
            else:
                if d_um > 0:
                    collapsed += 1
                m = trimesh.creation.extrude_polygon(
                    sp, height=thickness)
                m.apply_translation([0, 0, vl.z0_um])
            meshes.append(m)
        if collapsed:
            warnings.append(_taper_warning(vl, d_um, collapsed))
        if cutaway_um is not None and meshes:
            meshes = _cut_meshes(trimesh, meshes, cutaway_um,
                                 z_lo, z_hi)
        if not meshes:
            report.append({"name": vl.name, "layer": vl.layer,
                           "solids": 0, "triangles": 0})
            continue
        n = _add_geometry(scene, trimesh, vl.name, meshes, vl.color,
                          vl.alpha, vl.metallic, style.roughness)
        tris += n
        report.append({"name": vl.name, "layer": vl.layer,
                       "solids": len(meshes), "triangles": n})
    if tris == 0:
        bb = top.dbbox()
        raise Mesh3DError(
            f"nothing to export: no stack layer produced geometry"
            + (f" inside cutaway_um={list(cutaway_um)!r} — the cell "
               f"spans [{bb.left:.3f}, {bb.bottom:.3f}, {bb.right:.3f},"
               f" {bb.top:.3f}] um, move the keep-region there"
               if cutaway_um is not None else
               f" — check the stack's 'L/D' layers against the "
               f"layout (cell bbox [{bb.left:.3f}, {bb.bottom:.3f}, "
               f"{bb.right:.3f}, {bb.top:.3f}] um)"))
    scene.export(out_glb)
    return {"mode": "fast", "materials": report, "triangles": tris,
            "unstyled": [], "warnings": warnings,
            "cutaway_um": (list(map(float, cutaway_um))
                           if cutaway_um is not None else None)}

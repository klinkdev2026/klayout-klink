"""Blender exit — paper-grade renders via headless ``bpy``.

Two scene builders, one VisualStack declaration:

- ``render_die_glb``: import a GLB (from :mod:`.mesh3d`), stand it flat,
  polish materials, light it, render on a TRANSPARENT film with a
  shadow-catcher ground (drop the PNG onto any paper/slide background)
  and save a ``.blend`` so a human can open desktop Blender and adjust
  everything by hand.
- ``render_device_figure``: GDS-driven single-device figure — solid
  layers extrude as exact layout polygons; ``kind="lattice"`` layers
  render as the material's ATOMIC STRUCTURE (motif library:
  graphene/MoS2 — the visual language 2D-material papers use);
  ``dielectric`` layers go translucent; explicit ``slabs`` add
  substrate/oxide carriers (example-owned facts, never guessed).

bpy is an OPTIONAL dependency (``pip install bpy`` — Blender as a
Python module, GPL-3; import-only at arm's length, never vendored) and
is a heavyweight global-state module: the MCP tool therefore executes
this module in a SUBPROCESS (``python -m klink.domains.imaging.
blender_scene payload.json``); in-process calls are for scripts/tests.
Renders are NOT byte-deterministic (Cycles threading/denoising) — file
hashes in sidecars are provenance, not goldens.
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Dict, Mapping, Optional, Sequence

from .visual_stack import VisualStack


class BlenderSceneError(ValueError):
    """Bad input; the message says what to fix."""


def _bpy():
    try:
        import bpy
    except ImportError as exc:
        raise BlenderSceneError(
            "the Blender exit needs bpy in THIS interpreter (Blender as "
            "a Python module, ~300MB). Install with: pip install bpy"
        ) from exc
    return bpy


def _hex4(color: str, alpha: float = 1.0):
    c = color.lstrip("#")
    return (int(c[0:2], 16) / 255.0, int(c[2:4], 16) / 255.0,
            int(c[4:6], 16) / 255.0, alpha)


def _new_scene(bpy):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return bpy.context.scene.collection


def _material(bpy, name, color4, metallic=0.0, rough=0.5):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = color4
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = rough
    if color4[3] < 1.0:
        bsdf.inputs["Alpha"].default_value = color4[3]
        mat.surface_render_method = "BLENDED"
    return mat


def _prism(bpy, col, name, rings_um, z0, z1, mat):
    """Extrude a layout polygon into a solid.

    ``rings_um`` is [hull, hole1, hole2, ...] — holes are REAL: a ring
    gate or guard ring must not render as a filled disc. Side walls are
    built per ring; caps tessellate all rings together."""
    from mathutils import Vector
    from mathutils.geometry import tessellate_polygon

    rings = [list(r) for r in rings_um if len(r) >= 3]
    if not rings:
        raise BlenderSceneError(f"{name}: polygon has no valid rings")
    flat = [p for r in rings for p in r]
    N = len(flat)
    verts = [(x, y, z0) for x, y in flat] \
        + [(x, y, z1) for x, y in flat]
    faces = []
    off = 0
    for r in rings:
        n = len(r)
        for i in range(n):
            a = off + i
            b = off + (i + 1) % n
            faces.append((a, b, b + N, a + N))
        off += n
    caps = tessellate_polygon(
        [[Vector((x, y, 0)) for x, y in r] for r in rings])
    faces += [tuple(reversed(t)) for t in caps]
    faces += [tuple(i + N for i in t) for t in caps]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.materials.append(mat)
    obj = bpy.data.objects.new(name, mesh)
    col.objects.link(obj)
    return obj


def _stage(bpy, col, center, size, *, sun_energy=6.0,
           ground_z=None, samples=96, transparent=True,
           resolution=(1920, 1080)):
    """Camera-agnostic staging: lights, world, film, shadow catcher."""
    from mathutils import Vector

    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = sun_energy
    sun.rotation_euler = (math.radians(52), math.radians(-14),
                          math.radians(130))
    col.objects.link(sun)
    area = bpy.data.objects.new("area",
                                bpy.data.lights.new("area", "AREA"))
    area.data.energy = 1400
    area.data.size = size
    area.location = center + Vector((-0.4 * size, 0.55 * size,
                                     0.9 * size))
    area.rotation_euler = (math.radians(-33), math.radians(18), 0)
    col.objects.link(area)

    if ground_z is not None:
        import bmesh
        mesh = bpy.data.meshes.new("ground")
        bm = bmesh.new()
        L = size * 8
        p = [bm.verts.new(v) for v in
             [(-L + center.x, -L + center.y, ground_z),
              (L + center.x, -L + center.y, ground_z),
              (L + center.x, L + center.y, ground_z),
              (-L + center.x, L + center.y, ground_z)]]
        bm.faces.new(p)
        bm.to_mesh(mesh); bm.free()
        ground = bpy.data.objects.new("ground", mesh)
        ground.data.materials.append(_material(
            bpy, "backdrop", (0.18, 0.19, 0.21, 1.0), rough=0.9))
        col.objects.link(ground)
        ground.is_shadow_catcher = True

    world = bpy.data.worlds.new("world")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = \
        (0.04, 0.045, 0.055, 1.0)
    bpy.context.scene.world = world

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.view_settings.view_transform = "Filmic"
    scene.view_settings.look = "Medium High Contrast"
    scene.cycles.samples = int(samples)
    scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y = resolution
    scene.render.film_transparent = bool(transparent)
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"


def _camera(bpy, col, center, size, preset="default", *,
            mins=None, maxs=None, margin=1.06):
    """Place the camera along the preset's direction, at the distance
    that actually FITS the content.

    The distance must come from the bounding box PROJECTED into the
    camera frame, not from the box diagonal: a die is wide and paper
    thin, so its diagonal ~= its width and a diagonal-scaled distance
    puts the camera inside the slab. With ``mins``/``maxs`` given, the
    eight corners are solved exactly against the lens' horizontal and
    vertical field of view."""
    from mathutils import Vector

    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = 58
    cam = bpy.data.objects.new("cam", cam_data)
    if preset == "face":          # look at the +Y end (cutaway face)
        d = Vector((0.30, 0.85, 0.42)).normalized()
    elif preset == "top":
        d = Vector((0.0, -0.15, 0.9)).normalized()
    else:
        d = Vector((0.55, -0.75, 0.45)).normalized()

    dist = size * 1.03            # legacy fallback (no bbox given)
    if mins is not None and maxs is not None:
        scene = bpy.context.scene
        aspect = ((scene.render.resolution_x or 1)
                  / (scene.render.resolution_y or 1))
        sensor = 36.0
        t_h = (sensor / 2) / cam_data.lens
        t_v = (sensor / max(aspect, 1e-6) / 2) / cam_data.lens
        fwd = -d                            # camera looks along -d
        up_ref = Vector((0.0, 0.0, 1.0))
        right = fwd.cross(up_ref)
        if right.length < 1e-9:             # looking straight down
            right = Vector((1.0, 0.0, 0.0))
        right.normalize()
        up = right.cross(fwd).normalized()
        need = 0.0
        for cx in (mins.x, maxs.x):
            for cy in (mins.y, maxs.y):
                for cz in (mins.z, maxs.z):
                    r = Vector((cx, cy, cz)) - center
                    z = r.dot(fwd)          # + = beyond the center
                    need = max(need,
                               abs(r.dot(right)) / t_h - z,
                               abs(r.dot(up)) / t_v - z)
        dist = max(need * margin, 1e-6)
    cam.location = center + d * dist
    cam.rotation_euler = (center - cam.location).to_track_quat(
        "-Z", "Y").to_euler()
    col.objects.link(cam)
    bpy.context.scene.camera = cam


def _finish(bpy, out_png, out_blend):
    scene = bpy.context.scene
    scene.render.filepath = os.path.abspath(out_png)
    bpy.ops.render.render(write_still=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(out_blend))


def render_die_glb(
    glb_path: str,
    out_png: str,
    out_blend: str,
    *,
    camera: str = "default",
    samples: int = 96,
    transparent: bool = True,
    resolution: Sequence[int] = (1920, 1080),
    z_scale: float = 1.0,
) -> Dict[str, Any]:
    """GLB (from mesh3d) -> paper-grade PNG + hand-editable .blend.

    ``z_scale`` stretches the vertical axis before framing. A real die is
    microns wide and nanometres thick, so at 1:1 the stack renders as a
    line; the figure convention is to exaggerate z. The factor used is
    returned as ``z_scale`` — state it in the caption, it is no longer a
    metrically true picture."""
    bpy = _bpy()
    from mathutils import Matrix, Vector

    col = _new_scene(bpy)
    bpy.ops.import_scene.gltf(filepath=os.path.abspath(glb_path))
    # trimesh exports Z-up data into Y-up glTF; stand the die back flat
    rot = Matrix.Rotation(math.radians(-90), 4, "X")
    if z_scale <= 0:
        raise BlenderSceneError(
            f"z_scale must be > 0, got {z_scale!r}")
    xform = Matrix.Scale(z_scale, 4, (0.0, 0.0, 1.0)) @ rot
    n_mesh = 0
    for obj in bpy.data.objects:
        if obj.type == "MESH":
            obj.matrix_world = xform @ obj.matrix_world
            n_mesh += 1
    if not n_mesh:
        raise BlenderSceneError(f"no meshes imported from {glb_path}")
    mins = Vector((1e18, 1e18, 1e18)); maxs = -mins
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        for corner in obj.bound_box:
            w = obj.matrix_world @ Vector(corner)
            mins = Vector(map(min, mins, w))
            maxs = Vector(map(max, maxs, w))
    center = (mins + maxs) / 2
    size = (maxs - mins).length
    # slight sheen on light materials (metals in our stacks are light)
    for mat in bpy.data.materials:
        bsdf = (mat.node_tree.nodes.get("Principled BSDF")
                if mat.node_tree else None)
        if not bsdf:
            continue
        base = bsdf.inputs["Base Color"].default_value
        lum = 0.2126 * base[0] + 0.7152 * base[1] + 0.0722 * base[2]
        if lum > 0.55 and bsdf.inputs["Metallic"].default_value == 0:
            bsdf.inputs["Metallic"].default_value = 0.7
            bsdf.inputs["Roughness"].default_value = 0.4
    _stage(bpy, col, center, size, ground_z=mins.z - 0.02,
           samples=samples, transparent=transparent,
           resolution=tuple(resolution))
    _camera(bpy, col, center, size, camera,
            mins=mins, maxs=maxs)
    _finish(bpy, out_png, out_blend)
    return {"kind": "die", "meshes": n_mesh,
            "materials": len(bpy.data.materials),
            "z_scale": float(z_scale),
            "bbox_um": [list(mins), list(maxs)]}


def render_device_figure(
    gds_path: str,
    stack: VisualStack,
    out_png: str,
    out_blend: str,
    *,
    cell: Optional[str] = None,
    slabs: Sequence[Mapping[str, Any]] = (),
    lattice_a_um: float = 0.08,
    margin_um: float = 0.45,
    camera: str = "default",
    samples: int = 80,
    transparent: bool = True,
    resolution: Sequence[int] = (1920, 1200),
) -> Dict[str, Any]:
    """GDS-driven device figure at 1:1 layout coordinates.

    ``slabs``: explicit full-bbox carriers (substrate/oxide), each
    ``{name, z0_um, z1_um, color, alpha?, metallic?}`` — process facts
    the example owns; klink renders exactly what is declared."""
    bpy = _bpy()
    import klayout.db as kdb
    from mathutils import Vector

    ly = kdb.Layout(); ly.read(gds_path)
    from ._util import top_cell_of
    top = top_cell_of(ly, cell, BlenderSceneError, gds_path)
    dbu = ly.dbu

    col = _new_scene(bpy)
    bb = top.dbbox()
    x0, y0 = bb.left - margin_um, bb.bottom - margin_um
    x1, y1 = bb.right + margin_um, bb.top + margin_um

    counts: Dict[str, Any] = {"solids": 0, "atoms": 0, "bonds": 0}
    z_min = 0.0
    z_max = max([float(v.z1_um) for v in stack.layers]
                + [float(s.get("z1_um", 0.0)) for s in slabs] + [0.0])
    for i, s in enumerate(slabs):
        missing = [k for k in ("name", "z0_um", "z1_um", "color")
                   if k not in s]
        if missing:
            raise BlenderSceneError(
                f"slabs[{i}] is missing {missing} — slabs are explicit "
                f"process facts (name/z0_um/z1_um/color[/alpha]"
                f"[/metallic])")
        mat = _material(bpy, str(s["name"]),
                        _hex4(str(s["color"]),
                              float(s.get("alpha", 1.0))),
                        metallic=float(s.get("metallic", 0.0)))
        _prism(bpy, col, str(s["name"]),
               [[(x0, y0), (x1, y0), (x1, y1), (x0, y1)]],
               float(s["z0_um"]), float(s["z1_um"]), mat)
        counts["solids"] += 1
        z_min = min(z_min, float(s["z0_um"]))

    def layer_polys(vl):
        """-> list of ring lists [hull, hole1, ...] (holes preserved)."""
        l, d = (int(v) for v in vl.layer.split("/"))
        li = ly.find_layer(kdb.LayerInfo(l, d))
        if li is None:
            return []
        region = kdb.Region(top.begin_shapes_rec(li))
        region.merge()
        out = []
        for poly in region.each():
            rings = [[(p.x * dbu, p.y * dbu)
                      for p in poly.each_point_hull()]]
            for hi in range(poly.holes()):
                rings.append([(p.x * dbu, p.y * dbu)
                              for p in poly.each_point_hole(hi)])
            out.append(rings)
        return out

    for vl in stack.layers:
        polys = layer_polys(vl)
        if not polys:
            continue
        if vl.kind == "lattice":
            from shapely.geometry import Polygon as ShPoly

            from .motifs import MOTIFS
            if vl.motif not in MOTIFS:
                raise BlenderSceneError(
                    f"layer {vl.name}: motif {vl.motif!r} not in "
                    f"{sorted(MOTIFS)}")
            z_mid = (vl.z0_um + vl.z1_um) / 2
            for rings in polys:
                # holes matter for lattices too: no atoms inside voids
                sp = ShPoly(rings[0], rings[1:])
                motif = MOTIFS[vl.motif](sp, a_um=lattice_a_um,
                                         z_um=z_mid)
                _add_lattice(bpy, col, motif, counts)
        else:
            alpha = vl.alpha if vl.kind != "dielectric" \
                else min(vl.alpha, 0.5)
            mat = _material(bpy, vl.name, _hex4(vl.color, alpha),
                            metallic=vl.metallic)
            for k, rings in enumerate(polys):
                _prism(bpy, col, f"{vl.name}_{k}", rings, vl.z0_um,
                       vl.z1_um, mat)
                counts["solids"] += 1

    center = Vector(((x0 + x1) / 2, (y0 + y1) / 2, 0.05))
    size = max(x1 - x0, y1 - y0)
    _stage(bpy, col, center, size, ground_z=z_min - 0.02,
           samples=samples, transparent=transparent,
           resolution=tuple(resolution))
    _camera(bpy, col, center, size * 1.15, camera,
            mins=Vector((x0, y0, z_min)),
            maxs=Vector((x1, y1, z_max)))
    _finish(bpy, out_png, out_blend)
    counts["kind"] = "figure"
    return counts


def _add_lattice(bpy, col, motif, counts):
    import bmesh
    from mathutils import Vector

    for sp in motif["species"]:
        mesh = bpy.data.meshes.new("sp_" + sp["name"])
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=8,
                                  radius=sp["radius_um"])
        bm.to_mesh(mesh); bm.free()
        mesh.materials.append(_material(
            bpy, sp["name"], tuple(sp["color"]), metallic=0.1,
            rough=0.35))
        for i, p in enumerate(sp["positions"]):
            o = bpy.data.objects.new(f'{sp["name"]}_{i}', mesh)
            o.location = Vector(p)
            col.objects.link(o)
            counts["atoms"] += 1
    bond_mesh = bpy.data.meshes.new("bond")
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=False, segments=8,
                          radius1=motif["bond_radius_um"],
                          radius2=motif["bond_radius_um"], depth=1.0)
    bm.to_mesh(bond_mesh); bm.free()
    bond_mesh.materials.append(_material(
        bpy, "bond", (0.55, 0.58, 0.62, 1.0), rough=0.5))
    for i, (p1, p2) in enumerate(motif["bonds"]):
        v1, v2 = Vector(p1), Vector(p2)
        d = v2 - v1
        o = bpy.data.objects.new(f"bond_{i}", bond_mesh)
        o.location = (v1 + v2) / 2
        o.scale = (1, 1, d.length)
        o.rotation_euler = d.to_track_quat("Z", "Y").to_euler()
        col.objects.link(o)
        counts["bonds"] += 1


# --------------------------------------------------------------------- #
# subprocess runner (the MCP tool executes bpy OUT of process: bpy is a
# heavyweight global-state module that must not live in the server)
# --------------------------------------------------------------------- #

def main(argv: Sequence[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m klink.domains.imaging.blender_scene "
              "<payload.json>", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        payload = json.load(fh)
    mode = payload.get("mode")
    if mode == "die":
        report = render_die_glb(
            payload["glb"], payload["out_png"], payload["out_blend"],
            camera=payload.get("camera", "default"),
            samples=int(payload.get("samples", 96)),
            transparent=bool(payload.get("transparent", True)),
            resolution=payload.get("resolution", (1920, 1080)))
    elif mode == "figure":
        stack = VisualStack.from_dict(payload["stack"])
        report = render_device_figure(
            payload["gds"], stack, payload["out_png"],
            payload["out_blend"], cell=payload.get("cell"),
            slabs=payload.get("slabs", ()),
            lattice_a_um=float(payload.get("lattice_a_um", 0.08)),
            camera=payload.get("camera", "default"),
            samples=int(payload.get("samples", 80)),
            transparent=bool(payload.get("transparent", True)),
            resolution=payload.get("resolution", (1920, 1200)))
    else:
        print(f"unknown mode {mode!r}", file=sys.stderr)
        return 2
    with open(payload["out_report"], "w", encoding="utf-8") as fh:
        json.dump(report, fh, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

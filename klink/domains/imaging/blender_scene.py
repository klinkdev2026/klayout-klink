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

from .blender_style import BlenderStyle, BlenderStyleError
from .visual_stack import VisualStack

from ._util import kdb as _kdb

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


def _material(bpy, name, color4, metallic=0.0, rough=None):
    """A material carrying only what the STACK declared. How those
    values become a finished shader is the STYLE's job — see
    :func:`apply_style_shading`, which runs once the scene is built."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = color4
    bsdf.inputs["Metallic"].default_value = metallic
    if rough is not None:
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


def _stage(bpy, col, center, size, style, *,
           ground_z=None, samples=96, transparent=True,
           resolution=(1920, 1080)):
    """Lights, world, film and shadow catcher — every value from the
    caller's style declaration. klink places them; the style says how
    bright, what colour, and which film transform."""
    from mathutils import Vector

    st = style.staging
    key = st["key_light"]
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = float(key["energy"])
    sun.rotation_euler = tuple(math.radians(float(a))
                               for a in key["euler_deg"])
    col.objects.link(sun)

    fill = st["fill_light"]
    area = bpy.data.objects.new("area",
                                bpy.data.lights.new("area", "AREA"))
    area.data.energy = float(fill["energy"])
    area.data.size = size * float(fill["size_fraction"])
    ox, oy, oz = (float(v) for v in fill["offset_fraction"])
    area.location = center + Vector((ox * size, oy * size, oz * size))
    area.rotation_euler = tuple(math.radians(float(a))
                                for a in fill["euler_deg"])
    col.objects.link(area)

    if ground_z is not None:
        import bmesh
        bd = st["backdrop"]
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
            bpy, "backdrop", _hex4(str(bd["color"]), 1.0),
            rough=float(bd["roughness"])))
        col.objects.link(ground)
        ground.is_shadow_catcher = True

    world = bpy.data.worlds.new("world")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value =         _hex4(str(st["world_color"]), 1.0)
    bpy.context.scene.world = world

    film = st["film"]
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.view_settings.view_transform = str(film["view_transform"])
    scene.view_settings.look = str(film["look"])
    scene.cycles.samples = int(samples)
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = int(film["max_bounces"])
    scene.cycles.transparent_max_bounces = int(
        film["transparent_max_bounces"])
    scene.render.resolution_x, scene.render.resolution_y = resolution
    scene.render.film_transparent = bool(transparent)
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"


def _camera(bpy, col, center, size, style, preset="default", *,
            mins=None, maxs=None):
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
    cam_data.lens = float(style.camera["lens_mm"])
    cam = bpy.data.objects.new("cam", cam_data)
    # WHERE the camera stands is style data; klink only solves HOW FAR
    d = Vector(tuple(style.camera_direction(preset))).normalized()
    margin = float(style.camera["margin"])

    dist = size * margin          # fallback when no bbox is given
    if mins is not None and maxs is not None:
        scene = bpy.context.scene
        aspect = ((scene.render.resolution_x or 1)
                  / (scene.render.resolution_y or 1))
        sensor = float(style.camera["sensor_mm"])
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


def classify_material(color4, metallic: float) -> str:
    """Which of the style's three material recipes applies.

    The STACK is the authority: a material is a metal because the stack
    declared metallic > 0.5, and see-through because the stack gave it
    alpha < 1. Nothing is inferred from the colour.

    (A luminance guess used to live in the die path — "metals in our
    stacks are light", so anything bright with metallic 0 was set to
    metallic 0.7. It is backwards: in a plain CMOS stack the pale
    materials are the substrate, the wells, the implants, both oxides
    and the spacer, while the real metals are dark. It metallised 11 of
    15 materials, and because a metallic surface does not read as
    see-through it also defeated the declared alpha, hiding the tungsten
    plugs inside an opaque white ILD.)
    """
    alpha = float(color4[3]) if len(color4) > 3 else 1.0
    if alpha < 1.0:
        return "dielectric"
    if float(metallic) > 0.5:
        return "metal"
    return "matte"


def _set(bsdf, name, value):
    """Set a Principled input if this Blender build has it — the input
    names moved between 4.x releases, and a missing one is not fatal."""
    slot = bsdf.inputs.get(name)
    if slot is None:
        return False
    slot.default_value = value
    return True


def _mottle(mat, bsdf, color4, spec):
    """Noise -> ColourRamp -> Base Color. All five numbers are the
    style's; klink only wires the nodes."""
    nt = mat.node_tree
    tex = nt.nodes.new("ShaderNodeTexNoise")
    for key, field in (("Scale", "noise_scale"),
                       ("Detail", "noise_detail"),
                       ("Roughness", "noise_roughness")):
        if tex.inputs.get(key) is not None:
            tex.inputs[key].default_value = float(spec[field])
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.interpolation = "LINEAR"
    lo, hi = ramp.color_ramp.elements[0], ramp.color_ramp.elements[1]
    lo.position = float(spec["ramp_low"])
    lo.color = tuple(color4[:3]) + (1.0,)
    hi.position = float(spec["ramp_high"])
    hi.color = _hex4(str(spec["dark_color"]), 1.0)
    nt.links.new(tex.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])


def _bevel(mat, bsdf, radius: float, samples: int):
    """Bevel node into Normal: rounded-edge SHADING, no geometry
    change. Layout prisms are perfectly sharp, and a perfectly sharp
    edge catches no highlight."""
    nt = mat.node_tree
    node = nt.nodes.new("ShaderNodeBevel")
    node.samples = int(samples)
    node.inputs["Radius"].default_value = float(radius)
    nt.links.new(node.outputs["Normal"], bsdf.inputs["Normal"])


def apply_style_shading(materials, style: BlenderStyle, *,
                        model_size: float) -> Dict[str, int]:
    """Finish every material according to ``style``.

    Reads what the stack declared off each Principled BSDF, picks the
    style's recipe for that class, and applies it. Returns per-class
    counts so a caller can report what it shaded.
    """
    counts = {"metal": 0, "dielectric": 0, "matte": 0}
    bevel = style.bevel
    mottle = style.mottle
    recipes = {"metal": style.metal, "dielectric": style.dielectric,
               "matte": style.matte}
    for mat in materials:
        tree = getattr(mat, "node_tree", None)
        bsdf = tree.nodes.get("Principled BSDF") if tree else None
        if bsdf is None:
            continue
        base = list(bsdf.inputs["Base Color"].default_value)
        alpha_in = bsdf.inputs.get("Alpha")
        if alpha_in is not None and not alpha_in.is_linked:
            base = base[:3] + [float(alpha_in.default_value)]
        kind = classify_material(
            base, bsdf.inputs["Metallic"].default_value)
        recipe = recipes[kind]
        _set(bsdf, "Roughness", float(recipe["roughness"]))
        for field, slot in (("transmission", "Transmission Weight"),
                            ("ior", "IOR"),
                            ("coat_weight", "Coat Weight"),
                            ("coat_roughness", "Coat Roughness"),
                            ("sheen", "Sheen Weight"),
                            ("specular_level", "Specular IOR Level")):
            if field in recipe:
                _set(bsdf, slot, float(recipe[field]))
        if (kind == "metal" and mottle is not None
                and not bsdf.inputs["Base Color"].is_linked):
            _mottle(mat, bsdf, base, mottle)
        if bevel is not None and not bsdf.inputs["Normal"].is_linked:
            _bevel(mat, bsdf,
                   model_size * float(bevel["radius_fraction"]),
                   int(bevel["samples"]))
        counts[kind] += 1
    return counts


def render_die_glb(
    glb_path: str,
    out_png: str,
    out_blend: str,
    style: BlenderStyle,
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
    shaded = apply_style_shading(bpy.data.materials, style,
                                 model_size=size)
    _stage(bpy, col, center, size, style, ground_z=mins.z - 0.02,
           samples=samples, transparent=transparent,
           resolution=tuple(resolution))
    _camera(bpy, col, center, size, style, camera,
            mins=mins, maxs=maxs)
    _finish(bpy, out_png, out_blend)
    return {"kind": "die", "meshes": n_mesh, "shaded": shaded,
            "materials": len(bpy.data.materials),
            "z_scale": float(z_scale),
            "bbox_um": [list(mins), list(maxs)]}


def render_device_figure(
    gds_path: str,
    stack: VisualStack,
    out_png: str,
    out_blend: str,
    style: BlenderStyle,
    *,
    cell: Optional[str] = None,
    slabs: Sequence[Mapping[str, Any]] = (),
    lattice_a_um: Optional[float] = None,
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
    kdb = _kdb(BlenderSceneError)
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
                a_um = (lattice_a_um if lattice_a_um is not None
                        else style.lattice_a_um())
                motif = MOTIFS[vl.motif](sp, a_um=a_um,
                                         z_um=z_mid)
                _add_lattice(bpy, col, motif, style, counts)
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
    shaded = apply_style_shading(bpy.data.materials, style,
                                 model_size=size)
    _stage(bpy, col, center, size, style, ground_z=z_min - 0.02,
           samples=samples, transparent=transparent,
           resolution=tuple(resolution))
    _camera(bpy, col, center, size * 1.15, style, camera,
            mins=Vector((x0, y0, z_min)),
            maxs=Vector((x1, y1, z_max)))
    _finish(bpy, out_png, out_blend)
    counts["kind"] = "figure"
    return counts


def _add_lattice(bpy, col, motif, style, counts):
    """Draw a motif's atoms and bonds.

    The motif supplies POSITIONS; the style supplies what they look
    like. An atom species the style never declared is an error naming
    it, not a sphere in klink's idea of the right colour."""
    import bmesh
    from mathutils import Vector

    a_um = float(motif["a_um"])
    finish = style.atom_finish()
    bond = style.bond_style()
    for sp in motif["species"]:
        spec = style.species(sp["name"])
        mesh = bpy.data.meshes.new("sp_" + sp["name"])
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(
            bm, u_segments=12, v_segments=8,
            radius=a_um * float(spec["radius_fraction"]))
        bm.to_mesh(mesh); bm.free()
        mesh.materials.append(_material(
            bpy, sp["name"], _hex4(str(spec["color"]), 1.0),
            metallic=float(finish["metallic"]),
            rough=float(finish["roughness"])))
        for i, p in enumerate(sp["positions"]):
            o = bpy.data.objects.new(f'{sp["name"]}_{i}', mesh)
            o.location = Vector(p)
            col.objects.link(o)
            counts["atoms"] += 1
    bond_r = a_um * float(bond["radius_fraction"])
    bond_mesh = bpy.data.meshes.new("bond")
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=False, segments=8,
                          radius1=bond_r, radius2=bond_r, depth=1.0)
    bm.to_mesh(bond_mesh); bm.free()
    bond_mesh.materials.append(_material(
        bpy, "bond", _hex4(str(bond["color"]), 1.0),
        rough=float(bond["roughness"])))
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
    if "style" not in payload:
        print("payload has no 'style': klink ships no default look. "
              "Copy example_template/imaging/blender_style.py, edit it, "
              "and pass its JSON.", file=sys.stderr)
        return 2
    style = BlenderStyle.from_dict(payload["style"])
    if mode == "die":
        report = render_die_glb(
            payload["glb"], payload["out_png"], payload["out_blend"],
            style,
            camera=payload.get("camera", "default"),
            samples=int(payload.get("samples", 96)),
            transparent=bool(payload.get("transparent", True)),
            resolution=payload.get("resolution", (1920, 1080)))
    elif mode == "figure":
        stack = VisualStack.from_dict(payload["stack"])
        report = render_device_figure(
            payload["gds"], stack, payload["out_png"],
            payload["out_blend"], style, cell=payload.get("cell"),
            slabs=payload.get("slabs", ()),
            lattice_a_um=(float(payload["lattice_a_um"])
                          if payload.get("lattice_a_um") is not None
                          else None),
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

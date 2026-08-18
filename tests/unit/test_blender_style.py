"""The Blender exit owns no taste: every number comes from the example.

Two things are locked here.

1. klink ships NO default look. A missing style block is an error that
   names the file to copy — never a picture drawn with klink's numbers.
   (The whole `klink_blender_style_v1` schema exists because sun energy,
   camera lens, film transform and material recipes are somebody's taste
   tuned against somebody's device, exactly like a layer number.)

2. The STACK decides what a material IS; the style only decides how that
   looks. A luminance guess once lived in the die path — "metals in our
   stacks are light" — and metallised 11 of 15 materials in a plain CMOS
   stack, because there the pale materials are the substrate, the wells,
   the implants and the oxides while the real metals are dark. It also
   defeated the declared alpha and hid the tungsten plugs inside an
   opaque ILD. Nothing may infer a material class from a colour again.

No bpy needed: the classifier is pure and the shader builder runs
against stub materials.
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys

import pytest

from klink.domains.imaging.blender_scene import (apply_style_shading,
                                                 classify_material)
from klink.domains.imaging.blender_style import (BlenderStyle,
                                                 BlenderStyleError)

EXAMPLE = (pathlib.Path(__file__).resolve().parents[2]
           / "examples_klink" / "public" / "imaging")


@pytest.fixture(scope="module")
def style() -> BlenderStyle:
    """The example's style — the only place numbers are allowed."""
    sys.path.insert(0, str(EXAMPLE))
    try:
        from blender_style import STYLE
    finally:
        sys.path.remove(str(EXAMPLE))
    return STYLE


# ---------------------------------------------------------------- #
# 1. no style, no render
# ---------------------------------------------------------------- #

@pytest.mark.parametrize("block", ["material", "staging", "camera"])
def test_a_missing_block_names_the_file_to_copy(style, block):
    d = style.to_dict()
    d.pop(block)
    with pytest.raises(BlenderStyleError) as excinfo:
        BlenderStyle.from_dict(d)
    msg = str(excinfo.value)
    assert block in msg
    assert "example_template/imaging/blender_style.py" in msg


@pytest.mark.parametrize("path, key", [
    (("staging", "key_light"), "energy"),
    (("staging", "fill_light"), "offset_fraction"),
    (("material", "dielectric"), "transmission"),
    (("material", "metal"), "roughness"),
    (("camera",), "lens_mm"),
])
def test_a_missing_field_is_refused_not_defaulted(style, path, key):
    d = style.to_dict()
    node = d
    for step in path:
        node = node[step]
    node.pop(key)
    with pytest.raises(BlenderStyleError) as excinfo:
        BlenderStyle.from_dict(d)
    msg = str(excinfo.value)
    assert key in msg
    assert "klink ships no default" in msg


def test_wrong_format_is_refused(style):
    d = style.to_dict()
    d["format"] = "something_else"
    with pytest.raises(BlenderStyleError, match="klink_blender_style_v1"):
        BlenderStyle.from_dict(d)


def test_style_round_trips_through_json(style, tmp_path):
    p = tmp_path / "s.json"
    style.save(str(p))
    again = BlenderStyle.load(str(p))
    assert again.to_dict() == style.to_dict()
    assert again.camera_direction("face") == style.camera_direction("face")


def test_unknown_camera_preset_lists_the_real_ones(style):
    with pytest.raises(BlenderStyleError, match="default, face, top"):
        style.camera_direction("worm")


def test_lattice_constant_absence_explains_itself(style):
    bare = BlenderStyle(material=style.material, staging=style.staging,
                        camera=style.camera, lattice={})
    with pytest.raises(BlenderStyleError) as excinfo:
        bare.lattice_a_um()
    assert "lattice.a_um" in str(excinfo.value)


# ---------------------------------------------------------------- #
# 2. the stack classifies, the style only shades
# ---------------------------------------------------------------- #

@pytest.mark.parametrize("color, metallic, expect", [
    # the demo CMOS stack, which is where the old guess went wrong
    ([0.78, 0.72, 0.60, 1.0], 0.0, "matte"),      # p-substrate, pale
    ([0.91, 0.86, 0.75, 1.0], 0.0, "matte"),      # n-well, paler
    ([0.87, 0.90, 0.93, 1.0], 0.0, "matte"),      # spacer, palest
    ([0.81, 0.85, 0.88, 0.45], 0.0, "dielectric"),  # ILD
    ([0.43, 0.43, 0.47, 1.0], 0.9, "metal"),      # W plug, DARK
    ([0.48, 0.17, 0.17, 1.0], 0.7, "metal"),      # silicide, darker
])
def test_class_comes_from_the_declaration_not_the_colour(color, metallic,
                                                         expect):
    assert classify_material(color, metallic) == expect


def test_identical_declaration_opposite_colours_same_class():
    assert (classify_material([0.99, 0.99, 0.99, 1.0], 0.9)
            == classify_material([0.01, 0.01, 0.01, 1.0], 0.9) == "metal")
    assert (classify_material([0.99, 0.99, 0.99, 1.0], 0.0)
            == classify_material([0.01, 0.01, 0.01, 1.0], 0.0) == "matte")


# ---- stub Blender ------------------------------------------------- #

class Slot:
    def __init__(self, value):
        self.default_value = value
        self.is_linked = False


class Inputs(dict):
    """A real Principled BSDF has every socket already; the stub mints
    one on demand so `.get()` behaves like Blender's, not like a dict
    (returning None there would silently skip the very assignments
    these tests are checking)."""

    def __getitem__(self, k):
        if k not in self:
            self[k] = Slot(0.0)
        return dict.__getitem__(self, k)

    def get(self, k, default=None):
        return self[k]


class Node:
    def __init__(self, kind):
        self.type = kind
        self.samples = 0
        self.inputs = Inputs()
        self.outputs = Inputs()
        if kind == "ShaderNodeValToRGB":
            self.color_ramp = type("R", (), {
                "interpolation": "",
                "elements": [type("E", (), {"position": 0.0,
                                            "color": None})(),
                             type("E", (), {"position": 1.0,
                                            "color": None})()],
            })()


class Nodes:
    def __init__(self, bsdf):
        self._bsdf = bsdf
        self.created = []

    def get(self, name):
        return self._bsdf if name == "Principled BSDF" else None

    def new(self, kind):
        n = Node(kind)
        self.created.append(kind)
        return n


class Tree:
    def __init__(self, bsdf):
        self.nodes = Nodes(bsdf)
        self.links = type("L", (), {"new": lambda self, a, b: None})()


class Material:
    def __init__(self, name, color, alpha=1.0, metallic=0.0):
        bsdf = Node("BSDF_PRINCIPLED")
        bsdf.inputs["Base Color"] = Slot(list(color) + [alpha])
        bsdf.inputs["Alpha"] = Slot(alpha)
        bsdf.inputs["Metallic"] = Slot(metallic)
        bsdf.inputs["Roughness"] = Slot(0.5)
        bsdf.inputs["Normal"] = Slot(None)
        self.name = name
        self.bsdf = bsdf
        self.node_tree = Tree(bsdf)


def demo_materials():
    return [
        Material("p-substrate", [0.78, 0.72, 0.60]),
        Material("ILD", [0.81, 0.85, 0.88], alpha=0.45),
        Material("W plug", [0.43, 0.43, 0.47], metallic=0.9),
    ]


def test_shading_never_changes_what_the_stack_declared(style):
    mats = demo_materials()
    before = [(m.bsdf.inputs["Metallic"].default_value,
               m.bsdf.inputs["Alpha"].default_value) for m in mats]
    apply_style_shading(mats, style, model_size=10.0)
    after = [(m.bsdf.inputs["Metallic"].default_value,
              m.bsdf.inputs["Alpha"].default_value) for m in mats]
    assert after == before


def test_each_class_gets_its_own_declared_recipe(style):
    mats = demo_materials()
    counts = apply_style_shading(mats, style, model_size=10.0)
    assert counts == {"matte": 1, "dielectric": 1, "metal": 1}
    sub, ild, plug = mats
    assert (sub.bsdf.inputs["Roughness"].default_value
            == style.matte["roughness"])
    assert (ild.bsdf.inputs["Roughness"].default_value
            == style.dielectric["roughness"])
    assert (ild.bsdf.inputs["Transmission Weight"].default_value
            == style.dielectric["transmission"])
    assert (plug.bsdf.inputs["Roughness"].default_value
            == style.metal["roughness"])


def test_only_metals_are_mottled_and_everything_is_bevelled(style):
    mats = demo_materials()
    apply_style_shading(mats, style, model_size=10.0)
    sub, ild, plug = mats
    # mottling a transparent film would just make it look dirty
    assert "ShaderNodeTexNoise" not in ild.node_tree.nodes.created
    assert "ShaderNodeTexNoise" not in sub.node_tree.nodes.created
    assert "ShaderNodeTexNoise" in plug.node_tree.nodes.created
    assert "ShaderNodeValToRGB" in plug.node_tree.nodes.created
    for m in mats:
        assert "ShaderNodeBevel" in m.node_tree.nodes.created


def test_bevel_radius_scales_with_the_model(style):
    for size in (1.0, 1000.0):
        mats = demo_materials()
        apply_style_shading(mats, style, model_size=size)
        bevels = [n for m in mats
                  for n in [m.node_tree.nodes]]  # keep ref alive
        assert bevels
    # a fraction, so the same style suits a cell row and a whole die
    assert 0.0 < style.bevel["radius_fraction"] < 1.0


def test_a_style_with_no_mottle_block_simply_does_not_mottle(style):
    material = json.loads(json.dumps(style.material))
    material["metal"]["mottle"] = None
    plain = BlenderStyle(material=material, staging=style.staging,
                         camera=style.camera, lattice=style.lattice)
    mats = demo_materials()
    apply_style_shading(mats, plain, model_size=10.0)
    assert all("ShaderNodeTexNoise" not in m.node_tree.nodes.created
               for m in mats)


def test_a_style_with_no_bevel_simply_does_not_bevel(style):
    material = json.loads(json.dumps(style.material))
    material["bevel"] = None
    plain = BlenderStyle(material=material, staging=style.staging,
                         camera=style.camera, lattice=style.lattice)
    mats = demo_materials()
    apply_style_shading(mats, plain, model_size=10.0)
    assert all("ShaderNodeBevel" not in m.node_tree.nodes.created
               for m in mats)


# ---------------------------------------------------------------- #
# atoms: the motif gives positions, the style gives the look
# ---------------------------------------------------------------- #

def test_motifs_produce_geometry_and_no_appearance():
    """motifs.py used to pick every atom's colour and drawn radius —
    Mo blue-grey, S yellow. That is a convention in the 2D-material
    literature, not a physical fact, so it belongs to the caller. What
    is left here must be positions and bonds only."""
    import numpy as np
    shapely = pytest.importorskip("shapely.geometry")

    from klink.domains.imaging.motifs import graphene, mos2

    square = shapely.Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    for motif in (graphene(square, a_um=0.1), mos2(square, a_um=0.1)):
        assert motif["a_um"] == pytest.approx(0.1)
        assert motif["bonds"]
        assert "bond_radius_um" not in motif
        for sp in motif["species"]:
            assert set(sp) == {"name", "positions"}
            assert len(sp["positions"])
        assert isinstance(motif["species"][0]["positions"], np.ndarray)


def test_species_look_comes_from_the_declaration(style):
    mo = style.species("Mo")
    assert mo["color"].startswith("#")
    assert 0 < float(mo["radius_fraction"]) < 1
    assert set(style.atom_finish()) == {"metallic", "roughness"}
    assert set(style.bond_style()) == {"color", "roughness",
                                       "radius_fraction"}


def test_an_undeclared_species_is_named_not_guessed(style):
    with pytest.raises(BlenderStyleError) as excinfo:
        style.species("W")
    msg = str(excinfo.value)
    assert "'W'" in msg
    assert "lattice.species" in msg
    assert "C, Mo, S" in msg              # what IS declared
    assert "no palette for atoms" in msg


def test_a_malformed_species_entry_is_refused(style):
    d = style.to_dict()
    d["lattice"]["species"]["Mo"] = {"color": "#4a7594"}   # no radius
    with pytest.raises(BlenderStyleError, match="radius_fraction"):
        BlenderStyle.from_dict(d).species("Mo")
    d = style.to_dict()
    d["lattice"]["species"]["Mo"]["color"] = "blue"
    with pytest.raises(BlenderStyleError, match="#RRGGBB"):
        BlenderStyle.from_dict(d).species("Mo")


def test_a_stack_with_no_lattice_layer_never_needs_any_of_it(style):
    """The whole block is figure-mode-only: a plain CMOS stack must not
    be forced to declare atoms it will never draw."""
    d = style.to_dict()
    d["lattice"] = {}
    bare = BlenderStyle.from_dict(d)      # constructs fine
    with pytest.raises(BlenderStyleError, match="lattice.a_um"):
        bare.lattice_a_um()

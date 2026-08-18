"""klink xsection driver — headless, coordinate-driven process
cross-sections over the ``klayout_pyxs`` ENGINE.

The engine (import-only OPTIONAL dependency, pinned ``0.1.13`` — the
only verified version; it is never vendored) simulates the process
along a cut line from a ``.pyxs`` recipe.  Upstream's own driver is
GUI-bound (rulers, current view, dialogs); THIS driver is the klink
mechanism: layout file in → section layout + sidecar out, no GUI, no
plugin, CI-testable.

SECURITY / TRUST: a ``.pyxs`` recipe IS Python code and is executed
in-process with full privileges.  It is a trusted input — treat recipe
files like you treat your own scripts.  klink does not sandbox them.

ENGINE COUPLING (read before bumping the pin): this is the one place in
klink that reaches into a third-party library's PRIVATE surface.  The
driver SUBCLASSES ``klayout_pyxs.pyxs_lib.XSection`` and overrides
``_setup`` / ``_create_new_layout`` / ``_finalize_view`` (replacing the
GUI's ruler/view/dialog inputs with ours), calls
``_update_basic_regions()``, and type-tests recipe variables against
``MaterialData``.  None of that is public API; upstream can rename any
of it in a patch release without notice, and the failure would be a
changed section, not an ImportError.  ``PINNED_PYXS`` exists for exactly
this reason — bumping it is not a version-string edit, it requires
re-running the golden section tests and diffing the output GDS.

Determinism: section GDS files are written with GDS timestamps
DISABLED, so identical inputs give byte-identical files (golden-test
contract).

Output contract: every run takes ``output_dir`` + ``basename`` and
REFUSES to overwrite existing files unless ``overwrite=True``.  Every
run writes a machine-readable sidecar (format
``klink_imaging_result_v1``, stable key order).

Step protocol: a recipe line ``# klink-step: <name>`` marks a process
step boundary; ``steps=True`` executes cumulative prefixes (explicit
``output(...)`` lines stripped, all materials auto-output with a stable
name→layer map) and writes one section GDS per step.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

PINNED_PYXS = "0.1.13"
SIDECAR_FORMAT = "klink_imaging_result_v1"
STEP_RE = re.compile(r"^#\s*klink-step\s*:\s*(.+?)\s*$", re.M)
_OUTPUT_LINE_RE = re.compile(r"^\s*output\(.*$", re.M)
#: First layer number klink assigns to auto-output materials. A
#: klink convention (like the 999/99 port layer), not process data
#: — but a recipe that already writes 300/0 needs it moved, so
#: every entry point takes it as a parameter.
_AUTO_LAYER_BASE = 300


class XSectionError(ValueError):
    """Bad input or a recipe failure; the message says what to fix."""


def _engine():
    """Import the pinned engine, instructively."""
    try:
        import klayout_pyxs
    except ImportError as exc:
        raise XSectionError(
            "the cross-section engine is not installed in THIS "
            "interpreter. Install the verified version with: "
            f"pip install klayout-pyxs=={PINNED_PYXS}") from exc
    version = getattr(klayout_pyxs, "__version__", "unknown")
    if version != PINNED_PYXS:
        raise XSectionError(
            f"klayout_pyxs {version} is installed but klink pins "
            f"{PINNED_PYXS} (the only verified version — this driver "
            f"relies on engine internals). Install it with: "
            f"pip install klayout-pyxs=={PINNED_PYXS}")
    from klayout_pyxs import pyxs_lib
    from klayout_pyxs.geometry_2d import MaterialData
    return pyxs_lib, MaterialData


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _strip_outputs(recipe_text: str) -> str:
    """Remove explicit ``output(...)`` lines (auto-output replaces them).
    Only whole single-line calls are supported: a surviving fragment of
    a multi-line call would exec as a syntax error, so refuse it
    instructively instead."""
    stripped = _OUTPUT_LINE_RE.sub("", recipe_text)
    # a leftover CALL, or stripping that broke a recipe which parsed
    # fine before (= a multi-line output() whose tail survived)
    broke_it = _parses(recipe_text) and not _parses(stripped)
    if _calls_output(stripped) or broke_it:
        raise XSectionError(
            "the recipe uses a multi-line or non-statement output(...) "
            "call; keep each output() on ONE line (they are stripped "
            "and replaced by auto-output in steps/sweep modes)")
    return stripped


def _parses(text: str) -> bool:
    """Does this text parse as Python? (tokenize is too lenient — it
    accepts an unbalanced ')' left behind by stripping a multi-line
    call, which is exactly the case the guard must catch.)"""
    import ast
    try:
        ast.parse(text)
    except SyntaxError:
        return False
    return True


def _calls_output(text: str) -> bool:
    """True if a real ``output(`` CALL survives — tokenized, so the word
    inside a comment or a string does not count (a recipe that merely
    documents output() must not be refused)."""
    import io as _io
    import tokenize
    prev_name = False
    try:
        for tok in tokenize.generate_tokens(
                _io.StringIO(text).readline):
            if prev_name and tok.type == tokenize.OP and tok.string == "(":
                return True
            prev_name = (tok.type == tokenize.NAME
                         and tok.string == "output")
    except (tokenize.TokenError, IndentationError):
        # unbalanced source: the stripped text is not tokenizable, which
        # is exactly the multi-line-call case this guard exists for
        return "output(" in text
    return False


def parse_steps(recipe_text: str) -> List[Tuple[str, int]]:
    """``# klink-step: <name>`` markers -> [(name, line_index), ...]."""
    out = []
    for i, line in enumerate(recipe_text.splitlines()):
        m = STEP_RE.match(line)
        if m:
            out.append((m.group(1), i))
    return out


def _make_driver(pyxs_lib, MaterialData):
    """Build the db-only driver class (deferred so importing THIS module
    never imports the engine)."""
    import klayout.db as kdb

    class _KlinkXSection(pyxs_lib.XSectionGenerator):
        """db-only driver: layout in, layout out; no GUI anywhere."""

        def __init__(self, layout: "kdb.Layout", cell_index: int,
                     recipe_path: str, params: Mapping[str, float]):
            super().__init__(recipe_path)
            self._src_layout = layout
            self._src_cell = cell_index
            self._params = dict(params)
            self._recorded_outputs: List[Tuple[str, int]] = []

        # -- engine state, from OUR inputs (upstream reads the GUI) --
        def _setup(self, p1, p2):
            self._layout = self._src_layout
            self._dbu = self._layout.dbu
            self._cell = self._src_cell
            f = 1.0 / self._dbu
            p1_dbu = pyxs_lib.Point.from_dpoint(p1 * f)
            p2_dbu = pyxs_lib.Point.from_dpoint(p2 * f)
            self._line_dbu = pyxs_lib.Edge(p1_dbu, p2_dbu)
            def dbu_of(um):
                return pyxs_lib.int_floor(um / self._dbu + 0.5)
            self._extend = dbu_of(self._params["extend_um"])
            self._height = dbu_of(self._params["height_um"])
            self._depth = dbu_of(self._params["depth_um"])
            self._below = dbu_of(self._params["below_um"])
            self._delta = int(self._params["delta_dbu"])
            return True

        def _create_new_layout(self, cell_name_extension=None):
            self._target_layout = kdb.Layout()
            self._target_layout.dbu = self._dbu
            self._target_cell = self._target_layout.add_cell("XSECTION")
            self._is_target_layout_created = True
            self._target_view = None

        def _finalize_view(self):
            pass

        def output(self, layer_spec=None, layer_data=None, *args):
            # resolve the material's variable name EAGERLY: after exec a
            # rebound variable's original object may be gone and its id
            # reused, which would mislabel the layer in the sidecar
            name = None
            ns = getattr(self, "_exec_ns", None)
            if ns:
                name = next((k for k, v in ns.items()
                             if v is layer_data and not k.startswith("_")),
                            None)
            self._recorded_outputs.append((str(layer_spec), name,
                                           id(layer_data)))
            super().output(layer_spec, layer_data, *args)

        # -- the klink entry point --
        def run_text(self, p1_um, p2_um, text: str,
                     auto_output: bool,
                     name_to_layer: Dict[str, str],
                     exclude: Sequence[str],
                     auto_layer_base: int = _AUTO_LAYER_BASE):
            self._target_view = None
            self._target_cell_name = "XSECTION"
            self._setup(kdb.DPoint(*p1_um), kdb.DPoint(*p2_um))
            self._update_basic_regions()
            locals_dict = {a: getattr(self, a) for a in dir(self)
                           if a[0] != "_"}
            self._exec_ns = locals_dict
            try:
                exec(text, locals_dict)      # trusted recipe code
            except Exception as exc:
                raise XSectionError(
                    f"recipe raised {type(exc).__name__}: {exc} — "
                    f".pyxs recipes are trusted Python executed "
                    f"in-process; fix the recipe") from exc
            id_to_name = {id(v): k for k, v in locals_dict.items()
                          if isinstance(v, MaterialData)}
            materials: Dict[str, str] = {}      # layer 'L/D' -> name
            for layer_spec, eager_name, obj_id in self._recorded_outputs:
                materials[layer_spec] = (eager_name
                                         or id_to_name.get(obj_id)
                                         or layer_spec)
            if auto_output and not self._recorded_outputs:
                for nm, val in locals_dict.items():
                    if not isinstance(val, MaterialData):
                        continue
                    # convention: a recipe variable starting with '_' is
                    # an intermediate (half-grown oxide, scratch mask)
                    # and is not a material worth its own color
                    if nm.startswith("_") or nm in exclude:
                        continue
                    if nm not in name_to_layer:
                        name_to_layer[nm] = "%d/0" % (
                            auto_layer_base + len(name_to_layer))
                    super().output(name_to_layer[nm], val)
                    materials[name_to_layer[nm]] = nm
            return materials

        def write(self, path: str):
            opts = kdb.SaveLayoutOptions()
            # determinism contract: byte-identical files for identical
            # inputs (goldens); GDS timestamps would break that
            opts.gds2_write_timestamps = False
            self._target_layout.write(path, opts)

        def shape_counts(self) -> Dict[str, int]:
            cell = self._target_layout.cell(self._target_cell)
            out = {}
            for li in self._target_layout.layer_indexes():
                info = self._target_layout.get_info(li)
                n = cell.shapes(li).size()
                if n:
                    out["%d/%d" % (info.layer, info.datatype)] = n
            return out

    return _KlinkXSection


def _filename_slug(name: str) -> str:
    """An ASCII-safe filename fragment for a step name.

    Filenames stay ASCII on purpose (they travel through zips, CI and
    other people's tooling), but a name written in Chinese used to
    sanitise to nothing and leave a trail of dangling underscores:
    eleven steps produced `film_step03__`, `film_step09__`,
    `film_step10__1_`. Empty is honest — the real name is in the
    sidecar and burnt into the frame — so this returns "" rather than
    punctuation, and the caller keeps the step index for uniqueness.
    """
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
    return safe.strip("_.-")


def run_cut_polygons(
    layout: Any,
    cell_index: int,
    recipe_text: str,
    recipe_path: str,
    cut_um: Sequence[Sequence[float]],
    *,
    name_to_layer: Dict[str, str],
    exclude: Sequence[str] = (),
    height_um: float = 2.0,
    depth_um: float = 2.0,
    below_um: float = 2.0,
    extend_um: float = 2.0,
    delta_dbu: int = 10,
    auto_layer_base: int = _AUTO_LAYER_BASE,
) -> Dict[str, List[List[Tuple[float, float]]]]:
    """One engine cut -> ``{material name: [poly points (um), ...]}``.

    The building block for sweep consumers (process-true 3D): explicit
    ``output()`` lines are stripped and every material auto-output with
    the caller's persistent ``name_to_layer`` map, so material identity
    is stable across many cuts.  Section-plane coordinates: x = position
    along the cut, y = height (um).
    """
    pyxs_lib, MaterialData = _engine()
    params = {"height_um": height_um, "depth_um": depth_um,
              "below_um": below_um, "extend_um": extend_um,
              "delta_dbu": delta_dbu}
    driver_cls = _make_driver(pyxs_lib, MaterialData)
    drv = driver_cls(layout, cell_index, recipe_path, params)
    text = _strip_outputs(recipe_text)
    materials = drv.run_text(
        tuple(map(float, cut_um[0])), tuple(map(float, cut_um[1])),
        text, auto_output=True, name_to_layer=name_to_layer,
        exclude=exclude, auto_layer_base=auto_layer_base)
    # read back per-material polygons (ring lists [hull, hole...] —
    # a fully enclosed void, e.g. a keyhole in a trench fill, is real)
    tl = drv._target_layout
    cell = tl.cell(drv._target_cell)
    dbu = tl.dbu
    out: Dict[str, List[List[List[Tuple[float, float]]]]] = {}
    for li in tl.layer_indexes():
        info = tl.get_info(li)
        key = "%d/%d" % (info.layer, info.datatype)
        name = materials.get(key, key)
        polys = []
        for sh in cell.shapes(li).each():
            if not (sh.is_box() or sh.is_polygon() or sh.is_path()):
                continue
            dp = sh.dpolygon
            rings = [[(p.x, p.y) for p in dp.each_point_hull()]]
            for hi in range(dp.holes()):
                rings.append([(p.x, p.y)
                              for p in dp.each_point_hole(hi)])
            polys.append(rings)
        if polys:
            out.setdefault(name, []).extend(polys)
    return out


def run_xsection(
    gds_path: str,
    recipe_path: str,
    cut_um: Sequence[Sequence[float]],
    *,
    output_dir: str,
    basename: str,
    cell: Optional[str] = None,
    steps: bool = False,
    overwrite: bool = False,
    height_um: float = 2.0,
    depth_um: float = 2.0,
    below_um: float = 2.0,
    extend_um: float = 2.0,
    delta_dbu: int = 10,
    exclude: Sequence[str] = (),
    render: bool = False,
    stack: Optional[Any] = None,
    z_window_um: Optional[Sequence[float]] = None,
    axis: bool = False,
    style: Optional[Any] = None,
    auto_layer_base: int = _AUTO_LAYER_BASE,
    source_label: Optional[str] = None,
) -> Dict[str, Any]:
    """Cross-section ``gds_path`` along ``cut_um`` using ``recipe_path``.

    ``cut_um`` is the EXPLICIT cut line ``[[x1, y1], [x2, y2]]`` in
    microns (reading a GUI ruler needs a plugin RPC that does not exist
    yet — backlog).  Returns the sidecar dict; files are written under
    ``output_dir`` as ``<basename>.gds`` (or ``<basename>_stepNN_<name>
    .gds`` with ``steps=True``) plus ``<basename>.klink_imaging.json``.

    ``render=True`` additionally rasterizes each section to a PNG
    (materials colored via the optional ``stack`` VisualStack —
    recipe_symbol / recipe_styles matches; unmatched get deterministic
    auto-colors and are reported), and with ``steps=True`` assembles the
    frames into a contact-sheet PNG + animated GIF.  Needs
    numpy/scipy/pillow (instructive error when missing).
    ``z_window_um=(z_bottom, z_top)`` frames those rasters vertically —
    without it the deep substrate dominates the image and thin films are
    a sliver at the top; ``axis=True`` adds the z ruler and scale bar.
    Recipe variables whose name starts with ``_`` are treated as
    intermediates and are not auto-output as materials.
    """
    pyxs_lib, MaterialData = _engine()
    import klayout
    import klayout.db as kdb
    import klayout_pyxs

    if (not isinstance(cut_um, (list, tuple)) or len(cut_um) != 2
            or any(len(p) != 2 for p in cut_um)):
        raise XSectionError(
            f"cut_um must be [[x1, y1], [x2, y2]] in microns, got "
            f"{cut_um!r}")
    if not os.path.isfile(recipe_path):
        raise XSectionError(f"recipe not found: {recipe_path}")
    from ._util import require_plain_basename, top_cell_of
    basename = require_plain_basename(basename, XSectionError)
    os.makedirs(output_dir, exist_ok=True)

    layout = kdb.Layout()
    layout.read(gds_path)
    top = top_cell_of(layout, cell, XSectionError, gds_path)

    with open(recipe_path, encoding="utf-8") as fh:
        recipe_text = fh.read()
    params = {"height_um": height_um, "depth_um": depth_um,
              "below_um": below_um, "extend_um": extend_um,
              "delta_dbu": delta_dbu}
    driver_cls = _make_driver(pyxs_lib, MaterialData)
    p1, p2 = (tuple(map(float, cut_um[0])), tuple(map(float, cut_um[1])))

    if steps:
        markers = parse_steps(recipe_text)
        if not markers:
            raise XSectionError(
                "steps=True but the recipe has no '# klink-step: <name>' "
                "markers — add one line at the START of each process "
                "step you want a frame for")
        # a marker names the step that FOLLOWS it; the frame for step i
        # is the cumulative prefix up to the NEXT marker (last step runs
        # to end of file). Explicit output() lines are stripped and all
        # materials auto-output with a stable name->layer map.
        stripped = _strip_outputs(recipe_text)
        lines = stripped.splitlines(keepends=True)
        idxs = [idx for _, idx in markers] + [len(lines)]
        stages = [(markers[i][0], "".join(lines[:idxs[i + 1]]))
                  for i in range(len(markers))]
    else:
        stages = [("", recipe_text)]

    def stage_fname(si: int, stage_name: str) -> str:
        if steps:
            safe = _filename_slug(stage_name)
            tail = f"_{safe}" if safe else ""
            return f"{basename}_step{si:02d}{tail}.gds"
        return f"{basename}.gds"

    # the run is refuse-BEFORE-first-write: every output path is known
    # up front, so a collision can never leave partial results behind
    sidecar_path = os.path.join(output_dir,
                                f"{basename}.klink_imaging.json")
    planned = [os.path.join(output_dir, stage_fname(i, nm))
               for i, (nm, _t) in enumerate(stages)]
    if render:
        planned += [p[: -len(".gds")] + ".png" for p in planned]
        if steps and len(stages) > 1:
            planned += [os.path.join(output_dir, f"{basename}_film.png"),
                        os.path.join(output_dir, f"{basename}_film.gif")]
    planned.append(sidecar_path)
    if not overwrite:
        clashes = [p for p in planned if os.path.exists(p)]
        if clashes:
            raise XSectionError(
                f"{len(clashes)} output file(s) already exist (first: "
                f"{clashes[0]}); pass overwrite=True to replace them "
                f"(klink never clobbers silently, and refuses BEFORE "
                f"writing anything)")

    files: List[Dict[str, Any]] = []
    stage_reports: List[Dict[str, Any]] = []
    name_to_layer: Dict[str, str] = {}
    for si, (stage_name, text) in enumerate(stages):
        drv = driver_cls(layout, top.cell_index(), recipe_path, params)
        materials = drv.run_text(
            p1, p2, text, auto_output=True,
            name_to_layer=name_to_layer, exclude=exclude,
            auto_layer_base=auto_layer_base)
        if steps:
            safe = _filename_slug(stage_name)
            tail = f"_{safe}" if safe else ""
            fname = f"{basename}_step{si:02d}{tail}.gds"
        else:
            fname = f"{basename}.gds"
        path = os.path.join(output_dir, fname)
        if os.path.exists(path) and not overwrite:
            raise XSectionError(
                f"{path} exists; pass overwrite=True to replace it "
                f"(klink never clobbers silently)")
        drv.write(path)
        counts = drv.shape_counts()
        files.append({"path": path.replace(os.sep, "/"),
                      "sha256": _sha256(path), "kind": "section_gds",
                      "step": stage_name})
        stage_reports.append({
            "step": stage_name,
            "materials": [
                {"layer": layer, "name": materials.get(layer, layer),
                 "shapes": n}
                for layer, n in sorted(
                    counts.items(),
                    key=lambda kv: (int(kv[0].split("/")[0]),
                                    int(kv[0].split("/")[1])))],
        })

    render_out: Dict[str, Any] = {}
    if render:
        if style is None:
            raise XSectionError(
                "render=True needs style=<SectionStyle>: how the PNG "
                "LOOKS (page colour, gradient, outlines, ruler, label "
                "bar, scale bar) is YOUR data and klink ships no "
                "default. Copy example_template/imaging/section_style"
                ".py, or drop render=True and keep the section GDS.")
        from .raster import film_strip, render_section_png
        frame_paths: List[str] = []
        auto_colored: List[str] = []
        unrenderable: List[Dict[str, Any]] = []
        gds_files = list(files)          # snapshot: loop appends to files
        for f, rep in zip(gds_files, stage_reports):
            png = f["path"][: -len(".gds")] + ".png"
            if os.path.exists(png) and not overwrite:
                raise XSectionError(
                    f"{png} exists; pass overwrite=True to replace it")
            mat_map = {m["layer"]: m["name"] for m in rep["materials"]}
            r = render_section_png(
                f["path"], mat_map, png, style, stack=stack,
                z_window_um=z_window_um, axis=axis,
                label=rep["step"] or basename)
            frame_paths.append(png)
            for sym in r["auto_colored"]:
                if sym not in auto_colored:
                    auto_colored.append(sym)
            # a step name whose glyphs no installed font carries would
            # otherwise be baked into the PNG as tofu boxes and hashed
            # as if correct; say so instead
            if r.get("missing_glyphs"):
                unrenderable.append(
                    {"label": rep["step"] or basename,
                     "characters": r["missing_glyphs"]})
            files.append({"path": png.replace(os.sep, "/"),
                          "sha256": _sha256(png),
                          "kind": "section_png", "step": rep["step"]})
        render_out = {"auto_colored": auto_colored}
        if unrenderable:
            render_out["font_warnings"] = {
                "labels": unrenderable,
                "reason": "no installed font has these glyphs; they are "
                          "drawn as .notdef boxes. Install a CJK face "
                          "(Windows: Microsoft YaHei; Linux: "
                          "fonts-noto-cjk; macOS: PingFang) or use an "
                          "ASCII '# klink-step:' name",
            }
        if steps and len(frame_paths) > 1:
            strip = os.path.join(output_dir, f"{basename}_film.png")
            gif = os.path.join(output_dir, f"{basename}_film.gif")
            for p in (strip, gif):
                if os.path.exists(p) and not overwrite:
                    raise XSectionError(
                        f"{p} exists; pass overwrite=True to replace it")
            fs = film_strip(frame_paths, strip, gif)
            files.append({"path": fs["strip"], "sha256": _sha256(strip),
                          "kind": "film_strip", "step": ""})
            files.append({"path": fs["gif"], "sha256": _sha256(gif),
                          "kind": "film_gif", "step": ""})

    sidecar: Dict[str, Any] = {
        "format": SIDECAR_FORMAT,
        "tool": "imaging.xsection_run",
        "inputs": {
            "gds": (source_label or gds_path).replace(os.sep, "/"),
            "gds_sha256": _sha256(gds_path),
            "recipe": recipe_path.replace(os.sep, "/"),
            "recipe_sha256": _sha256(recipe_path),
            "cell": top.name,
            "cut_um": [list(p1), list(p2)],
            "steps": steps,
            "params": params,
            "exclude": sorted(exclude),
        },
        "outputs": {"files": files, "stages": stage_reports,
                    **({"render": render_out} if render else {})},
        "versions": {
            "klayout": klayout.__version__,
            "klayout_pyxs": klayout_pyxs.__version__,
        },
    }
    try:
        from klink._meta import __version__ as klink_version
        sidecar["versions"]["klink"] = klink_version
    except Exception:
        pass
    sidecar_path = os.path.join(output_dir,
                                f"{basename}.klink_imaging.json")
    if os.path.exists(sidecar_path) and not overwrite:
        raise XSectionError(
            f"{sidecar_path} exists; pass overwrite=True to replace it")
    with open(sidecar_path, "w", encoding="utf-8") as fh:
        json.dump(sidecar, fh, indent=1, sort_keys=True)
        fh.write("\n")
    sidecar["sidecar_path"] = sidecar_path.replace(os.sep, "/")
    return sidecar

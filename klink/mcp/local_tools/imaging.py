"""imaging.* local MCP tool handlers.

The imaging domain lives klink-side (design: docs/IMAGING_FAMILY_DESIGN
.md): heavy/optional deps stay in this interpreter, the KLayout plugin
is untouched.  Live-session interop uses existing RPCs only
(``layout.save_file`` to pull geometry, ``layout.show_file`` to display
results).
"""

from __future__ import annotations

import os
import tempfile

from ..results import _error_result, _json_result
from . import local_tool


def _safe_basename(value, default: str) -> str:
    """basename is a FILENAME STEM: reject separators/absolute paths so
    the write stays inside the declared output_dir (a traversing or
    absolute 'basename' would silently escape the fence)."""
    name = str(value or default)
    if (os.path.isabs(name) or os.path.dirname(name)
            or name in (".", "..") or not name.strip()):
        raise ValueError(
            f"basename must be a plain filename stem (no path "
            f"separators, not absolute), got {name!r}; the directory "
            f"is chosen with output_dir")
    return name


@local_tool(
    "imaging.blender",
    "Paper-grade Blender render (headless bpy, executed in a "
    "SUBPROCESS — bpy never lives in the server). mode='die': import a "
    "GLB from imaging.render3d, stand it flat, filmic + transparent "
    "film + shadow catcher. mode='figure': GDS-driven device figure at "
    "1:1 layout coordinates — kind='lattice' stack layers render as the "
    "material's ATOMIC STRUCTURE (graphene/MoS2 motifs), solids as "
    "exact layout prisms, plus explicit substrate/oxide 'slabs'. Both "
    "write <basename>.png (RGBA) + <basename>.blend (open in desktop "
    "Blender to adjust by hand) + sidecar. Needs pip install bpy "
    "(~300MB; instructive error if missing). Renders are not "
    "byte-deterministic (Cycles).",
    {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["die", "figure"]},
            "glb": {"type": "string", "description": "mode='die': GLB path (from imaging.render3d)."},
            "gds": {"type": "string", "description": "mode='figure': layout path. Omit to use the live KLayout session."},
            "cell": {"type": "string"},
            "stack": {"type": "string", "description": "mode='figure': klink_visual_stack_v1 JSON path."},
            "slabs": {"type": "array", "items": {"type": "object"},
                      "description": "mode='figure': explicit carriers, each {name,z0_um,z1_um,color[,alpha][,metallic]}."},
            "lattice_a_um": {"type": "number", "default": 0.08, "description": "Figure-scale lattice constant (visual parameter; real constants are in the motif library docs)."},
            "camera": {"type": "string", "enum": ["default", "face", "top"], "default": "default"},
            "samples": {"type": "integer", "default": 96},
            "transparent": {"type": "boolean", "default": True},
            "output_dir": {"type": "string"},
            "basename": {"type": "string", "default": "blender"},
            "overwrite": {"type": "boolean", "default": False},
            "timeout_s": {"type": "integer", "default": 600},
            "session": {"type": "string"},
        },
        "required": ["mode", "output_dir"],
        "additionalProperties": False,
    },
)
def _tool_imaging_blender(ctx, arguments: dict) -> dict:
    try:
        import hashlib
        import json as _json
        import subprocess
        import sys

        def sha(path):
            h = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()

        mode = str(arguments["mode"])
        out_dir = str(arguments["output_dir"])
        os.makedirs(out_dir, exist_ok=True)
        basename = _safe_basename(arguments.get("basename"), "blender")
        overwrite = bool(arguments.get("overwrite", False))
        png = os.path.join(out_dir, f"{basename}.png")
        blend = os.path.join(out_dir, f"{basename}.blend")
        side = os.path.join(out_dir, f"{basename}.klink_imaging.json")
        for p in (png, blend, side):
            if os.path.exists(p) and not overwrite:
                raise ValueError(
                    f"{p} exists; pass overwrite=true to replace it "
                    f"(klink never clobbers silently)")

        payload = {"mode": mode, "out_png": png, "out_blend": blend,
                   "camera": str(arguments.get("camera") or "default"),
                   "samples": int(arguments.get("samples", 96)),
                   "transparent": bool(
                       arguments.get("transparent", True))}
        client = close_after = None
        tmp_gds = None
        try:
            if mode == "die":
                if not arguments.get("glb"):
                    raise ValueError(
                        "mode='die' needs glb=<path> (build one with "
                        "imaging.render3d first)")
                payload["glb"] = str(arguments["glb"])
            elif mode == "figure":
                if not arguments.get("stack"):
                    raise ValueError(
                        "mode='figure' needs stack=<klink_visual_stack_"
                        "v1 json> (lattice layers use kind='lattice' + "
                        "motif)")
                from ...domains.imaging.visual_stack import VisualStack
                stack = VisualStack.load(str(arguments["stack"]))
                payload["stack"] = stack.to_dict()
                payload["slabs"] = arguments.get("slabs") or []
                payload["lattice_a_um"] = float(
                    arguments.get("lattice_a_um", 0.08))
                payload["cell"] = arguments.get("cell")
                gds = arguments.get("gds")
                if not gds:
                    client, close_after = ctx._session_scoped_client(
                        arguments.get("session"))
                    fd, tmp_gds = tempfile.mkstemp(suffix=".gds")
                    os.close(fd)
                    save = {"path": tmp_gds}
                    if arguments.get("cell"):
                        save["cell"] = arguments["cell"]
                    client.call("layout.save_file", save)
                    gds = tmp_gds
                    payload["cell"] = None
                payload["gds"] = str(gds)

            fd, payload_path = tempfile.mkstemp(suffix=".json")
            os.close(fd)
            fd, report_path = tempfile.mkstemp(suffix=".json")
            os.close(fd)
            payload["out_report"] = report_path
            try:
                with open(payload_path, "w", encoding="utf-8") as fh:
                    _json.dump(payload, fh)
                proc = subprocess.run(
                    [sys.executable, "-m",
                     "klink.domains.imaging.blender_scene",
                     payload_path],
                    capture_output=True, text=True,
                    timeout=int(arguments.get("timeout_s", 600)))
                if proc.returncode != 0:
                    tail = (proc.stderr or proc.stdout or "").strip()
                    raise ValueError(
                        "blender subprocess failed: "
                        + tail[-800:])
                with open(report_path, encoding="utf-8") as fh:
                    report = _json.load(fh)
            finally:
                for p in (payload_path, report_path):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
        finally:
            if tmp_gds:
                try:
                    os.unlink(tmp_gds)
                except OSError:
                    pass
            if close_after and client is not None:
                try:
                    client.close()
                except Exception:
                    pass

        sidecar = {
            "format": "klink_imaging_result_v1",
            "tool": "imaging.blender",
            "inputs": {k: arguments.get(k) for k in
                       ("mode", "glb", "gds", "cell", "stack",
                        "camera", "samples", "lattice_a_um")},
            "outputs": {
                "files": [
                    {"path": png.replace(os.sep, "/"),
                     "sha256": sha(png), "kind": "render_png"},
                    {"path": blend.replace(os.sep, "/"),
                     "sha256": sha(blend), "kind": "blend"},
                ],
                "report": report,
            },
            "notes": "render hashes are provenance, not goldens "
                     "(Cycles is not byte-deterministic)",
        }
        with open(side, "w", encoding="utf-8") as fh:
            _json.dump(sidecar, fh, indent=1, sort_keys=True)
            fh.write("\n")
        sidecar["sidecar_path"] = side.replace(os.sep, "/")
        return _json_result(sidecar)
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "imaging.sem_top",
    "SEM-style top view of the layout: per-layer grey levels "
    "(sem_grey) + bright topography rims (edge_glow) from a "
    "klink_visual_stack_v1 declaration, with beam blur, film grain, "
    "scanlines and vignette (deterministic, seeded). Writes "
    "<basename>_sem.png (greyscale) and <basename>_sem_color.png "
    "(false color from layer colors) + a klink_imaging_result_v1 "
    "sidecar. layers=[...] restricts to a subset — e.g. the masks "
    "printed up to a given process step. Needs numpy/scipy/pillow.",
    {
        "type": "object",
        "properties": {
            "gds": {"type": "string", "description": "Path to the layout file. Omit to use the live KLayout session."},
            "cell": {"type": "string", "description": "Cell to render (default: top/current cell)."},
            "stack": {"type": "string", "description": "Path to a klink_visual_stack_v1 JSON (sem_grey/edge_glow/color per layer)."},
            "layers": {"type": "array", "items": {"type": "string"}, "description": "Optional 'L/D' subset (paint order stays stack order)."},
            "width_px": {"type": "integer", "default": 1600},
            "corner_radius_um": {"type": "number", "default": 0.05, "description": "Litho corner rounding applied to every mask."},
            "seed": {"type": "integer", "default": 7, "description": "Noise seed (same seed = identical image)."},
            "output_dir": {"type": "string"},
            "basename": {"type": "string", "default": "semtop"},
            "overwrite": {"type": "boolean", "default": False},
            "session": {"type": "string", "description": "KLayout session id/label (default: primary)."},
        },
        "required": ["stack", "output_dir"],
        "additionalProperties": False,
    },
)
def _tool_imaging_sem_top(ctx, arguments: dict) -> dict:
    try:
        import hashlib
        import json as _json

        from ...domains.imaging.raster import render_sem_png
        from ...domains.imaging.visual_stack import VisualStack

        def sha(path):
            h = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()

        stack = VisualStack.load(str(arguments["stack"]))
        out_dir = str(arguments["output_dir"])
        os.makedirs(out_dir, exist_ok=True)
        basename = _safe_basename(arguments.get("basename"), "semtop")
        overwrite = bool(arguments.get("overwrite", False))
        grey = os.path.join(out_dir, f"{basename}_sem.png")
        color = os.path.join(out_dir, f"{basename}_sem_color.png")
        side = os.path.join(out_dir, f"{basename}.klink_imaging.json")
        for p in (grey, color, side):
            if os.path.exists(p) and not overwrite:
                raise ValueError(
                    f"{p} exists; pass overwrite=true to replace it "
                    f"(klink never clobbers silently)")

        gds = arguments.get("gds")
        client = close_after = None
        tmp_gds = None
        try:
            if not gds:
                client, close_after = ctx._session_scoped_client(
                    arguments.get("session"))
                fd, tmp_gds = tempfile.mkstemp(suffix=".gds")
                os.close(fd)
                save = {"path": tmp_gds}
                if arguments.get("cell"):
                    save["cell"] = arguments["cell"]
                client.call("layout.save_file", save)
                gds = tmp_gds
            report = render_sem_png(
                gds, stack, grey, out_color=color,
                cell=(None if tmp_gds else arguments.get("cell")),
                layers=arguments.get("layers"),
                width_px=int(arguments.get("width_px", 1600)),
                corner_radius_um=float(
                    arguments.get("corner_radius_um", 0.05)),
                seed=int(arguments.get("seed", 7)))
        finally:
            if tmp_gds:
                try:
                    os.unlink(tmp_gds)
                except OSError:
                    pass
            if close_after and client is not None:
                try:
                    client.close()
                except Exception:
                    pass

        sidecar = {
            "format": "klink_imaging_result_v1",
            "tool": "imaging.sem_top",
            "inputs": {
                "gds": str(arguments.get("gds") or "<live session>"),
                "stack": str(arguments["stack"]),
                "stack_name": stack.name,
                "layers": arguments.get("layers"),
                "seed": int(arguments.get("seed", 7)),
            },
            "outputs": {
                "files": [
                    {"path": grey.replace(os.sep, "/"),
                     "sha256": sha(grey), "kind": "sem_png"},
                    {"path": color.replace(os.sep, "/"),
                     "sha256": sha(color), "kind": "sem_color_png"},
                ],
                "report": report,
            },
        }
        with open(side, "w", encoding="utf-8") as fh:
            _json.dump(sidecar, fh, indent=1, sort_keys=True)
            fh.write("\n")
        sidecar["sidecar_path"] = side.replace(os.sep, "/")
        return _json_result(sidecar)
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "imaging.render3d",
    "Build a 3D model (GLB) of the layout plus a SELF-CONTAINED "
    "interactive viewer page (html: embedded model + vendored viewer "
    "JS + manual color/metal/rough panel + PNG export; opens by "
    "double-click, offline). mode='fast' extrudes each layer of a "
    "klink_visual_stack_v1 declaration between its z0_um/z1_um; "
    "mode='process' sweeps the xsection engine across the die so "
    "curvature (LOCOS, conformal layers, CMP) comes from the recipe — "
    "engine materials are styled by matching recipe_symbol in the "
    "stack, unmatched ones render grey and are listed as 'unstyled'. "
    "fraction<1 stops the sweep mid-die: the exposed face is a true "
    "cross-section (cutaway). Deterministic outputs + "
    "klink_imaging_result_v1 sidecar; never overwrites unless "
    "overwrite=true.",
    {
        "type": "object",
        "properties": {
            "gds": {"type": "string", "description": "Path to the layout file. Omit to use the live KLayout session."},
            "cell": {"type": "string", "description": "Cell to render (default: top/current cell)."},
            "stack": {"type": "string", "description": "Path to a klink_visual_stack_v1 JSON (example/project-owned; klink ships none)."},
            "mode": {"type": "string", "enum": ["fast", "process"], "default": "fast"},
            "recipe": {"type": "string", "description": "Required for mode='process': the .pyxs recipe (trusted Python)."},
            "slices": {"type": "integer", "default": 36, "description": "process mode: number of engine cuts."},
            "fraction": {"type": "number", "default": 1.0, "description": "process mode: sweep this fraction of the die (<1 = cutaway)."},
            "exclude": {"type": "array", "items": {"type": "string"}, "description": "process mode: recipe variables to skip (consumed intermediates)."},
            "output_dir": {"type": "string"},
            "basename": {"type": "string", "default": "render3d"},
            "overwrite": {"type": "boolean", "default": False},
            "session": {"type": "string", "description": "KLayout session id/label (default: primary)."},
        },
        "required": ["stack", "output_dir"],
        "additionalProperties": False,
    },
)
def _tool_imaging_render3d(ctx, arguments: dict) -> dict:
    try:
        import hashlib
        import json as _json

        from ...domains.imaging.mesh3d import (build_glb_fast,
                                               build_glb_process)
        from ...domains.imaging.viewer import build_viewer_html
        from ...domains.imaging.visual_stack import VisualStack

        def sha(path):
            h = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()

        stack = VisualStack.load(str(arguments["stack"]))
        out_dir = str(arguments["output_dir"])
        os.makedirs(out_dir, exist_ok=True)
        basename = _safe_basename(arguments.get("basename"), "render3d")
        overwrite = bool(arguments.get("overwrite", False))
        glb = os.path.join(out_dir, f"{basename}.glb")
        html = os.path.join(out_dir, f"{basename}.html")
        side = os.path.join(out_dir, f"{basename}.klink_imaging.json")
        for p in (glb, html, side):
            if os.path.exists(p) and not overwrite:
                raise ValueError(
                    f"{p} exists; pass overwrite=true to replace it "
                    f"(klink never clobbers silently)")

        gds = arguments.get("gds")
        client = close_after = None
        tmp_gds = None
        try:
            if not gds:
                client, close_after = ctx._session_scoped_client(
                    arguments.get("session"))
                fd, tmp_gds = tempfile.mkstemp(suffix=".gds")
                os.close(fd)
                save = {"path": tmp_gds}
                if arguments.get("cell"):
                    save["cell"] = arguments["cell"]
                client.call("layout.save_file", save)
                gds = tmp_gds
            mode = str(arguments.get("mode") or "fast")
            cell = None if tmp_gds else arguments.get("cell")
            if mode == "process":
                recipe = arguments.get("recipe")
                if not recipe:
                    raise ValueError(
                        "mode='process' needs recipe=<path to .pyxs> "
                        "(the engine sweep is recipe-driven); use "
                        "mode='fast' for a plain stack extrusion")
                report = build_glb_process(
                    gds, stack, str(recipe), glb, cell=cell,
                    slices=int(arguments.get("slices", 36)),
                    fraction=float(arguments.get("fraction", 1.0)),
                    exclude=tuple(arguments.get("exclude") or ()))
            else:
                report = build_glb_fast(gds, stack, glb, cell=cell)
        finally:
            if tmp_gds:
                try:
                    os.unlink(tmp_gds)
                except OSError:
                    pass
            if close_after and client is not None:
                try:
                    client.close()
                except Exception:
                    pass

        build_viewer_html(glb, html, title=basename,
                          overwrite=overwrite)
        sidecar = {
            "format": "klink_imaging_result_v1",
            "tool": "imaging.render3d",
            "inputs": {
                "gds": str(arguments.get("gds") or "<live session>"),
                "stack": str(arguments["stack"]),
                "stack_name": stack.name,
                "mode": report["mode"],
                "recipe": arguments.get("recipe"),
            },
            "outputs": {
                "files": [
                    {"path": glb.replace(os.sep, "/"),
                     "sha256": sha(glb), "kind": "glb"},
                    {"path": html.replace(os.sep, "/"),
                     "sha256": sha(html), "kind": "viewer_html"},
                ],
                "report": report,
            },
        }
        with open(side, "w", encoding="utf-8") as fh:
            _json.dump(sidecar, fh, indent=1, sort_keys=True)
            fh.write("\n")
        sidecar["sidecar_path"] = side.replace(os.sep, "/")
        return _json_result(sidecar)
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "imaging.xsection_run",
    "Process cross-section from a .pyxs recipe along an explicit cut "
    "line — headless, deterministic, engine=klayout_pyxs (pinned; "
    "instructive install error if missing). Writes <basename>.gds (or "
    "per-step files with steps=true using '# klink-step: <name>' "
    "recipe markers) + a klink_imaging_result_v1 sidecar into "
    "output_dir; never overwrites unless overwrite=true. Source is "
    "either a gds path OR the live KLayout session (saved via "
    "layout.save_file). show=true opens the section in a new tab. "
    "NOTE: .pyxs recipes are trusted Python code executed in-process.",
    {
        "type": "object",
        "properties": {
            "gds": {"type": "string", "description": "Path to the layout file. Omit to use the live KLayout session."},
            "cell": {"type": "string", "description": "Cell to section (default: top/current cell)."},
            "recipe": {"type": "string", "description": "Path to the .pyxs process recipe (example/project-owned; klink ships none)."},
            "cut_um": {
                "type": "array", "items": {
                    "type": "array", "items": {"type": "number"},
                    "minItems": 2, "maxItems": 2},
                "minItems": 2, "maxItems": 2,
                "description": "[[x1,y1],[x2,y2]] microns. Explicit only — GUI ruler readout is not available (backlog).",
            },
            "output_dir": {"type": "string", "description": "Directory for outputs (created if missing)."},
            "basename": {"type": "string", "default": "xsection", "description": "Plain filename stem (no path separators)."},
            "steps": {"type": "boolean", "default": False, "description": "Per-step film via '# klink-step: <name>' markers."},
            "overwrite": {"type": "boolean", "default": False},
            "show": {"type": "boolean", "default": False, "description": "Open the (last) section GDS in a new KLayout tab (needs a live session)."},
            "height_um": {"type": "number", "default": 2.0},
            "depth_um": {"type": "number", "default": 2.0},
            "below_um": {"type": "number", "default": 2.0},
            "extend_um": {"type": "number", "default": 2.0},
            "delta_dbu": {"type": "integer", "default": 10},
            "exclude": {"type": "array", "items": {"type": "string"}, "description": "Recipe material variable names to skip in auto-output (consumed intermediates)."},
            "render": {"type": "boolean", "default": False, "description": "Also rasterize each section to PNG (+ film strip/GIF with steps=true). Needs numpy/scipy/pillow."},
            "stack": {"type": "string", "description": "Optional klink_visual_stack_v1 JSON path: colors rendered materials via recipe_symbol/recipe_styles; unmatched get deterministic auto-colors (reported)."},
            "session": {"type": "string", "description": "KLayout session id/label (default: primary)."},
        },
        "required": ["recipe", "cut_um", "output_dir"],
        "additionalProperties": False,
    },
)
def _tool_imaging_xsection_run(ctx, arguments: dict) -> dict:
    try:
        from ...domains.imaging.xsection_driver import run_xsection

        gds = arguments.get("gds")
        client = close_after = None
        tmp_gds = None
        try:
            if not gds or arguments.get("show"):
                client, close_after = ctx._session_scoped_client(
                    arguments.get("session"))
            if not gds:
                fd, tmp_gds = tempfile.mkstemp(suffix=".gds")
                os.close(fd)
                save = {"path": tmp_gds}
                if arguments.get("cell"):
                    save["cell"] = arguments["cell"]
                client.call("layout.save_file", save)
                gds = tmp_gds
            stack = None
            if arguments.get("stack"):
                from ...domains.imaging.visual_stack import VisualStack
                stack = VisualStack.load(str(arguments["stack"]))
            result = run_xsection(
                gds,
                str(arguments["recipe"]),
                arguments["cut_um"],
                output_dir=str(arguments["output_dir"]),
                basename=_safe_basename(arguments.get("basename"), "xsection"),
                cell=(None if tmp_gds else arguments.get("cell")),
                steps=bool(arguments.get("steps", False)),
                overwrite=bool(arguments.get("overwrite", False)),
                height_um=float(arguments.get("height_um", 2.0)),
                depth_um=float(arguments.get("depth_um", 2.0)),
                below_um=float(arguments.get("below_um", 2.0)),
                extend_um=float(arguments.get("extend_um", 2.0)),
                delta_dbu=int(arguments.get("delta_dbu", 10)),
                exclude=tuple(arguments.get("exclude") or ()),
                render=bool(arguments.get("render", False)),
                stack=stack,
                source_label=("<live session>" if tmp_gds else None),
            )
            if arguments.get("show"):
                shown = [f["path"] for f in result["outputs"]["files"]
                         if f["kind"] == "section_gds"][-1]
                client.call("layout.show_file", {"path": shown})
                result["shown"] = shown
        finally:
            if tmp_gds:
                try:
                    os.unlink(tmp_gds)
                except OSError:
                    pass
            if close_after and client is not None:
                try:
                    client.close()
                except Exception:
                    pass
        return _json_result(result)
    except Exception as exc:
        return _error_result(str(exc))

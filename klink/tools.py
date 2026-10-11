"""Deterministic KLayout tools: install / uninstall / list / export tool directories.

A tool is a directory ``<id>/`` with ``tool.json`` (interface: title, params, presets,
selection input, prompt policy) and ``run.py`` (a plain pya macro as written for
KLayout's macro IDE, or ``def run(ctx, params) -> dict``; see ``docs/TOOL_AUTHORING.md``).
Installed tools live under ``<registry_root>/tools/<id>/``; installing one also writes a toolbar button manifest (``definition: "tool:<id>"``, one dropdown entry per
preset), so a click runs the tool KLayout-native through the klink plugin -- no external
process. ``run`` asks the plugin over RPC (``tool.run``). See ``docs/TOOLS.md``.

The validation rules here MUST stay identical to
``klink_plugin/python/klink_server/tools.py`` (unit-tested for parity).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PureWindowsPath

from . import buttons

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
TYPES = ("int", "number", "string", "bool", "enum", "layer")
PROMPTS = ("missing", "always", "never")
# where a layer param's list comes from (docs/TOOLS.md "Layer recognition")
LAYER_FROM = ("region", "selection", "layout")
# "1/0", "1" (datatype 0), "1,0", "1 0", "1:0"; surrounding whitespace allowed
LAYER_RE = re.compile(r"^\s*(\d+)\s*(?:[/,:\s]\s*(\d+))?\s*$")
_TRUE, _FALSE = ("true", "yes", "y", "1", "on"), ("false", "no", "n", "0", "off")
ICON_EXTS = (".png", ".svg")  # toolbar icon formats (KLayout's Qt renders both)
ICON_REASON = "'icon' must be a file name inside the tool folder (relative, no '..') ending in .png or .svg"


def icon_name_ok(icon) -> bool:
    """True for a relative .png/.svg path that stays inside the tool folder (same on every OS)."""
    if not isinstance(icon, str) or not icon.strip():
        return False
    p = PureWindowsPath(icon)  # treats / and \ as separators everywhere; anchor = drive or root
    return not p.anchor and ".." not in p.parts and p.suffix.lower() in ICON_EXTS


def _parse_layer(value) -> str | None:
    """Canonical "L/D" for "1/0", "1", "1,0", "1 0", "1:0", an int (datatype 0) or [L, D]; else None."""
    def num(v):
        return isinstance(v, int) and not isinstance(v, bool) and v >= 0
    if num(value):
        return f"{value}/0"
    if isinstance(value, list) and len(value) == 2 and all(num(v) for v in value):
        return f"{value[0]}/{value[1]}"
    m = LAYER_RE.match(value) if isinstance(value, str) else None
    return None if m is None else f"{int(m.group(1))}/{int(m.group(2) or 0)}"


def coerce(pdef: dict, value):
    """Validated value for one param (strings from a dialog are converted); ValueError(reason)."""
    kind = pdef["type"]
    if kind in ("int", "number"):
        conv, need = (int, "an integer") if kind == "int" else (float, "a number")
        if isinstance(value, str):
            try:
                value = conv(value.strip())
            except ValueError:
                raise ValueError(f"expected {need}") from None
        if isinstance(value, bool) or not isinstance(value, int if kind == "int" else (int, float)):
            raise ValueError(f"expected {need}")
        if pdef.get("min") is not None and value < pdef["min"]:
            raise ValueError(f"must be >= {pdef['min']}")
        if pdef.get("max") is not None and value > pdef["max"]:
            raise ValueError(f"must be <= {pdef['max']}")
    elif kind == "bool":
        if isinstance(value, str) and value.strip().lower() in _TRUE + _FALSE:
            value = value.strip().lower() in _TRUE
        if not isinstance(value, bool):
            raise ValueError("expected yes/no (a boolean)")
    elif kind == "layer":
        same_ok = pdef.get("allow_same") is True  # "same" is opt-in per param
        if same_ok and isinstance(value, str) and value.strip().lower() == "same":
            return "same"
        layer = _parse_layer(value)
        if layer is None:
            raise ValueError("expected a layer as L/D (e.g. 1/0 or just 1)"
                             + (" or 'same'" if same_ok else ""))
        return layer
    elif kind == "enum" and value not in pdef["choices"]:
        raise ValueError("expected one of " + ", ".join(pdef["choices"]))
    elif kind == "string" and not isinstance(value, str):
        raise ValueError("expected a string")
    return value


def _check_param(name: str, pdef) -> str | None:
    if not ID_RE.fullmatch(name):
        return f"param name {name!r} must match ^[a-z][a-z0-9_]{{0,39}}$"
    if not isinstance(pdef, dict):
        return f"params.{name} must be an object"
    if pdef.get("type") not in TYPES:
        return f"params.{name}.type must be one of " + ", ".join(TYPES)
    if pdef["type"] == "enum":
        ch = pdef.get("choices")
        if not isinstance(ch, list) or not ch or not all(isinstance(c, str) for c in ch) \
                or len(set(ch)) != len(ch):
            return f"params.{name}: enum needs a non-empty 'choices' list of distinct strings"
    for key in ("min", "max"):
        v = pdef.get(key)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
            return f"params.{name}.{key} must be a number"
    for key in ("title", "doc", "unit"):
        if pdef.get(key) is not None and not isinstance(pdef[key], str):
            return f"params.{name}.{key} must be a string"
    if "allow_same" in pdef and (pdef["type"] != "layer" or not isinstance(pdef["allow_same"], bool)):
        return f"params.{name}.allow_same must be true/false, on a layer param"
    for key in ("allow_new", "skip_selection_layers"):
        if key in pdef and (pdef["type"] != "layer" or not isinstance(pdef[key], bool)):
            return f"params.{name}.{key} must be true/false, on a layer param"
    if "from" in pdef and (pdef["type"] != "layer" or pdef["from"] not in LAYER_FROM):
        return f"params.{name}.from must be one of " + ", ".join(LAYER_FROM) + ", on a layer param"
    for key in ("exclude", "allow_new", "skip_selection_layers"):
        if key in pdef and "from" not in pdef:
            return f"params.{name}.{key} needs 'from' (the layer list to pick from)"
    if "default" in pdef:
        try:
            coerce(pdef, pdef["default"])
        except ValueError as exc:
            return f"params.{name}.default: {exc}"
    return None


def validate_tool(spec, dirname: str) -> str | None:
    """None when tool.json is usable, else a one-line reason."""
    if not isinstance(spec, dict):
        return "not a JSON object"
    tid = spec.get("id")
    if not isinstance(tid, str) or not ID_RE.fullmatch(tid):
        return "'id' must match ^[a-z][a-z0-9_]{0,39}$"
    if tid != dirname:
        return f"'id' {tid!r} does not match the directory name {dirname!r}"
    if not isinstance(spec.get("title"), str) or not spec["title"].strip():
        return "'title' must be a non-empty string"
    if spec.get("label") is not None and (not isinstance(spec["label"], str) or not spec["label"].strip()):
        return "'label' must be a non-empty string"
    if spec.get("description") is not None and not isinstance(spec["description"], str):
        return "'description' must be a string"
    if spec.get("icon") is not None and not icon_name_ok(spec["icon"]):
        return ICON_REASON
    inputs = spec.get("inputs", {})
    if not isinstance(inputs, dict):
        return "'inputs' must be an object"
    for kind, idef in inputs.items():
        if kind != "selection":
            return f"unknown input kind {kind!r} (supported: selection)"
        if not isinstance(idef, dict):
            return "'inputs.selection' must be an object"
        for key in ("min_shapes", "min"):
            n = idef.get(key, 0)
            if isinstance(n, bool) or not isinstance(n, int) or n < 0:
                return f"inputs.selection.{key} must be an integer >= 0"
    params = spec.get("params", {})
    if not isinstance(params, dict):
        return "'params' must be an object"
    for name, pdef in params.items():
        problem = _check_param(name, pdef)
        if problem:
            return problem
    for name, pdef in params.items():
        if "exclude" not in pdef:
            continue
        ex = pdef["exclude"]
        if not isinstance(ex, list) or not all(isinstance(n, str) for n in ex):
            return f"params.{name}.exclude must be a list of layer param names"
        for other in ex:
            if other == name or other not in params or params[other].get("type") != "layer":
                return f"params.{name}.exclude: {other!r} is not another layer param of this tool"
    presets = spec.get("presets", {})
    if not isinstance(presets, dict):
        return "'presets' must be an object"
    for name, pre in presets.items():
        if not ID_RE.fullmatch(name):
            return f"preset name {name!r} must match ^[a-z][a-z0-9_]{{0,39}}$"
        if not isinstance(pre, dict):
            return f"presets.{name} must be an object"
        if pre.get("title") is not None and not isinstance(pre["title"], str):
            return f"presets.{name}.title must be a string"
        if not isinstance(pre.get("params"), dict):
            return f"presets.{name}.params must be an object"
        for key, value in pre["params"].items():
            if key not in params:
                return f"presets.{name}: unknown param {key!r}"
            try:
                coerce(params[key], value)
            except ValueError as exc:
                return f"presets.{name}.params.{key}: {exc}"
    if spec.get("prompt", "missing") not in PROMPTS:
        return "'prompt' must be one of " + ", ".join(PROMPTS)
    return None


def tools_dir(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root) / "tools"
    from .mcp.session_registry import default_registry_root  # same resolver as the plugin

    return default_registry_root() / "tools"


def check_dir(folder: Path, dirname: str | None = None) -> tuple[dict | None, str | None]:
    """(tool.json spec, None) when ``folder`` is a usable tool directory, else (spec?, reason)."""
    folder = Path(folder)
    try:
        spec = json.loads((folder / "tool.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"tool.json unreadable: {exc}"
    name = dirname if dirname is not None else (spec.get("id") if isinstance(spec, dict) else None)
    problem = validate_tool(spec, name if isinstance(name, str) else folder.name)
    if problem is None and not (folder / "run.py").is_file():
        problem = "missing run.py"
    if problem is None and spec.get("icon") and not (folder / spec["icon"]).is_file():
        problem = f"icon file not found: {folder / spec['icon']} (put it in the tool folder or drop 'icon')"
    return spec, problem


def _find_tool_root(base: Path) -> Path:
    """The single tool directory inside an extracted zip: files at the root, or one top folder."""
    if (base / "tool.json").is_file():
        return base
    subs = [p for p in base.iterdir() if p.is_dir() and not p.name.startswith(("__MACOSX", "."))]
    hits = [p for p in subs if (p / "tool.json").is_file()]
    if len(hits) != 1:
        raise ValueError(f"zip must contain exactly one tool directory with a tool.json "
                         f"(found {len(hits)})")
    return hits[0]


def _copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def install(src: str | Path, root: str | Path | None = None, reload: bool = True) -> dict:
    """Validate and copy a tool directory (or a .zip of one) into ``<root>/tools/<id>/``,
    replacing an existing install, then write its button manifest and optionally reload
    the toolbar. Returns ``{id, dir, button, items, reload}``. ValueError when invalid."""
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(f"{src} does not exist; pass a tool directory or a .zip of one")
    with tempfile.TemporaryDirectory(prefix="klink_tool_") as scratch:
        if src.is_file():
            if not zipfile.is_zipfile(src):
                raise ValueError(f"{src} is neither a directory nor a .zip file")
            base = Path(scratch) / "unzipped"
            with zipfile.ZipFile(src) as zf:
                for member in zf.namelist():  # refuse path traversal
                    target = (base / member).resolve()
                    if not str(target).startswith(str(base.resolve())):
                        raise ValueError(f"zip member {member!r} escapes the archive root")
                zf.extractall(base)
            tool_src = _find_tool_root(base)
            # files at the zip root: id from tool.json; one top folder: it must be named like the id
            spec, problem = check_dir(tool_src, None if tool_src == base else tool_src.name)
        else:
            tool_src = src
            spec, problem = check_dir(tool_src, src.resolve().name)
        if problem:
            raise ValueError(f"{src}: {problem}")
        tool_id = spec["id"]
        folder = tools_dir(root)
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / tool_id
        staging = Path(tempfile.mkdtemp(prefix=f".{tool_id}.new.", dir=folder))
        old = folder / f".{tool_id}.old"
        try:
            _copy_tree(tool_src, staging / tool_id)
            shutil.rmtree(old, ignore_errors=True)
            if dest.exists():
                os.replace(dest, old)
            os.replace(staging / tool_id, dest)
        except Exception:
            if old.exists() and not dest.exists():
                os.replace(old, dest)  # put the previous install back
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(old, ignore_errors=True)
    presets = spec.get("presets") or {}
    items = [{"id": name, "title": p.get("title") or name} for name, p in presets.items()] or None
    label = (spec.get("label") or tool_id[:6].upper()).strip()
    # the installed copy's icon, as an absolute path: the plugin just sets it (no path logic there)
    icon = (dest / spec["icon"]).resolve() if spec.get("icon") else None
    button = buttons.define(tool_id, label, title=spec["title"], definition=f"tool:{tool_id}",
                            items=items, icon=icon, root=root)
    return {"id": tool_id, "dir": str(dest), "button": str(button),
            "items": [it["id"] for it in items or []], "reload": _reload(reload)}


def uninstall(tool_id: str, root: str | Path | None = None, reload: bool = True) -> bool:
    """Remove the tool's button manifest and its directory; True if anything was removed."""
    if not ID_RE.fullmatch(tool_id or ""):
        raise ValueError("tool id must match ^[a-z][a-z0-9_]{0,39}$")
    removed = buttons.remove(tool_id, root=root)
    dest = tools_dir(root) / tool_id
    if dest.is_dir():
        shutil.rmtree(dest)
        removed = True
    _reload(reload)
    return removed


def list_installed(root: str | Path | None = None) -> list[dict]:
    """Every directory under tools/ (sorted): ``{id, title, label, presets, dir, problem}``."""
    folder, out = tools_dir(root), []
    if not folder.is_dir():
        return out
    for d in sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith(".")):
        spec, problem = check_dir(d, d.name)
        spec = spec if isinstance(spec, dict) else {}
        presets = spec.get("presets") if isinstance(spec.get("presets"), dict) else {}
        out.append({"id": d.name, "title": spec.get("title"), "label": spec.get("label"),
                    "icon": spec.get("icon"), "presets": list(presets), "dir": str(d),
                    "problem": problem})
    return out


def export(tool_id: str, zip_path: str | Path, root: str | Path | None = None) -> Path:
    """Zip an installed tool with its directory as the top-level folder (installable as is)."""
    src = tools_dir(root) / tool_id
    if not src.is_dir():
        raise FileNotFoundError(f"no tool {tool_id!r} installed in {src.parent}")
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(src.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                zf.write(f, Path(tool_id) / f.relative_to(src))
    return zip_path


def run(client, tool_id: str, preset: str | None = None, params: dict | None = None,
        interactive: bool = False) -> dict:
    """Run an installed tool in the connected KLayout (``tool.run`` RPC)."""
    req = {"id": tool_id, "interactive": bool(interactive)}
    if preset is not None:
        req["preset"] = preset
    if params:
        req["params"] = params
    return client.call("tool.run", req, timeout=3600.0 if interactive else None)  # dialogs wait for a human


def _reload(do_reload: bool) -> dict | str:
    """Ask the running KLayout to re-sync its toolbar; a short status when it cannot."""
    if not do_reload:
        return "skipped (--no-reload): applied at the next buttons.reload RPC or KLayout start"
    from .client import KLinkClient

    try:
        with KLinkClient(connect_timeout=1.0).connect() as client:
            return buttons.reload(client)
    except Exception as exc:  # KLayout not running / old plugin without buttons.reload
        return (f"KLayout not reachable or not updated ({type(exc).__name__}: {exc}); "
                "the button will appear at the next KLayout start")


# ------------------------------------------------------------------ authoring pipeline --
# `klink tool new` (scaffold) -> `klink tool check` (check) -> install -> run -> `klink tool log`
# (read_log). Pure Python: nothing here imports pya.

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates" / "tool"
LABEL_MAX = 6  # the toolbar shows at most this many characters


def _code_title(title: str) -> str:
    """The title as it may appear inside a triple-quoted docstring."""
    return " ".join(title.replace("\\", "/").replace('"', "'").split())


def scaffold(tool_id: str, dest: str | Path = ".klink/tools", from_dir: str | Path | None = None,
             title: str | None = None, label: str | None = None, min_selected: int | None = None,
             macro: bool = False, icon: str | Path | None = None) -> dict:
    """Create ``<dest>/<tool_id>/`` with tool.json + run.py (+ README.md).

    ``icon`` (optional): an existing .png/.svg copied into the folder as ``icon.<ext>`` and set
    as tool.json ``"icon"``; without it the button is text only.

    Default run.py is the ``run(ctx, params)`` skeleton (``macro=True``: the plain-macro one);
    ``from_dir`` copies an existing tool folder instead and rewrites its ``id`` (and
    ``title``/``label``/``inputs.selection.min`` when given). Refuses a non-empty target.
    Returns ``{id, dir, files, form, check}`` (``check`` = :func:`check` on the result).
    ValueError / FileExistsError / FileNotFoundError with an actionable message."""
    if not isinstance(tool_id, str) or not ID_RE.fullmatch(tool_id):
        raise ValueError(f"tool id {tool_id!r} must match ^[a-z][a-z0-9_]{{0,39}}$ "
                         "(lowercase letter first, then lowercase letters, digits, _)")
    if min_selected is not None and (isinstance(min_selected, bool) or not isinstance(min_selected, int)
                                     or min_selected < 0):
        raise ValueError("min_selected must be an integer >= 0")
    if label is not None and not label.strip():
        raise ValueError("label must be a non-empty string")
    if title is not None and not title.strip():
        raise ValueError("title must be a non-empty string")
    if from_dir is not None and macro:
        raise ValueError("--from copies an existing run.py; drop --macro (or drop --from)")
    if icon is not None:
        icon = Path(icon)
        if icon.suffix.lower() not in ICON_EXTS:
            raise ValueError(f"icon {icon} must be a .png or .svg file")
        if not icon.is_file():
            raise FileNotFoundError(f"icon file not found: {icon}; pass an existing .png or .svg")
    target = Path(dest) / tool_id
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise FileExistsError(f"{target} already exists and is not empty; pick another id, "
                              f"or delete/rename {target} first")
    if from_dir is not None:
        src = Path(from_dir)
        if not (src / "tool.json").is_file():
            raise FileNotFoundError(f"{src} has no tool.json; --from needs an existing tool folder "
                                    "(e.g. example_template/tools/bool_region)")
        try:
            spec = json.loads((src / "tool.json").read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError(f"{src / 'tool.json'} is not valid JSON ({exc}); fix it or copy "
                             "another tool") from None
        if not isinstance(spec, dict):
            raise ValueError(f"{src / 'tool.json'} is not a JSON object")
        if target.exists():
            target.rmdir()  # empty (checked above); copytree wants to create it
        _copy_tree(src, target)
        files = sorted(str(p.relative_to(target)).replace("\\", "/") for p in target.rglob("*") if p.is_file())
    else:
        spec = json.loads((TEMPLATE_DIR / "tool.json").read_text(encoding="utf-8"))
        title = title or tool_id.replace("_", " ").capitalize()
        skeleton = (TEMPLATE_DIR / ("macro_skeleton.py" if macro else "run_skeleton.py")).read_text(encoding="utf-8")
        run_src = (skeleton.replace("__TITLE_REPR__", json.dumps(title))
                   .replace("__TITLE__", _code_title(title)))
        target.mkdir(parents=True, exist_ok=True)
        (target / "run.py").write_text(run_src, encoding="utf-8")
        form = "plain KLayout macro" if macro else "run(ctx, params)"
        (target / "README.md").write_text(
            f"# {tool_id}: {title}\n\n"
            f"A klink deterministic tool (`tool.json` + `run.py`, {form} form), scaffolded by "
            f"`klink tool new`. As scaffolded it counts the selected shapes per layer and changes "
            f"nothing; edit `run.py` (the contract is in its docstring) and declare params in "
            f"`tool.json`. Check it with `python -m klink.cli tool check {target.as_posix()}`, "
            f"install it with `python -m klink.cli tool install {target.as_posix()}`, run it with "
            f"`python -m klink.cli tool run {tool_id}` (select the input in KLayout first) and read "
            f"its runs with `python -m klink.cli tool log {tool_id}`.\n", encoding="utf-8")
        files = ["tool.json", "run.py", "README.md"]
    spec["id"] = tool_id
    if title is not None:
        spec["title"] = title
    if label is not None:
        spec["label"] = label.strip()
    elif from_dir is None:
        spec["label"] = tool_id[:LABEL_MAX].upper()
    if icon is not None:
        name = "icon" + icon.suffix.lower()
        shutil.copyfile(icon, target / name)
        spec["icon"] = name
        if name not in files:
            files.append(name)
    if min_selected is not None:
        inputs = spec.get("inputs") if isinstance(spec.get("inputs"), dict) else {}
        sel = inputs.get("selection") if isinstance(inputs.get("selection"), dict) else {}
        sel["min"] = min_selected
        inputs["selection"] = sel
        spec["inputs"] = inputs
    (target / "tool.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report = check(target)
    return {"id": tool_id, "dir": str(target), "files": files, "form": report["form"], "check": report}


# ---- check ----

def _spec_problems(spec, dirname: str) -> list[str]:
    """Every problem :func:`validate_tool` would report one at a time, collected at once."""
    if not isinstance(spec, dict):
        return ["tool.json is not a JSON object"]
    out = []
    tid = spec.get("id")
    if not isinstance(tid, str) or not ID_RE.fullmatch(tid):
        out.append(f"'id' must match ^[a-z][a-z0-9_]{{0,39}}$ (got {tid!r})")
    elif tid != dirname:
        out.append(f"'id' {tid!r} does not match the directory name {dirname!r} "
                   "(rename the folder or the id)")
    if not isinstance(spec.get("title"), str) or not spec["title"].strip():
        out.append("'title' must be a non-empty string")
    if spec.get("label") is not None and (not isinstance(spec["label"], str) or not spec["label"].strip()):
        out.append("'label' must be a non-empty string")
    if spec.get("description") is not None and not isinstance(spec["description"], str):
        out.append("'description' must be a string")
    if spec.get("icon") is not None and not icon_name_ok(spec["icon"]):
        out.append(ICON_REASON + f" (got {spec['icon']!r})")
    inputs = spec.get("inputs", {})
    if not isinstance(inputs, dict):
        out.append("'inputs' must be an object")
    else:
        for kind, idef in inputs.items():
            if kind != "selection":
                out.append(f"unknown input kind {kind!r} (supported: selection)")
            elif not isinstance(idef, dict):
                out.append("'inputs.selection' must be an object")
            else:
                for key in ("min_shapes", "min"):
                    n = idef.get(key, 0)
                    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
                        out.append(f"inputs.selection.{key} must be an integer >= 0")
    params = spec.get("params", {})
    good = {}
    if not isinstance(params, dict):
        out.append("'params' must be an object")
        params = {}
    for name, pdef in params.items():
        problem = _check_param(name, pdef)
        if problem:
            out.append(problem)
        else:
            good[name] = pdef
    for name, pdef in good.items():
        if "exclude" not in pdef:
            continue
        ex = pdef["exclude"]
        if not isinstance(ex, list) or not all(isinstance(n, str) for n in ex):
            out.append(f"params.{name}.exclude must be a list of layer param names")
            continue
        for other in ex:
            if other == name or other not in params or not isinstance(params[other], dict) \
                    or params[other].get("type") != "layer":
                out.append(f"params.{name}.exclude: {other!r} is not another layer param of this tool")
    presets = spec.get("presets", {})
    if not isinstance(presets, dict):
        out.append("'presets' must be an object")
        presets = {}
    for name, pre in presets.items():
        if not ID_RE.fullmatch(name):
            out.append(f"preset name {name!r} must match ^[a-z][a-z0-9_]{{0,39}}$")
        if not isinstance(pre, dict):
            out.append(f"presets.{name} must be an object")
            continue
        if pre.get("title") is not None and not isinstance(pre["title"], str):
            out.append(f"presets.{name}.title must be a string")
        if not isinstance(pre.get("params"), dict):
            out.append(f"presets.{name}.params must be an object")
            continue
        for key, value in pre["params"].items():
            if key not in params:
                out.append(f"presets.{name}: unknown param {key!r} (params: "
                           + (", ".join(params) or "none") + ")")
            elif key in good:
                try:
                    coerce(good[key], value)
                except ValueError as exc:
                    out.append(f"presets.{name}.params.{key}: {exc}")
    if spec.get("prompt", "missing") not in PROMPTS:
        out.append("'prompt' must be one of " + ", ".join(PROMPTS))
    first = validate_tool(spec, dirname)  # parity guard: the install-time reason is always listed
    if first is not None and not any(first in p or p in first for p in out):
        out.append(first)
    return out


def _call_name(func) -> str:
    """'sys.exit' / 'view.transaction' / 'exit' for a Call's func node."""
    import ast

    parts = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return ".".join(reversed(parts))


def _code_warnings(tree, form: str) -> list[str]:
    import ast

    out = []
    if form == "run":
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run")
        a = fn.args
        npos = len(a.posonlyargs) + len(a.args)
        required = npos - len(a.defaults)
        kw_required = [k.arg for k, d in zip(a.kwonlyargs, a.kw_defaults) if d is None]
        if required > 2 or (npos < 2 and a.vararg is None) or kw_required:
            out.append(f"run.py line {fn.lineno}: `def run(...)` must accept exactly two positional "
                       "arguments, `def run(ctx, params):` -- klink calls run(ctx, params)")
        for node in tree.body:
            if isinstance(node, ast.If) and "__main__" in ast.dump(node.test):
                out.append(f"run.py line {node.lineno}: this file defines a top-level `run`, so klink "
                           "calls run(ctx, params) and the `if __name__ == \"__main__\":` block never runs; "
                           "keep one form (move the macro body into run, or rename run)")
    else:
        for node in tree.body:
            names = []
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = [t.id for t in targets if isinstance(t, ast.Name)]
            elif isinstance(node, (ast.AsyncFunctionDef, ast.ClassDef)):
                names = [node.name]
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [al.asname or al.name for al in node.names]
            if "run" in names:
                out.append(f"run.py line {node.lineno}: a macro defines a top-level name `run` that is "
                           "not `def run(ctx, params)`; klink treats the file as a plain macro, so it is "
                           "never called -- write `def run(ctx, params):` or rename it")
    seen = set()
    for node in ast.walk(tree):
        hit = None
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            last = name.rsplit(".", 1)[-1]
            if last == "transaction":
                hit = ("transaction", f"run.py line {node.lineno}: `{name}(...)` -- klink already wraps "
                       "the whole run in ONE undo transaction (an exception rolls it back); remove "
                       "your own transaction")
            elif name in ("sys.exit", "exit", "quit") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and arg.value not in (0, None):
                    hit = (f"exit{node.lineno}", f"run.py line {node.lineno}: `{name}({arg.value!r})` "
                           "-- a non-zero exit is reported as a FAILED run and the edit rolled back; "
                           "raise ValueError(\"<what is wrong>; <what to do>\") for a user error")
        elif isinstance(node, (ast.Attribute, ast.Name)):
            name = node.attr if isinstance(node, ast.Attribute) else node.id
            if name in ("InputDialog", "MessageBox", "QInputDialog", "QMessageBox"):
                hit = (name, f"run.py line {node.lineno}: `{name}` -- klink asks the params declared "
                       "in tool.json in one form and shows errors itself; declare a param instead of "
                       "a dialog, raise ValueError for an error")
        if hit and hit[0] not in seen:
            seen.add(hit[0])
            out.append(hit[1])
    return out


def _resolve_check_target(target: str | Path, root: str | Path | None) -> Path | None:
    p = Path(target)
    if p.is_dir():
        return p
    if isinstance(target, str) and ID_RE.fullmatch(target):
        installed = tools_dir(root) / target
        if installed.is_dir():
            return installed
    return None


def check(target: str | Path, root: str | Path | None = None) -> dict:
    """Lint a tool folder (or an installed tool id) without KLayout; reports EVERY problem found.

    Returns ``{ok, dir, id, errors, warnings, form, params, presets, prompt}``: ``form`` is
    ``"run"`` (``def run(ctx, params)``), ``"macro"`` or None (run.py missing/unparsable);
    ``params`` a list of ``{name, type, default, required, from, exclude, allow_new, asked,
    title}``; ``presets`` a list of ``{name, title, params}``; ``icon`` the icon file's path
    (None without ``icon``). ``ok`` is ``not errors``."""
    import ast

    folder = _resolve_check_target(target, root)
    rep = {"ok": False, "dir": str(target), "id": None, "errors": [], "warnings": [], "form": None,
           "params": [], "presets": [], "prompt": None, "icon": None}
    if folder is None:
        rep["errors"].append(f"{target}: not a tool directory, nor an installed tool id "
                             f"(installed tools live in {tools_dir(root)})")
        return rep
    rep["dir"] = str(folder)
    dirname = folder.resolve().name
    spec = None
    tj = folder / "tool.json"
    if not tj.is_file():
        rep["errors"].append(f"missing tool.json in {folder} (minimal: "
                             '{"id": "' + dirname + '", "title": "My tool"})')
    else:
        try:
            spec = json.loads(tj.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            rep["errors"].append(f"tool.json line {exc.lineno} column {exc.colno}: invalid JSON ({exc.msg})")
        except (OSError, UnicodeDecodeError) as exc:
            rep["errors"].append(f"tool.json unreadable: {exc}")
    if spec is not None:
        rep["errors"].extend(_spec_problems(spec, dirname))
    if isinstance(spec, dict):
        rep["id"] = spec.get("id")
        if spec.get("icon") is not None and icon_name_ok(spec["icon"]):
            icon_path = folder / spec["icon"]
            rep["icon"] = str(icon_path)
            if not icon_path.is_file():
                rep["errors"].append(f"icon file not found: {icon_path} (put a .png/.svg there, "
                                     "or drop 'icon' for a text-only button)")
        prompt = spec.get("prompt", "missing")
        rep["prompt"] = prompt
        params = spec.get("params") if isinstance(spec.get("params"), dict) else {}
        presets = spec.get("presets") if isinstance(spec.get("presets"), dict) else {}
        for name, p in params.items():
            p = p if isinstance(p, dict) else {}
            asked = {"never": "never", "always": "always"}.get(prompt, "if missing")
            rep["params"].append({"name": name, "type": p.get("type"), "default": p.get("default"),
                                  "required": "default" not in p, "from": p.get("from"),
                                  "exclude": p.get("exclude"), "allow_new": p.get("allow_new"),
                                  "asked": asked, "title": p.get("title")})
        for name, pre in presets.items():
            pre = pre if isinstance(pre, dict) else {}
            rep["presets"].append({"name": name, "title": pre.get("title") or name,
                                   "params": pre.get("params") if isinstance(pre.get("params"), dict) else {}})
        label = spec.get("label")
        if isinstance(label, str) and len(label.strip()) > LABEL_MAX:
            rep["warnings"].append(f"label {label!r} is {len(label.strip())} characters; the toolbar "
                                   f"shows only the first {LABEL_MAX} ({label.strip()[:LABEL_MAX]!r})")
        if prompt == "never":
            for row in rep["params"]:
                if not row["required"] or row["from"]:
                    continue
                pinned = bool(presets) and all(isinstance(pre, dict) and isinstance(pre.get("params"), dict)
                                               and row["name"] in pre["params"] for pre in presets.values())
                if not pinned:
                    rep["warnings"].append(
                        f"param {row['name']!r} has no default and \"prompt\": \"never\": a button click "
                        f"can never ask for it, so it fails with ERR_TOOL_PARAMS; give it a default, pin "
                        f"it in every preset, or use \"prompt\": \"missing\"")
    rp = folder / "run.py"
    if not rp.is_file():
        rep["errors"].append(f"missing run.py in {folder} (scaffold one with: "
                             "python -m klink.cli tool new <id>)")
    else:
        try:
            src = rp.read_text(encoding="utf-8-sig")
            tree = ast.parse(src, str(rp))
        except SyntaxError as exc:
            rep["errors"].append(f"run.py line {exc.lineno}: SyntaxError: {exc.msg}")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            rep["errors"].append(f"run.py unreadable: {type(exc).__name__}: {exc}")
        else:
            has_run = any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body)
            rep["form"] = "run" if has_run else "macro"
            rep["warnings"].extend(_code_warnings(tree, rep["form"]))
    rep["ok"] = not rep["errors"]
    return rep


# ---- log ----

def journals_dir(root: str | Path | None = None) -> Path:
    """Where the plugin journals tool runs: ``<registry_root>/journals/<session>.tools.jsonl``."""
    if root is not None:
        return Path(root) / "journals"
    from .mcp.session_registry import default_registry_root

    return default_registry_root() / "journals"


def read_log(tool_id: str | None = None, n: int | None = 5, root: str | Path | None = None) -> list[dict]:
    """Tool run records of every session (rotated ``.jsonl.1`` files included), oldest first,
    filtered by ``tool_id`` when given, the newest ``n`` kept (``n`` None or <= 0: all).
    Each record gains ``session`` (its session id, else the journal file's stem)."""
    folder = journals_dir(root)
    records = []
    if folder.is_dir():
        files = sorted(folder.glob("*.tools.jsonl")) + sorted(folder.glob("*.tools.jsonl.1"))
        for f in files:
            stem = f.name.split(".tools.jsonl")[0]
            try:
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                if tool_id is not None and rec.get("tool") != tool_id:
                    continue
                rec.setdefault("session", rec.get("session_id") or stem)
                records.append(rec)

    def key(r):
        ts = r.get("ts")
        seq = r.get("seq")
        return (ts if isinstance(ts, (int, float)) else 0.0, seq if isinstance(seq, int) else 0)
    records.sort(key=key)
    if n is not None and n > 0:
        records = records[-n:]
    return records

"""Custom KLayout toolbar buttons: write/remove/list the manifests the plugin reads.

A button is one JSON manifest at ``<registry_root>/buttons/<id>.json``, optionally
with ``items`` (a dropdown of sub-functions) and/or ``icon`` (absolute path to a .png/.svg
shown above the label). The klink plugin inside KLayout turns
each manifest into a toolbar action (``buttons.reload``
RPC, and at every KLayout start); a click journals a trigger record and broadcasts
a ``button_triggered`` event. Nothing is executed. See ``docs/BUTTONS.md``.

The validation rules here MUST stay identical to
``klink_plugin/python/klink_server/buttons.py`` (unit-tested for parity).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
ITEM_LABEL_REJECTED = "item 'label' is no longer supported; the entry's 'title' is shown on the parent"
ICON_EXTS = (".png", ".svg")  # KLayout's Qt renders both on the toolbar
ICON_REASON = "'icon' must be an absolute path to a .png or .svg file"
ITEM_ICON_REJECTED = "dropdown entries take no icon; set 'icon' on the button itself"


def buttons_dir(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root) / "buttons"
    from .mcp.session_registry import default_registry_root  # same resolver as the plugin

    return default_registry_root() / "buttons"


def validate(spec, stem: str) -> str | None:
    """None when the manifest is usable, else a one-line reason (same rules as the plugin)."""
    if not isinstance(spec, dict):
        return "not a JSON object"
    bid = spec.get("id")
    if not isinstance(bid, str) or not ID_RE.fullmatch(bid):
        return "'id' must match ^[a-z][a-z0-9_]{0,39}$"
    if bid != stem:
        return f"'id' {bid!r} does not match the file name {stem!r}"
    if not isinstance(spec.get("label"), str) or not spec["label"].strip():
        return "'label' must be a non-empty string"
    for key in ("title", "definition"):
        if spec.get(key) is not None and not isinstance(spec[key], str):
            return f"'{key}' must be a string"
    if spec.get("promote") is not None and not isinstance(spec["promote"], bool):
        return "'promote' must be a boolean"
    icon = spec.get("icon")
    if icon is not None and (not isinstance(icon, str) or not Path(icon).is_absolute()
                             or Path(icon).suffix.lower() not in ICON_EXTS):
        return ICON_REASON
    items = spec.get("items")
    if items is None:
        return None
    if not isinstance(items, list):
        return "'items' must be a list"
    seen = set()
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            return f"items[{i}] is not a JSON object"
        sid = item.get("id")
        if not isinstance(sid, str) or not ID_RE.fullmatch(sid):
            return f"items[{i}].id must match ^[a-z][a-z0-9_]{{0,39}}$"
        if sid in seen:
            return f"duplicate item id {sid!r}"
        seen.add(sid)
        if "label" in item:
            return ITEM_LABEL_REJECTED
        if "icon" in item:
            return f"items[{i}]: " + ITEM_ICON_REJECTED
        for key in ("title", "definition"):
            if item.get(key) is not None and not isinstance(item[key], str):
                return f"items[{i}].{key} must be a string"
    return None


def _write(spec: dict, root) -> Path:
    button_id = spec["id"]
    problem = validate(spec, button_id)
    if problem:
        raise ValueError(f"button {button_id!r}: {problem}")
    folder = buttons_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{button_id}.json"
    tmp = folder / f".{button_id}.json.tmp"
    tmp.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def define(button_id: str, label: str, title: str | None = None,
           definition: str | None = None, items: list[dict] | None = None,
           promote: bool | None = None, icon: str | Path | None = None,
           root: str | Path | None = None) -> Path:
    """Write (or overwrite) one manifest atomically; returns its path. ValueError when invalid.

    ``items`` (optional) makes the button a dropdown: ``[{"id", "title"?,
    "definition"?}]`` in menu order (an entry has one name, its ``title``). Written as given (no defaults filled in on disk).
    ``promote`` is written only when given (plugin default: true, the parent button shows
    the current item's title verbatim).
    ``icon`` (optional): a .png/.svg file shown on the toolbar with the label under it; written
    as its absolute path, and the file must exist now (ValueError naming the path otherwise).
    Without it the button is text only, exactly as before.
    """
    spec = {"id": button_id, "label": label}
    if icon is not None:
        icon_path = Path(icon).expanduser().resolve()
        if not icon_path.is_file():
            raise ValueError(f"button {button_id!r}: icon file not found: {icon_path} "
                             "(pass an existing .png or .svg file)")
        spec["icon"] = str(icon_path)
    if title is not None:
        spec["title"] = title
    if definition is not None:
        spec["definition"] = definition
    if items is not None:
        spec["items"] = items
    if promote is not None:
        spec["promote"] = promote
    return _write(spec, root)


def _read(button_id: str, root) -> dict:
    path = buttons_dir(root) / f"{button_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"no manifest for button {button_id!r} in {path.parent}; "
            f"create it first: klink button add {button_id} --label <TEXT>")
    spec = json.loads(path.read_text(encoding="utf-8"))
    problem = validate(spec, button_id)
    if problem:
        raise ValueError(f"button {button_id!r}: existing manifest is invalid ({problem}); "
                         f"fix or rewrite {path}")
    return spec


def set_item(button_id: str, item_id: str, title: str | None = None,
             definition: str | None = None,
             root: str | Path | None = None) -> Path:
    """Add or replace one dropdown entry (read-modify-write; replaced in place, else appended).

    FileNotFoundError when the button manifest does not exist; ValueError when invalid.
    """
    spec = _read(button_id, root)
    item = {"id": item_id}
    if title is not None:
        item["title"] = title
    if definition is not None:
        item["definition"] = definition
    items = list(spec.get("items") or [])
    for i, existing in enumerate(items):
        if existing.get("id") == item_id:
            items[i] = item
            break
    else:
        items.append(item)
    spec["items"] = items
    return _write(spec, root)


def remove_item(button_id: str, item_id: str, root: str | Path | None = None) -> bool:
    """Drop one dropdown entry; True if it existed. FileNotFoundError when the button is missing."""
    spec = _read(button_id, root)
    items = list(spec.get("items") or [])
    kept = [it for it in items if it.get("id") != item_id]
    if len(kept) == len(items):
        return False
    spec["items"] = kept
    _write(spec, root)
    return True


def remove(button_id: str, root: str | Path | None = None) -> bool:
    """Delete the manifest; True if a file was deleted."""
    path = buttons_dir(root) / f"{button_id}.json"
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def list_defined(root: str | Path | None = None) -> list[dict]:
    """Every manifest on disk (sorted by file name), each with ``file`` and ``problem`` (None if valid)."""
    folder = buttons_dir(root)
    out = []
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.json")):
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
            problem = validate(spec, path.stem)
        except (OSError, ValueError) as exc:
            spec, problem = {}, f"unreadable: {exc}"
        entry = dict(spec) if isinstance(spec, dict) else {}
        entry.update({"file": str(path), "problem": problem})
        out.append(entry)
    return out


def reload(client) -> dict:
    """Ask the running KLayout to sync its toolbar to the manifests on disk."""
    return client.call("buttons.reload", {})

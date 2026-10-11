"""Custom toolbar buttons: thin manifest -> pya.Action sync, click -> journal + event.

One JSON manifest per button under ``<registry_root>/buttons/<id>.json``::

    {"id": "label_modules", "label": "LBL", "title": "Label all modules",
     "definition": "<opaque string, echoed in the trigger record>", "promote": true,
     "icon": "<registry root>/tools/<id>/icon.png",
     "items": [{"id": "union", "title": "Union", "definition": "<opaque>"}]}

``items`` is optional: absent/empty -> a flat toolbar action; non-empty -> a
toolbar button with a dropdown, one entry per item in file order (an item's
``title`` defaults to its id, its ``definition`` to the button's; an item has
no ``label`` of its own -- one name, its ``title``; and no ``icon``). ``icon`` is optional: an
absolute path to a .png/.svg set as ``action.icon`` (the label stays as ``icon_text``); a
missing file only logs a warning and the button stays text-only; without ``icon`` nothing
about the button changes. A dropdown works like a tool group: the main part of the
button runs the CURRENT item, a dropdown entry runs that item and makes it
current (the parent then shows that item's ``title`` verbatim as its text unless
``promote`` is false). The current item is restored at start from the last click record with
an item in this session's journal; the plugin writes no state file.

The plugin only creates/updates/deletes toolbar actions to match the files on
disk (dropdown entries are added, retitled and removed one at a time) and, on
click, journals a trigger record to
``<registry_root>/journals/<session_id>.buttons.jsonl`` (monotonic ``seq``)
BEFORE broadcasting it on the ``button_triggered`` event channel. It never opens
``definition`` and never executes anything; whoever listens decides what a
click means -- except ``definition: "tool:<id>"``, which runs that installed tool
(``tools.run_tool``, see ``docs/TOOLS.md``) after the journal + event. Manifests are written by the ``klink`` package (``klink button``).

Stdlib only; runs on KLayout's GUI thread.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .log import get_logger
from .session_registry import default_registry_root

_log = get_logger("buttons")

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
LABEL_MAX = 6  # button label only; an item's title goes up on the parent uncut
ITEM_LABEL_REJECTED = "item 'label' is no longer supported; the entry's 'title' is shown on the parent"
ICON_EXTS = (".png", ".svg")  # KLayout's Qt renders both on the toolbar
ICON_REASON = "'icon' must be an absolute path to a .png or .svg file"
ITEM_ICON_REJECTED = "dropdown entries take no icon; set 'icon' on the button itself"
ITEM_PREFIX = "klink_button_"
# Inserted at position "@toolbar.end", but the item's path is "@toolbar.<name>"; a delete of
# "@toolbar.end.<name>" is a silent no-op (verified live on KLayout 0.30). Dropdown entries
# are inserted at "@toolbar.<name>.end" and live at "@toolbar.<name>.<sub_id>"; deleting
# "@toolbar.<name>" deletes the parent and all of its entries.
MENU_PATH = "@toolbar."
_MAX_JOURNAL_BYTES = 5_000_000

# id -> {"action": pya.Action (kept alive), "manifest": dict,
#        "items": {sub_id: pya.Action}, "current": sub_id}   ("items"/"current": dropdowns only)
_installed: dict = {}


def buttons_dir(root: str | Path | None = None) -> Path:
    return (Path(root) if root is not None else default_registry_root()) / "buttons"


def validate_manifest(spec, stem: str) -> str | None:
    """None when usable, else a one-line reason."""
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


def _normalize(spec: dict) -> dict:
    out = {"id": spec["id"], "label": spec["label"].strip()[:LABEL_MAX],
           "title": spec.get("title") or spec["id"], "definition": spec.get("definition")}
    if spec.get("items"):
        out["items"] = [{"id": it["id"], "title": it.get("title") or it["id"],
                         "definition": it["definition"] if it.get("definition") is not None
                         else out["definition"]} for it in spec["items"]]
        out["promote"] = spec.get("promote") is not False
    if spec.get("icon"):
        out["icon"] = spec["icon"]  # absolute path (checked by validate_manifest); file read at apply
    return out


def load_manifests(root: str | Path | None = None) -> tuple[list[dict], list[dict]]:
    """(valid manifests sorted by id, skipped [{"file", "reason"}])."""
    folder = buttons_dir(root)
    valid, skipped = [], []
    if not folder.is_dir():
        return valid, skipped
    for path in sorted(folder.glob("*.json")):
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
            problem = validate_manifest(spec, path.stem)
        except (OSError, ValueError) as exc:
            problem = f"unreadable: {exc}"
        if problem:
            _log.warning("button manifest %s skipped: %s", path.name, problem)
            skipped.append({"file": path.name, "reason": problem})
            continue
        valid.append(_normalize(spec))
    valid.sort(key=lambda m: m["id"])
    return valid, skipped


def _apply_texts(action, manifest: dict) -> None:
    action.title = manifest["title"]
    action.icon_text = manifest["label"]
    action.tool_tip = manifest["title"]


def _apply_icon(action, manifest: dict, had_icon: bool = False) -> None:
    """Set the action's icon from ``manifest["icon"]``; a manifest without one never touches it
    (except to clear an icon an earlier version of the manifest set). A missing/unreadable icon
    file is a warning: the button stays a text button. Never raises."""
    icon = manifest.get("icon")
    try:
        if icon:
            if Path(icon).is_file():
                action.icon = icon
                return
            _log.warning("button %s: icon file %s not found; showing the text button",
                         manifest["id"], icon)
        if had_icon:
            action.icon = ""
    except Exception as exc:
        _log.warning("button %s: icon %s not applied: %s", manifest["id"], icon, exc)


def _parent_texts(manifest: dict, sid: str | None) -> tuple:
    """(title, icon_text, tool_tip) of a dropdown's parent while item ``sid`` is current."""
    item = next((it for it in manifest["items"] if it["id"] == sid), None)
    if item is None or not manifest.get("promote", True):
        return manifest["title"], manifest["label"], manifest["title"]
    return manifest["title"], item["title"], f"{manifest['title']}: {item['title']}"


def _apply_parent_texts(action, manifest: dict, sid: str | None) -> None:
    action.title, action.icon_text, action.tool_tip = _parent_texts(manifest, sid)


def _delete(menu, path: str, quiet: bool = False) -> None:
    try:
        menu.delete_item(path)
    except Exception as exc:
        if not quiet:
            _log.warning("delete_item(%s) failed: %s", path, exc)


def _insert_sub(menu, pya, bid: str, item: dict):
    action = pya.Action()
    action.title = item["title"]
    sid = item["id"]
    action.on_triggered = lambda bid=bid, sid=sid: _select(bid, sid)
    menu.insert_item(f"{MENU_PATH}{ITEM_PREFIX}{bid}.end", sid, action)
    return action


def _select(bid: str, sid: str) -> None:
    """Dropdown entry clicked: run it, then promote it to the parent button. Never raises."""
    _fire(bid, sid, via="item")
    try:
        entry = _installed.get(bid)
        if entry is not None and "items" in entry and sid in entry["items"]:
            entry["current"] = sid
            _apply_parent_texts(entry["action"], entry["manifest"], sid)
    except Exception as exc:
        _log.error("button %s: promoting item %s failed: %s", bid, sid, exc)


def _fire_parent(bid: str) -> None:
    """Main part of a dropdown's toolbar button clicked: run the current item."""
    entry = _installed.get(bid)
    if entry is None or not entry.get("current"):
        _log.warning("button %s: parent clicked but no current item", bid)
        return
    _fire(bid, entry["current"], via="parent")


def _create(menu, pya, manifest: dict, restored: dict | None = None) -> dict:
    """Create the toolbar entry for one manifest (flat action or dropdown); returns the entry.

    ``restored`` maps button id -> last clicked item id (from the journal); a dropdown starts
    with that item current when it still exists, else with its first item.
    """
    bid = manifest["id"]
    item_id = ITEM_PREFIX + bid
    _delete(menu, MENU_PATH + item_id, quiet=True)  # leftover from a previous macro run
    if "items" not in manifest:
        action = pya.Action()
        _apply_texts(action, manifest)
        _apply_icon(action, manifest)
        action.on_triggered = lambda bid=bid, m=manifest: _on_trigger(_installed.get(bid, {}).get("manifest", m))
        menu.insert_item("@toolbar.end", item_id, action)
        return {"action": action, "manifest": manifest}
    menu.insert_menu("@toolbar.end", item_id, manifest["title"])
    parent = menu.action(MENU_PATH + item_id)
    ids = [it["id"] for it in manifest["items"]]
    cur = (restored or {}).get(bid)
    if cur not in ids:
        cur = ids[0]
    _apply_parent_texts(parent, manifest, cur)
    _apply_icon(parent, manifest)
    parent.on_triggered = lambda bid=bid: _fire_parent(bid)
    subs = {it["id"]: _insert_sub(menu, pya, bid, it) for it in manifest["items"]}
    return {"action": parent, "manifest": manifest, "items": subs, "current": cur}


def _sync_items(menu, pya, entry: dict, new: dict) -> dict:
    """Bring an installed dropdown to ``new`` touching only the entries that changed."""
    bid = new["id"]
    base = f"{MENU_PATH}{ITEM_PREFIX}{bid}."
    old = entry["manifest"]
    detail = {"items_added": [], "items_removed": [], "items_updated": []}
    old_cur = entry.get("current")
    old_items = {it["id"]: it for it in old["items"]}
    new_ids = [it["id"] for it in new["items"]]
    new_set = set(new_ids)
    for sid in [s for s in old_items if s not in new_set]:
        _delete(menu, base + sid)
        entry["items"].pop(sid, None)
        detail["items_removed"].append(sid)
    survivors = [s for s in old_items if s in new_set]  # menu order after the deletes
    added = [s for s in new_ids if s not in old_items]
    for it in new["items"]:
        sid = it["id"]
        if sid in old_items and old_items[sid] != it:
            detail["items_updated"].append(sid)
        elif sid not in old_items:
            detail["items_added"].append(sid)
    if survivors + added == new_ids:
        # Incremental path: retitle in place, append new entries at ".end".
        for it in new["items"]:
            sid = it["id"]
            if sid in old_items:
                entry["items"][sid].title = it["title"]
            else:
                entry["items"][sid] = _insert_sub(menu, pya, bid, it)
    else:
        # The menu API only appends at ".end" (no position by sub-id), so when the surviving
        # entries changed their relative order, or a new entry goes between existing ones,
        # delete and re-insert all entries in file order. The parent action is kept.
        for sid in survivors:
            _delete(menu, base + sid)
        entry["items"] = {it["id"]: _insert_sub(menu, pya, bid, it) for it in new["items"]}
        detail["items_reordered"] = True
    cur = old_cur if old_cur in new_set else new_ids[0]  # current item removed -> first item
    if _parent_texts(old, old_cur) != _parent_texts(new, cur):
        _apply_parent_texts(entry["action"], new, cur)
    if old.get("icon") != new.get("icon"):
        _apply_icon(entry["action"], new, had_icon="icon" in old)
    entry["current"] = cur
    entry["manifest"] = new
    return {k: v for k, v in detail.items() if v}


def install_all(menu, pya, root: str | Path | None = None) -> dict:
    """Idempotently sync the toolbar to the manifests on disk."""
    manifests, skipped = load_manifests(root)
    report = {"installed": [], "updated": [], "removed": [], "skipped": skipped, "items": {}}
    on_disk = {m["id"] for m in manifests}
    journal_items = []  # read at most once per sync, and only if a dropdown gets created

    def restored() -> dict:
        if not journal_items:
            journal_items.append(_last_items_from_journal(_session_id()))
        return journal_items[0]

    for bid in [b for b in _installed if b not in on_disk]:
        _delete(menu, MENU_PATH + ITEM_PREFIX + bid)  # a dropdown's entries go with it
        del _installed[bid]
        report["removed"].append(bid)
    for manifest in manifests:
        bid = manifest["id"]
        entry = _installed.get(bid)
        if entry is not None:
            if entry["manifest"] == manifest:
                continue
            if ("items" in entry) != ("items" in manifest):
                # flat <-> dropdown: different kind of toolbar entry, recreate it
                _installed[bid] = _create(menu, pya, manifest,
                                          restored() if "items" in manifest else None)
            elif "items" in manifest:
                detail = _sync_items(menu, pya, entry, manifest)
                if detail:
                    report["items"][bid] = detail
            else:
                _apply_texts(entry["action"], manifest)
                _apply_icon(entry["action"], manifest, had_icon="icon" in entry["manifest"])
                entry["manifest"] = manifest
            report["updated"].append(bid)
            continue
        _installed[bid] = _create(menu, pya, manifest, restored() if "items" in manifest else None)
        report["installed"].append(bid)
    report["buttons"] = current()
    if report["installed"] or report["updated"] or report["removed"]:
        _log.info("buttons synced: +%s ~%s -%s", report["installed"], report["updated"], report["removed"])
    return report


def current() -> list[dict]:
    out = []
    for b in sorted(_installed):
        m = dict(_installed[b]["manifest"])
        if "items" in m:
            m["items"] = [dict(it) for it in m["items"]]
            m["current"] = _installed[b].get("current")
        out.append(m)
    return out


# ---------------------------------------------------------------- journal --
def journal_path(session_id: str) -> Path:
    return default_registry_root() / "journals" / f"{session_id}.buttons.jsonl"


def _last_seq(path: Path) -> int:
    last = 0
    for p in (path.with_suffix(".jsonl.1"), path):
        if not p.exists():
            continue
        with p.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    last = max(last, int(json.loads(line).get("seq", 0)))
                except Exception:
                    continue
    return last


def journal_append(session_id: str, record: dict) -> int:
    """Append one trigger record; returns its seq. Raises on write failure."""
    path = journal_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    seq = _last_seq(path) + 1
    if path.exists() and path.stat().st_size > _MAX_JOURNAL_BYTES:
        path.replace(path.with_suffix(".jsonl.1"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"seq": seq, **record}, ensure_ascii=False) + "\n")
    return seq


def _last_items_from_journal(session_id: str) -> dict:
    """{button id: item id of its last click record with a non-null item} (rotated file first)."""
    path = journal_path(session_id)
    out = {}
    for p in (path.with_suffix(".jsonl.1"), path):
        if not p.exists():
            continue
        try:
            with p.open("r", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(rec, dict) and isinstance(rec.get("button"), str) \
                            and isinstance(rec.get("item"), str):
                        out[rec["button"]] = rec["item"]
        except OSError as exc:
            _log.warning("reading %s failed: %s", p, exc)
    return out


def _server():
    try:
        from .server import instance
        return instance()
    except Exception:
        return None


def _session_id(srv=None) -> str:
    return getattr(srv if srv is not None else _server(), "session_id", None) or "unknown"


def _fire(bid: str, sid: str, via: str | None = None) -> None:
    """Dropdown item run: resolve the button's CURRENT manifest at click time."""
    entry = _installed.get(bid)
    manifest = entry["manifest"] if entry else {"id": bid, "title": bid, "definition": None}
    _on_trigger(manifest, sid, via)


def _on_trigger(manifest: dict, item: str | None = None, via: str | None = None) -> None:
    """Qt callback: journal first, then broadcast. Never raises.

    ``via`` is "parent" (main part of a dropdown button), "item" (a dropdown entry) or None
    (flat button).
    """
    try:
        srv = _server()
        session_id = _session_id(srv)
        source = manifest
        if item is not None:
            source = next((it for it in manifest.get("items") or [] if it.get("id") == item), manifest)
        record = {"button": manifest["id"], "item": item, "via": via,
                  "definition": source.get("definition"),
                  "title": source.get("title"), "session_id": session_id, "ts": time.time()}
        record = {"seq": journal_append(session_id, record), **record}
        if srv is not None and getattr(srv, "events", None) is not None:
            srv.events.emit("button_triggered", record)
    except Exception as exc:
        _log.error("button %s trigger failed: %s", manifest.get("id"), exc)
    _dispatch_tool(manifest, item)


def _dispatch_tool(manifest: dict, item: str | None) -> None:
    """``definition: "tool:<id>"`` -> run that tool KLayout-native (``tools.run_tool``, the item
    as preset name). Any other definition: nothing (journal + event only). Never raises."""
    definition = manifest.get("definition")
    if not isinstance(definition, str) or not definition.startswith("tool:"):
        return
    try:
        from . import tools
        tools.run_tool(definition[len("tool:"):], preset=item, interactive=True)
    except Exception as exc:
        _log.error("button %s: tool %s failed: %s", manifest.get("id"), definition, exc)
        if getattr(exc, "code", None) == "ERR_TOOL_INPUT":
            return  # run_tool already told the user what to select
        try:
            import pya
            text = getattr(exc, "message", None) or str(exc)
            if getattr(exc, "hint", ""):
                text += "\n\n" + exc.hint
            pya.MessageBox.critical(manifest.get("title") or manifest.get("id"), text, pya.MessageBox.Ok)
        except Exception as box_exc:
            _log.error("button %s: error dialog failed: %s", manifest.get("id"), box_exc)

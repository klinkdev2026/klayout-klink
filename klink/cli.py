"""klink command-line interface.

    klink init <dir>        scaffold a new klink user project
    klink update [dir]      refresh the project's bundled example_template/ from
                            the installed package (never touches your own files)
    klink plugin install    copy the bundled KLayout plugin into KLayout's salt/
    klink plugin status     compare installed plugin vs the bundled one
    klink doctor            preflight check (interpreter, plugin, handshake)

The `init` scaffold is the bundled project template (``klink/templates/project``)
so a pure ``pip install klayout-klink`` user can start a project with no repo
checkout: ``pip install klayout-klink`` -> ``klink init my-chip`` -> open it with
an agent and describe what you are building. ``klink plugin install`` closes the
same gap for the KLayout side: the salt plugin ships inside the wheel
(``klink/plugin_payload/``), so no repository clone is needed there either.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

from ._meta import __version__


def _template_dir() -> Path:
    """Path to the bundled project template. A pip-installed wheel unpacks to a
    real filesystem path, so a plain Path works."""
    return Path(__file__).resolve().parent / "templates" / "project"


def _plugin_payload_dir() -> Path:
    """Path to the bundled KLayout salt plugin (mirror of the repo's
    klink_plugin/, kept in sync by tools/sync_plugin_payload.py)."""
    return Path(__file__).resolve().parent / "plugin_payload" / "klink_plugin"


def _default_salt_dir() -> Path:
    """KLayout's per-user salt directory. KLayout reads packages from
    ``<KLayout home>/salt``; the home is ``$KLAYOUT_HOME`` when set, else
    ``%USERPROFILE%\\KLayout`` on Windows and ``~/.klayout`` elsewhere."""
    env_home = os.environ.get("KLAYOUT_HOME")
    if env_home:
        return Path(env_home) / "salt"
    if sys.platform.startswith("win"):
        return Path.home() / "KLayout" / "salt"
    return Path.home() / ".klayout" / "salt"


def _grain_version(plugin_dir: Path) -> str | None:
    """Read <version> from a salt package's grain.xml (None if unreadable)."""
    grain = plugin_dir / "grain.xml"
    try:
        text = grain.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"<version>\s*([^<]+?)\s*</version>", text)
    return m.group(1) if m else None


def _grain_is_klink(plugin_dir: Path) -> bool:
    grain = plugin_dir / "grain.xml"
    try:
        return "<name>klink</name>" in grain.read_text(encoding="utf-8")
    except OSError:
        return False


def plugin_status(salt_dir: Path | None = None) -> int:
    payload = _plugin_payload_dir()
    salt = (salt_dir or _default_salt_dir()).resolve()
    installed = salt / "klink_plugin"

    bundled_ver = _grain_version(payload) if payload.is_dir() else None
    print(f"bundled plugin : {bundled_ver or 'NOT FOUND'}"
          f"  ({payload if payload.is_dir() else 'payload missing from this install'})")
    if not installed.is_dir():
        print(f"installed      : none  ({installed})")
        print("Run `klink plugin install` to install it, then restart KLayout.")
        return 0
    inst_ver = _grain_version(installed)
    print(f"installed      : {inst_ver or 'unknown version'}  ({installed})")
    if bundled_ver and inst_ver and bundled_ver != inst_ver:
        print(f"MISMATCH: run `klink plugin install` to update "
              f"{inst_ver} -> {bundled_ver}, then restart KLayout.")
    elif bundled_ver and inst_ver:
        print("up to date.")
    return 0


def plugin_install(salt_dir: Path | None = None, *, force: bool = False) -> int:
    payload = _plugin_payload_dir()
    if not payload.is_dir():
        print(f"error: bundled plugin payload not found at {payload}\n"
              "This install is missing package data; reinstall with "
              "`pip install --force-reinstall klayout-klink`.", file=sys.stderr)
        return 1

    salt = (salt_dir or _default_salt_dir()).resolve()
    target = salt / "klink_plugin"

    if target.exists():
        # Only auto-replace something that is recognisably OUR salt package;
        # anything else needs an explicit --force so we never clobber foreign
        # or hand-modified content silently.
        if not (_grain_is_klink(target) or force):
            print(f"error: {target} exists but does not look like the klink "
                  "plugin (no klink grain.xml). Re-run with --force to replace "
                  "it anyway.", file=sys.stderr)
            return 1
        old = _grain_version(target)
        shutil.rmtree(target)
        print(f"replaced existing plugin ({old or 'unknown version'}).")

    salt.mkdir(parents=True, exist_ok=True)
    shutil.copytree(payload, target,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    ver = _grain_version(target)
    print(f"Installed klink plugin {ver or ''} into {target}".rstrip())
    print("Restart KLayout to load it (a running KLayout keeps the old code "
          "until restarted). It listens on 127.0.0.1:8765 (next free port up "
          "to 8799 if busy). Verify with `python -m klink.doctor`.")
    return 0


def init(target: str) -> int:
    dst = Path(target).resolve()
    if dst.exists() and any(dst.iterdir()):
        print(f"error: {dst} already exists and is not empty", file=sys.stderr)
        return 1
    src = _template_dir()
    if not src.is_dir():
        print(f"error: bundled project template not found at {src}", file=sys.stderr)
        return 1

    shutil.copytree(src, dst, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # `gitignore` is stored without the leading dot so it survives packaging;
    # restore it in the generated project.
    gi = dst / "gitignore"
    if gi.exists():
        gi.rename(dst / ".gitignore")
    # Empty working dirs are not carried as package data; create them here.
    for sub in ("custom_devices", "runs", "specs", "out"):
        (dst / sub).mkdir(exist_ok=True)

    print(f"Created klink project in {dst}")
    print("Next:")
    print(f"  1. cd {dst}")
    print("  2. open it with your agent (Claude Code / Codex) and describe what")
    print("     you build; it scaffolds pdk.py + custom_devices/ from the matching recipe.")
    print("  3. see README.md and recipes/README.md")
    return 0


def update(target: str, *, dry_run: bool = False) -> int:
    """Refresh a project's bundled ``example_template/`` from the installed
    package. This is the copy-and-adapt reference set (the starter demos); it is
    package-owned, so it is safe to overwrite. Everything YOU own -- ``pdk.py``,
    ``custom_devices/``, ``runs/``, ``.klink/`` (session + net tables), ``out/``, ``specs/``
    -- is never touched. Use this after upgrading klink so a `klink init` project
    picks up new/fixed starters without a destructive re-init."""
    dst = Path(target).resolve()
    src_examples = _template_dir() / "example_template"
    if not src_examples.is_dir():
        print(f"error: bundled template has no example_template at {src_examples}",
              file=sys.stderr)
        return 1
    dst_examples = dst / "example_template"
    # Only update something that actually looks like a klink project, so we never
    # scribble example_template/ into an unrelated directory.
    if not (dst_examples.is_dir() or (dst / "pdk.py").exists() or (dst / ".klink").is_dir()):
        print(f"error: {dst} does not look like a klink project (no example_template/, "
              f"pdk.py, or .klink/). Run `klink init {target}` to create one.",
              file=sys.stderr)
        return 1

    dst_examples.mkdir(parents=True, exist_ok=True)
    # Starters are grouped into category subfolders (nanodevice/ photonics/
    # passives/ digital/), so walk RECURSIVELY and sync by relative path -- a
    # flat glob would miss everything and silently refresh nothing. Skip
    # bytecode caches on BOTH sides: pip compiles every .py in the wheel at
    # install time, so the installed template dir grows __pycache__/ that must
    # never be copied into (or deleted from) the user's project.
    def _rel_files(root: Path) -> set:
        return {
            p.relative_to(root) for p in root.rglob("*")
            if p.is_file()
            and "__pycache__" not in p.parts
            # demo OUTPUT dirs are user-produced, never shipped — they
            # must survive an update (mirror-cleanup would delete them)
            and "_generated" not in p.parts
            and p.suffix not in (".pyc", ".pyo")
        }

    want = _rel_files(src_examples)
    have = _rel_files(dst_examples)
    added, updated, removed = [], [], []
    for rel in sorted(want, key=str):
        s, d = src_examples / rel, dst_examples / rel
        if not d.exists():
            if not dry_run:
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, d)
            added.append(str(rel).replace("\\", "/"))
        elif s.read_bytes() != d.read_bytes():
            if not dry_run:
                shutil.copy2(s, d)
            updated.append(str(rel).replace("\\", "/"))
    for rel in sorted(have - want, key=str):     # starters no longer shipped
        if not dry_run:
            (dst_examples / rel).unlink()
        removed.append(str(rel).replace("\\", "/"))
    if not dry_run:
        # prune any now-empty category dirs left behind (e.g. after a
        # flat->nested migration), deepest first
        for d in sorted((p for p in dst_examples.rglob("*") if p.is_dir()),
                        key=lambda p: len(p.parts), reverse=True):
            if not any(d.iterdir()):
                d.rmdir()

    verb = "Would refresh" if dry_run else "Refreshed"
    print(f"{verb} example_template/ in {dst} from the installed package.")
    for label, items in (("added", added), ("updated", updated), ("removed", removed)):
        if items:
            print(f"  {label}: {', '.join(items)}")
    if not (added or updated or removed):
        print("  already up to date.")
    if updated:
        # Starters are package-owned copy-and-adapt references, but people do
        # edit them in place; give them an out before their edits vanish.
        print("note: 'updated' files are overwritten with the packaged "
              "version. If you edited one of them in place, copy it into "
              "custom_devices/ first (use --dry-run to preview).")
    print("Left untouched: pdk.py, custom_devices/, runs/, .klink/, out/, specs/ (your files).")
    return 0


_NOTES_TEMPLATE = """\
# {name}

## Request

(what was asked, in one or two lines)

## What was done

(steps, tools, key parameters)

## Verification evidence

(REAL output: LVS result, geometry counts/asserts -- never "looks done")

## Files

- run.py -- the driver
- out/   -- artifacts of this run
"""

_RUN_PY_TEMPLATE = """\
\"\"\"Run driver: {slug}. Artifacts go to this folder's out/.\"\"\"

import sys
from pathlib import Path

# project root (two levels up: runs/<this>/)
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "custom_devices"))

from pdk import PROCESS            # noqa: E402  process facts live ONLY here
# from toolbox import ...          # check toolbox/__init__.py before rewriting a tool

OUT = Path(__file__).resolve().parent / "out"


def main():
    from klink import KLinkClient
    with KLinkClient().connect() as c:
        ...


if __name__ == "__main__":
    main()
"""


def run_new(slug: str, *, project: str = ".") -> int:
    """Create runs/<today>_<slug>/ and register it in the ledger.

    The folder is born WITH its INDEX.md line ("in progress"), so the
    ledger can never miss a directory even if the finishing discipline
    slips. Date-prefixed names sort chronologically and cannot collide
    (an existing name gets a _2 suffix)."""
    import datetime
    import re

    proj = Path(project).resolve()
    cd = proj / "custom_devices"
    if not (proj / "pdk.py").exists() or not cd.is_dir():
        print(f"{proj} does not look like a klink project "
              "(pdk.py + custom_devices/ expected). Run `klink init` "
              "first, or pass --project.")
        return 2
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", slug.strip()).strip("-")
    if not slug:
        print("slug must contain some letters/digits, e.g. "
              "`klink run new my-first-task`")
        return 2
    runs = proj / "runs"
    runs.mkdir(exist_ok=True)
    today = datetime.date.today().isoformat()
    name = f"{today}_{slug}"
    n = 2
    while (runs / name).exists():
        name = f"{today}_{slug}_{n}"
        n += 1
    rd = runs / name
    (rd / "out").mkdir(parents=True)
    (rd / "notes.md").write_text(_NOTES_TEMPLATE.format(name=name),
                                 encoding="utf-8", newline="\n")
    (rd / "run.py").write_text(_RUN_PY_TEMPLATE.format(slug=slug),
                               encoding="utf-8", newline="\n")
    index = runs / "INDEX.md"
    line = f"- [{name}]({name}/) -- (in progress)\n"
    anchor = "<!-- newest first -->\n"
    if index.exists():
        text = index.read_text(encoding="utf-8")
        pos = text.find(anchor)
        if pos >= 0:
            at = pos + len(anchor)
            text = text[:at] + line + text[at:]
        else:
            text = text.rstrip("\n") + "\n" + line
        index.write_text(text, encoding="utf-8", newline="\n")
    else:
        index.write_text("# runs\n\n" + anchor + line,
                         encoding="utf-8", newline="\n")
    print(f"run folder: {rd}")
    print("next: write the driver in run.py, keep artifacts in out/, "
          "finish notes.md, then replace the ledger's '(in progress)' "
          "with a one-line summary + PASS/FAIL.")
    return 0


def _button_sync(do_reload: bool) -> None:
    """After a manifest change: ask the running KLayout to re-sync its toolbar."""
    if not do_reload:
        print("--no-reload: applied at the next buttons.reload RPC or KLayout start")
        return
    from . import buttons
    from .client import KLinkClient

    try:
        with KLinkClient(connect_timeout=1.0).connect() as client:
            report = buttons.reload(client)
    except Exception as exc:  # KLayout not running / old plugin without buttons.reload
        print(f"file written; KLayout not reachable or not updated ({type(exc).__name__}: {exc}); "
              "the button will appear at the next KLayout start")
        return
    print("installed=%s updated=%s removed=%s skipped=%s" % (
        report.get("installed", []), report.get("updated", []), report.get("removed", []),
        [f"{s.get('file')}: {s.get('reason')}" for s in report.get("skipped", [])]))
    for bid, detail in (report.get("items") or {}).items():
        print(f"  {bid}: " + " ".join(f"{k}={v}" for k, v in detail.items()))


def _button_items(item_args: list[str], def_args: list[str]) -> list[dict] | None:
    """--item ID=TITLE / --item-definition ID=PATH -> items list.

    Each value is split at the first '='.
    """
    if not item_args and not def_args:
        return None
    items, by_id = [], {}
    for raw in item_args:
        sid, sep, title = raw.partition("=")
        if not sep:
            raise ValueError(f"--item {raw!r}: expected ID=TITLE")
        if sid in by_id:
            raise ValueError(f"--item {sid!r} given twice")
        by_id[sid] = {"id": sid, "title": title} if title else {"id": sid}
        items.append(by_id[sid])
    for raw in def_args:
        sid, sep, path = raw.partition("=")
        if not sep or not path:
            raise ValueError(f"--item-definition {raw!r}: expected ID=PATH")
        if sid not in by_id:
            raise ValueError(f"--item-definition {sid!r}: no --item {sid}=TITLE given")
        by_id[sid]["definition"] = path
    return items


def button_cmd(args) -> int:
    from . import buttons

    if args.button_cmd == "add":
        try:
            items = _button_items(args.item or [], args.item_definition or [])
            path = buttons.define(args.id, args.label, title=args.title, definition=args.definition,
                                  items=items, promote=False if args.no_promote else None,
                                  icon=args.icon)
        except ValueError as exc:
            print(str(exc))
            return 2
        print(f"wrote {path}")
        _button_sync(not args.no_reload)
        return 0
    if args.button_cmd == "item":
        try:
            if args.item_cmd == "set":
                path = buttons.set_item(args.button, args.item_id, title=args.title,
                                        definition=args.definition)
                print(f"wrote {path} (item {args.item_id!r})")
            elif args.item_cmd == "remove":
                if not buttons.remove_item(args.button, args.item_id):
                    print(f"button {args.button!r} has no item {args.item_id!r}")
                    return 1
                print(f"removed item {args.item_id!r} from button {args.button!r}")
            else:
                return -1
        except FileNotFoundError as exc:
            print(str(exc))
            return 1
        except ValueError as exc:
            print(str(exc))
            return 2
        _button_sync(not args.no_reload)
        return 0
    if args.button_cmd == "remove":
        if not buttons.remove(args.id):
            print(f"no manifest for button {args.id!r} in {buttons.buttons_dir()}")
            return 1
        print(f"removed {buttons.buttons_dir() / (args.id + '.json')}")
        _button_sync(not args.no_reload)
        return 0
    if args.button_cmd == "list":
        entries = buttons.list_defined()
        print(f"{buttons.buttons_dir()}: {len(entries)} manifest(s)")
        for e in entries:
            status = "ok" if e["problem"] is None else f"SKIPPED: {e['problem']}"
            print(f"  {e.get('id', Path(e['file']).stem):<20} {e.get('label', ''):<7} "
                  f"{e.get('title') or ''}  [{status}]")
            if isinstance(e.get("icon"), str):
                missing = "" if Path(e["icon"]).is_file() else "  (file missing: text button)"
                print(f"      icon: {e['icon']}{missing}")
            items = e.get("items") if isinstance(e.get("items"), list) else []
            for it in items:
                if not isinstance(it, dict):
                    continue
                extra = f"  -> {it['definition']}" if isinstance(it.get("definition"), str) else ""
                print(f"      - {it.get('id')!s:<16} {it.get('title') or it.get('id')}{extra}")
            if e.get("promote") is False:
                print("      (promote: false -- the parent keeps the button's own label)")
        return 0
    return -1


def _print_reload(report) -> None:
    if isinstance(report, str):
        print(report)
        return
    print("toolbar: installed=%s updated=%s removed=%s" % (
        report.get("installed", []), report.get("updated", []), report.get("removed", [])))


def tool_cmd(args) -> int:
    from . import tools

    if args.tool_cmd == "install":
        try:
            rep = tools.install(args.src, reload=not args.no_reload)
        except (OSError, ValueError) as exc:
            print(str(exc))
            return 2
        print(f"installed {rep['id']} -> {rep['dir']}")
        print(f"button {rep['button']}" + (f" (entries: {', '.join(rep['items'])})" if rep["items"] else ""))
        _print_reload(rep["reload"])
        return 0
    if args.tool_cmd == "uninstall":
        try:
            removed = tools.uninstall(args.id, reload=not args.no_reload)
        except ValueError as exc:
            print(str(exc))
            return 2
        if not removed:
            print(f"no tool {args.id!r} installed in {tools.tools_dir()}")
            return 1
        print(f"uninstalled {args.id}")
        return 0
    if args.tool_cmd == "list":
        entries = tools.list_installed()
        print(f"{tools.tools_dir()}: {len(entries)} tool(s)")
        for e in entries:
            status = "ok" if e["problem"] is None else f"UNUSABLE: {e['problem']}"
            presets = f"  presets: {', '.join(e['presets'])}" if e["presets"] else ""
            print(f"  {e['id']:<20} {e.get('title') or ''}  [{status}]{presets}")
        return 0
    if args.tool_cmd == "export":
        try:
            print(f"wrote {tools.export(args.id, args.zip)}")
        except FileNotFoundError as exc:
            print(str(exc))
            return 1
        return 0
    if args.tool_cmd == "run":
        from .client import KLinkClient
        from .errors import KLinkServerError

        params = {}
        for raw in args.param or []:
            key, sep, value = raw.partition("=")
            if not sep or not key:
                print(f"--param {raw!r}: expected NAME=VALUE")
                return 2
            params[key] = value
        try:
            with KLinkClient(connect_timeout=2.0).connect() as client:
                out = tools.run(client, args.id, preset=args.preset, params=params,
                                interactive=args.interactive)
        except KLinkServerError as exc:
            print(f"[{exc.code}] {exc.message}")
            if exc.hint:
                print(f"next: {exc.hint}")
            data = exc.data if isinstance(exc.data, dict) else {}
            if data.get("stdout"):
                print("stdout (printed by run.py before it failed):")
                print(_indent(data["stdout"]))
            if data.get("traceback") and exc.code == "ERR_TOOL_RUN":
                tb = [ln for ln in str(data["traceback"]).splitlines() if ln.strip()]
                print("traceback (tail):")
                print(_indent("\n".join(tb[-4:])))
            print(f"journal: python -m klink.cli tool log {args.id}")
            return 1
        except Exception as exc:  # transport: KLayout not running
            print(f"KLayout not reachable ({type(exc).__name__}: {exc}); start KLayout with klink loaded")
            return 1
        print(f"{out.get('status')}: {out.get('tool')} preset={out.get('preset')} "
              f"selection={out.get('selection_count')} {out.get('wall_ms')} ms")
        result = out.get("result")
        if isinstance(result, dict) and result.get("stdout"):
            print("stdout:")
            print(_indent(result["stdout"]))
            result = {k: v for k, v in result.items() if k != "stdout"}
        if result is not None:
            print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.tool_cmd == "new":
        return _tool_new(args)
    if args.tool_cmd == "check":
        return _tool_check(args)
    if args.tool_cmd == "log":
        return _tool_log(args)
    return -1


def _indent(text, prefix: str = "    ") -> str:
    return "\n".join(prefix + ln for ln in str(text).splitlines())


def _tool_new(args) -> int:
    from . import tools

    try:
        rep = tools.scaffold(args.id, dest=args.dir, from_dir=args.from_dir, title=args.title,
                             label=args.label, min_selected=args.min_selected, macro=args.macro,
                             icon=args.icon)
    except (OSError, ValueError) as exc:
        print(str(exc))
        return 2
    rel = Path(rep["dir"]).as_posix()
    shown = ", ".join(rep["files"]) if len(rep["files"]) <= 6 else f"{len(rep['files'])} files"
    origin = f" from {Path(args.from_dir).as_posix()}" if args.from_dir else ""
    form = "def run(ctx, params)" if rep["form"] == "run" else "plain macro" if rep["form"] else "?"
    print(f"created {rel}  ({shown}){origin}  [run.py form: {form}]")
    for err in rep["check"]["errors"]:
        print(f"  ERROR: {err}")
    for warn in rep["check"]["warnings"]:
        print(f"  warning: {warn}")
    print("next:")
    print(f"  python -m klink.cli tool check {rel}")
    print(f"  python -m klink.cli tool install {rel}")
    print(f"  python -m klink.cli tool run {rep['id']}            # select the input in KLayout first")
    print(f"  python -m klink.cli tool log {rep['id']}")
    return 0


def _fmt_value(v) -> str:
    return "-" if v is None else json.dumps(v, ensure_ascii=False)


def _tool_check(args) -> int:
    from . import tools

    rep = tools.check(args.target)
    if args.json:
        print(json.dumps({k: rep[k] for k in ("ok", "errors", "warnings", "form", "params", "presets",
                                              "dir", "id", "prompt", "icon")}, ensure_ascii=False, indent=2))
        return 0 if rep["ok"] else 1
    form = {"run": "def run(ctx, params)", "macro": "plain KLayout macro"}.get(rep["form"], "unknown")
    print(f"{rep['dir']}: tool {rep['id']!r}, run.py form: {form}, prompt: {rep['prompt'] or '-'}, "
          f"icon: {rep['icon'] or '- (text button)'}")
    if rep["params"]:
        print("params:")
        rows = [("name", "type", "default", "from", "exclude", "allow_new", "asked")]
        for p in rep["params"]:
            rows.append((p["name"], str(p["type"]), "(required)" if p["required"] else _fmt_value(p["default"]),
                         p["from"] or "-", ",".join(p["exclude"]) if isinstance(p["exclude"], list) else "-",
                         _fmt_value(p["allow_new"]), p["asked"]))
        widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
        for r in rows:
            print("  " + "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())
    else:
        print("params: none")
    if rep["presets"]:
        print("presets:")
        for pre in rep["presets"]:
            vals = ", ".join(f"{k}={_fmt_value(v)}" for k, v in pre["params"].items()) or "-"
            print(f"  {pre['name']:<16} {pre['title']!s:<28} {vals}")
    for err in rep["errors"]:
        print(f"ERROR: {err}")
    for warn in rep["warnings"]:
        print(f"warning: {warn}")
    print(f"{'FAILED' if rep['errors'] else 'ok'}: {len(rep['errors'])} error(s), "
          f"{len(rep['warnings'])} warning(s)")
    return 0 if rep["ok"] else 1


def _tool_log(args) -> int:
    import time as _time
    from . import tools

    recs = tools.read_log(args.id, n=args.n)
    if args.json:
        print(json.dumps(recs, ensure_ascii=False, indent=2))
        return 0
    if not recs:
        what = f"tool {args.id!r}" if args.id else "any tool"
        print(f"no runs recorded for {what} in {tools.journals_dir()}; click the button or run "
              f"`python -m klink.cli tool run {args.id or '<id>'}`")
        return 0
    for i, r in enumerate(recs):
        if i:
            print()
        ts = r.get("ts")
        when = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(ts)) if isinstance(ts, (int, float)) else "?"
        head = (f"{when}  {r.get('status', '?').upper()}  tool={r.get('tool')}  preset={r.get('preset') or '-'}"
                f"  session={r.get('session')}")
        extra = [] if r.get("wall_ms") is None else [f"{r['wall_ms']} ms"]
        if r.get("seq") is not None:
            extra.append(f"seq {r['seq']}")
        if r.get("interactive"):
            extra.append("interactive")
        print(head + (f"  ({', '.join(extra)})" if extra else ""))
        if "selection_count" in r:
            print(f"  selection: {r.get('selection_count')} shape(s), {r.get('selected_count')} object(s)")
        if r.get("params") is not None:
            print(f"  params: {json.dumps(r['params'], ensure_ascii=False)}")
        if r.get("auto"):
            print(f"  auto:   {json.dumps(r['auto'], ensure_ascii=False)}")
        result = r.get("result")
        stdout = r.get("stdout")
        if isinstance(result, dict):
            stdout = stdout or result.get("stdout")
            result = {k: v for k, v in result.items() if k != "stdout"}
        if result is not None:
            print(f"  result: {json.dumps(result, ensure_ascii=False)}")
        if stdout:
            print("  stdout:")
            print(_indent(stdout, "    | "))
        err = r.get("error")
        if isinstance(err, dict):
            print(f"  error:  [{err.get('code')}] {err.get('message')}"
                  + (f" ({err['type']})" if err.get("type") else ""))
            if err.get("hint"):
                print(f"  hint:   {err['hint']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="klink", description="klink command-line tools.")
    ap.add_argument("--version", action="version",
                    version=f"klayout-klink {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    p_init = sub.add_parser("init", help="scaffold a new klink user project")
    p_init.add_argument("directory", help="target directory for the new project")

    p_update = sub.add_parser(
        "update", help="refresh the project's example_template/ from the "
                       "installed package (never touches your own files)")
    p_update.add_argument("directory", nargs="?", default=".",
                          help="the klink project directory (default: current dir)")
    p_update.add_argument("--dry-run", action="store_true",
                          help="list what would change without writing anything")

    p_plugin = sub.add_parser(
        "plugin", help="manage the bundled KLayout salt plugin")
    plugin_sub = p_plugin.add_subparsers(dest="plugin_cmd")
    p_pi = plugin_sub.add_parser(
        "install", help="copy the bundled plugin into KLayout's salt/ "
                        "directory (then restart KLayout)")
    p_pi.add_argument("--salt-dir", default=None,
                      help="KLayout salt directory (default: auto-detected "
                           "per OS, honouring KLAYOUT_HOME)")
    p_pi.add_argument("--force", action="store_true",
                      help="replace an existing klink_plugin/ even if it does "
                           "not look like the klink plugin")
    p_ps = plugin_sub.add_parser(
        "status", help="show installed vs bundled plugin versions")
    p_ps.add_argument("--salt-dir", default=None,
                      help="KLayout salt directory (default: auto-detected)")

    p_run = sub.add_parser(
        "run", help="manage task runs inside a klink project")
    run_sub = p_run.add_subparsers(dest="run_cmd")
    p_rn = run_sub.add_parser(
        "new", help="create runs/<today>_<slug>/ (run.py + out/ + "
                    "notes.md) and register it in runs/INDEX.md")
    p_rn.add_argument("slug", help="short kebab-case task name, e.g. "
                                   "my-first-task")
    p_rn.add_argument("--project", default=".",
                      help="the klink project directory (default: "
                           "current dir)")

    p_btn = sub.add_parser(
        "button", help="manage custom KLayout toolbar buttons (manifests under "
                       "<registry_root>/buttons/)")
    btn_sub = p_btn.add_subparsers(dest="button_cmd")
    p_ba = btn_sub.add_parser("add", help="write a button manifest and hot-add it")
    p_ba.add_argument("id", help="button id: ^[a-z][a-z0-9_]{0,39}$")
    p_ba.add_argument("--label", required=True, help="toolbar text (cut to 6 chars)")
    p_ba.add_argument("--title", default=None, help="tooltip / action title (default: id)")
    p_ba.add_argument("--definition", default=None,
                      help="opaque path/string echoed in every click record")
    p_ba.add_argument("--item", action="append", metavar="ID=TITLE",
                      help="dropdown entry (repeatable, menu order); makes the button a dropdown. "
                           "TITLE is the entry's one name: it is also what the parent shows "
                           "once that entry is clicked")
    p_ba.add_argument("--item-definition", action="append", metavar="ID=PATH",
                      help="definition for one --item (repeatable; default: the button's)")
    p_ba.add_argument("--icon", default=None, metavar="FILE",
                      help="optional .png/.svg shown on the toolbar with the label under it "
                           "(stored as its absolute path; the file must exist)")
    p_ba.add_argument("--no-promote", action="store_true",
                      help="dropdown: the parent always shows the button's own label/title "
                           "(its click still runs the current item)")
    p_ba.add_argument("--no-reload", action="store_true",
                      help="only write the file; do not contact KLayout")
    p_bi = btn_sub.add_parser("item", help="add/replace/remove ONE dropdown entry of a button")
    item_sub = p_bi.add_subparsers(dest="item_cmd")
    p_bis = item_sub.add_parser("set", help="add (append) or replace one dropdown entry")
    p_bis.add_argument("button")
    p_bis.add_argument("item_id", metavar="item")
    p_bis.add_argument("--title", default=None, help="entry name (default: item id); the parent shows it once "
                            "this entry is clicked")
    p_bis.add_argument("--definition", default=None,
                       help="opaque string echoed in this entry's click records "
                            "(default: the button's)")
    p_bis.add_argument("--no-reload", action="store_true",
                       help="only write the file; do not contact KLayout")
    p_bir = item_sub.add_parser("remove", help="remove one dropdown entry")
    p_bir.add_argument("button")
    p_bir.add_argument("item_id", metavar="item")
    p_bir.add_argument("--no-reload", action="store_true",
                       help="only write the file; do not contact KLayout")
    p_br = btn_sub.add_parser("remove", help="delete a button manifest and hot-remove it")
    p_br.add_argument("id")
    p_br.add_argument("--no-reload", action="store_true",
                      help="only delete the file; do not contact KLayout")
    btn_sub.add_parser("list", help="list the button manifests on disk")

    p_tool = sub.add_parser(
        "tool", help="manage deterministic KLayout tools (tool.json + run.py under "
                     "<registry_root>/tools/<id>/)")
    tool_sub = p_tool.add_subparsers(dest="tool_cmd")
    p_ti = tool_sub.add_parser("install", help="install a tool directory or .zip and add its button")
    p_ti.add_argument("src", help="tool directory (named like its id) or a .zip of one")
    p_ti.add_argument("--no-reload", action="store_true", help="do not contact KLayout")
    p_tu = tool_sub.add_parser("uninstall", help="remove a tool and its button")
    p_tu.add_argument("id")
    p_tu.add_argument("--no-reload", action="store_true", help="do not contact KLayout")
    tool_sub.add_parser("list", help="list installed tools")
    p_te = tool_sub.add_parser("export", help="zip an installed tool (shareable, installable)")
    p_te.add_argument("id")
    p_te.add_argument("zip")
    p_tr = tool_sub.add_parser("run", help="run an installed tool in the running KLayout")
    p_tr.add_argument("id")
    p_tr.add_argument("--preset")
    p_tr.add_argument("--param", action="append", metavar="NAME=VALUE",
                      help="param value (repeatable); overrides the preset")
    p_tr.add_argument("--interactive", action="store_true",
                      help="ask the params in one KLayout form")
    p_tn = tool_sub.add_parser(
        "new", help="scaffold a tool folder (tool.json + run.py + README.md) to edit, check and install",
        description="Scaffold <dir>/<id>/ with tool.json, run.py and README.md. The default run.py is "
                    "a `def run(ctx, params)` skeleton whose docstring spells out the whole contract; "
                    "it installs and runs as is (it counts the selected shapes per layer).")
    p_tn.add_argument("id", help="tool id: ^[a-z][a-z0-9_]{0,39}$ (also the folder name)")
    p_tn.add_argument("--dir", default=".klink/tools",
                      help="parent folder for the new tool (default: .klink/tools)")
    p_tn.add_argument("--from", dest="from_dir", default=None, metavar="TOOL_DIR",
                      help="copy an existing tool folder (e.g. example_template/tools/bool_region) "
                           "and rewrite its id")
    p_tn.add_argument("--title", default=None, help="tool.json title (default: from the id)")
    p_tn.add_argument("--label", default=None,
                      help="toolbar text, at most 6 characters shown (default: the id's first 6, upper case)")
    p_tn.add_argument("--min-selected", type=int, default=None, metavar="N",
                      help="inputs.selection.min: klink refuses the run with fewer selected objects")
    p_tn.add_argument("--icon", default=None, metavar="FILE",
                      help="optional .png/.svg (24x24 or 32x32) copied in as icon.<ext> and set as "
                           "tool.json \"icon\"; without it the button is text only")
    p_tn.add_argument("--macro", action="store_true",
                      help="write a plain KLayout macro skeleton instead of def run(ctx, params)")
    p_tc = tool_sub.add_parser(
        "check", help="lint a tool folder or installed tool without KLayout (all problems at once)",
        description="Check tool.json and run.py without KLayout and report every problem found: "
                    "validator errors, run.py missing / syntax errors, the run.py form, the params "
                    "and presets, and warnings for patterns that misbehave at run time. Exit code "
                    "1 on errors, 0 when only warnings.")
    p_tc.add_argument("target", help="tool directory, or the id of an installed tool")
    p_tc.add_argument("--json", action="store_true",
                      help="print {ok, errors, warnings, form, params, presets} as JSON")
    p_tl = tool_sub.add_parser(
        "log", help="show recent tool runs from the plugin's journals (all sessions)",
        description="Read <registry_root>/journals/*.tools.jsonl (written by the klink plugin for "
                    "every button click / tool run), merge all sessions, sort by time and print the "
                    "newest N runs, newest last: status, params, result, stdout, error + hint.")
    p_tl.add_argument("id", nargs="?", default=None, help="only runs of this tool")
    p_tl.add_argument("-n", type=int, default=5, help="how many runs to show (default 5; 0 = all)")
    p_tl.add_argument("--json", action="store_true", help="dump the records as JSON")

    # `klink doctor` used to drop every flag on the floor (doctor_main([])),
    # so --scan/--report were reachable only as `python -m klink.doctor`.
    p_doc = sub.add_parser(
        "doctor",
        help="preflight check (interpreter, plugin, handshake, L-Edit bridge)")
    p_doc.add_argument("--host", default="127.0.0.1")
    p_doc.add_argument("--port", type=int, default=8765)
    p_doc.add_argument("--scan", action="store_true",
                       help="scan localhost ports for a live klink session")
    p_doc.add_argument("--gdsfactory", action="store_true",
                       help="also check gdsfactory is importable here")
    p_doc.add_argument("--json", action="store_true",
                       help="emit the report as JSON")
    p_doc.add_argument("--report", action="store_true",
                       help="print a markdown block ready to paste into an issue")

    args = ap.parse_args(argv)
    if args.cmd == "init":
        return init(args.directory)
    if args.cmd == "run":
        if args.run_cmd == "new":
            return run_new(args.slug, project=args.project)
        p_run.print_help()
        return 2
    if args.cmd == "update":
        return update(args.directory, dry_run=args.dry_run)
    if args.cmd == "plugin":
        salt = Path(args.salt_dir) if getattr(args, "salt_dir", None) else None
        if args.plugin_cmd == "install":
            return plugin_install(salt, force=args.force)
        if args.plugin_cmd == "status":
            return plugin_status(salt)
        p_plugin.print_help()
        return 1
    if args.cmd == "button":
        rc = button_cmd(args)
        if rc == -1:
            p_btn.print_help()
            return 2
        return rc
    if args.cmd == "tool":
        rc = tool_cmd(args)
        if rc == -1:
            p_tool.print_help()
            return 2
        return rc
    if args.cmd == "doctor":
        from .doctor import main as doctor_main

        forwarded = ["--host", args.host, "--port", str(args.port)]
        for flag in ("scan", "gdsfactory", "json", "report"):
            if getattr(args, flag, False):
                forwarded.append("--" + flag)
        return doctor_main(forwarded)
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

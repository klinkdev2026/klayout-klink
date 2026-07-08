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
    for sub in ("custom_devices", "specs", "out"):
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
    ``custom_devices/``, ``.klink/`` (session + net tables), ``out/``, ``specs/``
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
    print("Left untouched: pdk.py, custom_devices/, .klink/, out/, specs/ (your files).")
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

    sub.add_parser("doctor", help="preflight check (interpreter, plugin, handshake)")

    args = ap.parse_args(argv)
    if args.cmd == "init":
        return init(args.directory)
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
    if args.cmd == "doctor":
        from .doctor import main as doctor_main

        return doctor_main([])
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

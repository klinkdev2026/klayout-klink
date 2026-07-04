"""klink command-line interface.

    klink init <dir>   scaffold a new klink user project
    klink update [dir] refresh the project's bundled example_template/ from the
                       installed package (never touches your own files)
    klink doctor       preflight check (interpreter, plugin, version handshake)

The `init` scaffold is the bundled project template (``klink/templates/project``)
so a pure ``pip install klayout-klink`` user can start a project with no repo
checkout: ``pip install klayout-klink`` -> ``klink init my-chip`` -> open it with
an agent and describe what you are building.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def _template_dir() -> Path:
    """Path to the bundled project template. A pip-installed wheel unpacks to a
    real filesystem path, so a plain Path works."""
    return Path(__file__).resolve().parent / "templates" / "project"


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


def update(target: str) -> int:
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
    want = {p.name for p in src_examples.glob("*.py")}
    have = {p.name for p in dst_examples.glob("*.py")}
    added, updated, removed = [], [], []
    for name in sorted(want):
        s, d = src_examples / name, dst_examples / name
        if not d.exists():
            shutil.copy2(s, d)
            added.append(name)
        elif s.read_bytes() != d.read_bytes():
            shutil.copy2(s, d)
            updated.append(name)
    for name in sorted(have - want):        # starters no longer shipped
        (dst_examples / name).unlink()
        removed.append(name)

    print(f"Refreshed example_template/ in {dst} from the installed package.")
    for label, items in (("added", added), ("updated", updated), ("removed", removed)):
        if items:
            print(f"  {label}: {', '.join(items)}")
    if not (added or updated or removed):
        print("  already up to date.")
    print("Left untouched: pdk.py, custom_devices/, .klink/, out/, specs/ (your files).")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="klink", description="klink command-line tools.")
    sub = ap.add_subparsers(dest="cmd")

    p_init = sub.add_parser("init", help="scaffold a new klink user project")
    p_init.add_argument("directory", help="target directory for the new project")

    p_update = sub.add_parser(
        "update", help="refresh the project's example_template/ from the "
                       "installed package (never touches your own files)")
    p_update.add_argument("directory", nargs="?", default=".",
                          help="the klink project directory (default: current dir)")

    sub.add_parser("doctor", help="preflight check (interpreter, plugin, handshake)")

    args = ap.parse_args(argv)
    if args.cmd == "init":
        return init(args.directory)
    if args.cmd == "update":
        return update(args.directory)
    if args.cmd == "doctor":
        from .doctor import main as doctor_main

        return doctor_main([])
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

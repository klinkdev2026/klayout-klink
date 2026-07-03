"""Migrate the STARTER examples -> the shipped project template.

`examples_klink/public/` is the curated public example gallery (the open subset
that ships in the public repo, like `docs/public/` for docs), organized in
buckets `demos/` `features/` `smoke/`. Only a small STARTER subset
(`public/demos/`) is also bundled in the WHEEL and scaffolded by `klink init`
into a user project's `example_template/` — those are the copy-and-adapt starting
points; the rest of the gallery is repo-only.

Run after adding/editing a starter:

    python examples_klink/public/sync_to_template.py

Copies `public/demos/*.py` into the template and asserts byte-for-byte identity.
"""
from __future__ import annotations

import filecmp
import shutil
from pathlib import Path

SRC = Path(__file__).resolve().parent
STARTERS_DIR = SRC / "demos"
# The wheel-shipped STARTER subset: fully self-contained copy-and-adapt starters
# (import only `klink`, carry their own layers, need no bundled data file or repo
# layout). fit_device_pnr_lvs is a repo-only flagship demo — it reads a bundled
# netlist and the repo tree, so it is NOT a template starter.
STARTERS = ["ebl_wraparound.py", "gf_mzi_module.py", "hallbar.py",
            "neural_electrode.py"]
TEMPLATE = SRC.parents[1] / "klink" / "templates" / "project" / "example_template"


def main() -> int:
    TEMPLATE.mkdir(parents=True, exist_ok=True)
    src_files = [STARTERS_DIR / name for name in STARTERS]
    missing = [str(p) for p in src_files if not p.exists()]
    if missing:
        raise SystemExit("missing starter(s): " + ", ".join(missing))

    # drop template .py files that are no longer starters
    for stale in TEMPLATE.glob("*.py"):
        if stale.name not in STARTERS:
            stale.unlink()
            print(f"removed stale: example_template/{stale.name}")

    for p in src_files:
        shutil.copy2(p, TEMPLATE / p.name)

    ok = all(filecmp.cmp(p, TEMPLATE / p.name, shallow=False) for p in src_files)
    print(f"synced {len(src_files)} starter(s) -> {TEMPLATE}")
    print("starters == template:", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

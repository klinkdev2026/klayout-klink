"""Migrate the STARTER examples -> the shipped project template.

`examples_klink/public/` is the curated public example gallery (the open subset
that ships in the public repo, like `docs/public/` for docs), organized in
buckets `demos/` `features/` `smoke/` `passives/`. A STARTER subset is also
bundled in the WHEEL and scaffolded by `klink init` into a user project's
`example_template/` — those are the copy-and-adapt starting points; the rest of
the gallery is repo-only.

The template groups starters into category subfolders so a fresh project is not
a flat pile:

    example_template/
      nanodevice/  hallbar, ebl_wraparound, neural_electrode
      photonics/   gf_mzi_module
      passives/    idc_capacitor, spiral_inductor, saw_idt_filter, baw_fbar_planview

A starter must be fully self-contained: it imports only `klink` (plus
`klayout.db` for offline geometry), carries its own layers, and needs no bundled
data file or the repo tree. The digital P&R demos (fit_device_pnr_lvs, padframe,
chat_to_netlist, multilayer) read a bundled netlist and cross-import each other,
so they are repo-only, NOT template starters.

Run after adding/editing a starter:

    python examples_klink/public/sync_to_template.py

Copies each starter into its category subfolder and asserts byte-for-byte
identity.
"""
from __future__ import annotations

import filecmp
import shutil
from pathlib import Path

SRC = Path(__file__).resolve().parent

# category -> list of (source bucket dir, filename). The category is the
# example_template subfolder; the source bucket is the matching category
# subfolder under examples_klink/public/demos/.
STARTERS: dict[str, list[tuple[str, str]]] = {
    "nanodevice": [
        ("demos/nanodevice", "hallbar.py"),
        ("demos/nanodevice", "ebl_wraparound.py"),
        ("demos/nanodevice", "neural_electrode.py"),
    ],
    "photonics": [
        ("demos/photonics", "gf_mzi_module.py"),
    ],
    "passives": [
        ("demos/passives", "idc_capacitor.py"),
        ("demos/passives", "spiral_inductor.py"),
        ("demos/passives", "saw_idt_filter.py"),
        ("demos/passives", "baw_fbar_planview.py"),
    ],
    # digital P&R -> LVS: a self-contained family that cross-imports within the
    # folder and reads its own bundled netlists, so the whole folder ships (the
    # demos fall back to a same-dir sibling import when scaffolded). These are
    # LIVE starters (they need a running KLayout session for P&R + LVS).
    "digital": [
        ("demos/digital", "fit_device_pnr_lvs.py"),
        ("demos/digital", "padframe_pnr_lvs.py"),
        ("demos/digital", "chat_to_netlist_pnr.py"),
        ("demos/digital", "multilayer_pnr_lvs.py"),
        ("demos/digital", "_multilayer_engine.py"),
        ("demos/digital", "add4.devnet.json"),
        ("demos/digital", "cpu4.devnet.json"),
    ],
}

TEMPLATE = SRC.parents[1] / "klink" / "templates" / "project" / "example_template"


def _planned() -> list[tuple[Path, Path]]:
    """Return (source_path, template_path) for every starter."""
    pairs: list[tuple[Path, Path]] = []
    for category, items in STARTERS.items():
        for bucket, name in items:
            pairs.append((SRC / bucket / name, TEMPLATE / category / name))
    return pairs


def main() -> int:
    pairs = _planned()
    missing = [str(s) for s, _ in pairs if not s.exists()]
    if missing:
        raise SystemExit("missing starter(s): " + ", ".join(missing))

    wanted = {dst for _, dst in pairs}
    # drop template .py files that are no longer starters (recurse subfolders)
    for stale in TEMPLATE.rglob("*.py"):
        if stale not in wanted:
            stale.unlink()
            print(f"removed stale: {stale.relative_to(TEMPLATE.parent)}")

    for src, dst in pairs:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    ok = all(filecmp.cmp(src, dst, shallow=False) for src, dst in pairs)
    print(f"synced {len(pairs)} starter(s) into {len(STARTERS)} categories -> {TEMPLATE}")
    print("starters == template:", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

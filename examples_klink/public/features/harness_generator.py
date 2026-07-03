"""Compatibility wrapper for the harness-backed neural electrode generator."""

from __future__ import annotations

from harnesspcell import HarnessPCellSpec, generate_harnesspcell, main


def generate(client, cell: str, elec_rows: int = 4, **kwargs):
    spec = HarnessPCellSpec(cell_name=cell, elec_rows=elec_rows, **kwargs)
    return generate_harnesspcell(client, spec)


if __name__ == "__main__":
    raise SystemExit(main())

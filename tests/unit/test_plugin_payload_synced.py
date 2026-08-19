"""Guard: klink/plugin_payload/klink_plugin/ must be a byte-exact mirror of
klink_plugin/ (the source of truth). Same pattern as the example_template
starters guard: editing the plugin without re-running the sync tool fails the
suite instead of shipping a stale payload in the wheel.

Regenerate with: python tools/sync_plugin_payload.py
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "klink_plugin"
DEST = REPO_ROOT / "klink" / "plugin_payload" / "klink_plugin"

RESYNC = "run: python tools/sync_plugin_payload.py"


def _source_files() -> dict[Path, Path]:
    """rel -> abs source file; git-tracked when possible so caches never count."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "klink_plugin"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        files = [REPO_ROOT / line for line in out.stdout.splitlines() if line.strip()]
        if files:
            return {p.relative_to(SOURCE): p for p in files}
    except (OSError, subprocess.CalledProcessError):
        pass
    return {
        p.relative_to(SOURCE): p
        for p in SOURCE.rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.suffix not in (".pyc", ".pyo")
    }


@pytest.mark.skipif(not SOURCE.is_dir(), reason="no klink_plugin/ source tree here")
def test_plugin_payload_matches_source():
    assert DEST.is_dir(), f"klink/plugin_payload/klink_plugin missing; {RESYNC}"

    want = _source_files()
    have = {p.relative_to(DEST) for p in DEST.rglob("*") if p.is_file()}

    missing = sorted(str(r) for r in set(want) - have)
    stale = sorted(str(r) for r in have - set(want))
    assert not missing, f"payload missing {missing}; {RESYNC}"
    assert not stale, f"payload has stale files {stale}; {RESYNC}"

    drift = sorted(
        str(rel) for rel, src in want.items()
        if src.read_bytes() != (DEST / rel).read_bytes()
    )
    assert not drift, f"payload content drift in {drift}; {RESYNC}"


@pytest.mark.skipif(not SOURCE.is_dir(), reason="no klink_plugin/ source tree here")
def test_plugin_payload_readme_marks_it_generated():
    readme = DEST.parent / "README.md"
    assert readme.is_file(), f"plugin_payload/README.md missing; {RESYNC}"
    text = readme.read_text(encoding="utf-8")
    assert "do not edit" in text
    assert "sync_plugin_payload" in text


def test_no_untracked_plugin_sources():
    """The sync and the guard above both enumerate via `git ls-files`,
    so a brand-new module that was never `git add`ed is invisible to
    BOTH: the payload ships without it and everything reports green.
    That false green bit twice (annotation_m.py sat out of the payload
    with the full suite passing; the same gap false-alarmed the public
    tree at a release gate). Fail loudly instead."""
    import subprocess
    try:
        out = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard",
             "klink_plugin"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return  # not a git checkout (sdist): nothing to assert
    untracked = [l for l in out.stdout.splitlines()
                 if l.strip() and "__pycache__" not in l
                 and not l.endswith((".pyc", ".pyo"))]
    assert not untracked, (
        f"klink_plugin/ has untracked file(s) {untracked}; the payload "
        f"sync only mirrors git-tracked files, so these would be missing "
        f"from the wheel. `git add` them, then re-run "
        f"tools/sync_plugin_payload.py")

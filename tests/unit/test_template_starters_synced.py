"""Guard: the STARTER demos shipped into the project template must stay
byte-identical to their source under `examples_klink/public/`.

`klink init` scaffolds `klink/templates/project/example_template/` from those
starters (via `examples_klink/public/sync_to_template.py`), grouped into
category subfolders (nanodevice/ photonics/ passives/). Nothing FORCES the sync
to be re-run after editing a source example, so the template silently goes stale
-- which is exactly how a `klink init` user ends up with an outdated example
(e.g. a gf_mzi_module missing its `--reroute` flow, or a passive template that
never followed a fix). This test fails the moment they drift, telling you to
re-sync.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "examples_klink" / "public"
TEMPLATE = ROOT / "klink" / "templates" / "project" / "example_template"

sys.path.insert(0, str(PUBLIC))
from sync_to_template import STARTERS, _planned  # noqa: E402


def test_template_starters_are_byte_identical_to_source():
    pairs = _planned()
    assert pairs, "sync_to_template declares no starters"
    for src, dst in pairs:
        assert src.exists(), f"declared starter source is missing: {src}"
        assert dst.exists(), (
            f"template is missing {dst.relative_to(TEMPLATE.parent)}; re-run "
            f"`python examples_klink/public/sync_to_template.py`")
        assert src.read_bytes() == dst.read_bytes(), (
            f"{dst.relative_to(TEMPLATE.parent)} drifted from {src}; re-run "
            f"`python examples_klink/public/sync_to_template.py`")


def test_template_has_no_stale_starter_files():
    # every .py under the template must be a declared starter (no leftovers
    # from a rename/removal that the sync forgot to prune)
    wanted = {dst for _, dst in _planned()}
    for found in TEMPLATE.rglob("*.py"):
        assert found in wanted, (
            f"stale starter in template: {found.relative_to(TEMPLATE.parent)}; "
            f"re-run sync_to_template.py")


def test_every_category_has_at_least_one_starter():
    for category, items in STARTERS.items():
        assert items, f"category {category!r} has no starters"
        for _bucket, name in items:
            assert (TEMPLATE / category / name).exists()

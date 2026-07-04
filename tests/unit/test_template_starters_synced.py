"""Guard: the STARTER demos shipped into the project template must stay
byte-identical to their source in `examples_klink/public/demos/`.

`klink init` scaffolds `klink/templates/project/example_template/` from those
starters (via `examples_klink/public/sync_to_template.py`). Nothing FORCES the
sync to be re-run after editing a demo, so the template silently goes stale --
which is exactly how a `klink init` user ends up with an outdated example (e.g.
a gf_mzi_module missing its `--reroute` flow). This test fails the moment they
drift, telling you to re-sync.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEMOS = ROOT / "examples_klink" / "public" / "demos"
TEMPLATE = ROOT / "klink" / "templates" / "project" / "example_template"


def test_template_starters_are_byte_identical_to_demos():
    starters = sorted(p.name for p in TEMPLATE.glob("*.py"))
    assert starters, f"no starter .py files in {TEMPLATE}"
    for name in starters:
        src = DEMOS / name
        dst = TEMPLATE / name
        assert src.exists(), (
            f"template ships {name} but examples_klink/public/demos/ has no such "
            f"starter -- remove it from the template or restore the source")
        assert src.read_bytes() == dst.read_bytes(), (
            f"{name} drifted from its source; re-run "
            f"`python examples_klink/public/sync_to_template.py`")

"""`klink update` refreshes the project's example_template/ from the installed
package WITHOUT touching anything the user owns (pdk.py, custom_devices/,
.klink/, out/, specs/). This is the non-destructive alternative to re-running
`klink init` (which refuses a non-empty dir)."""
from klink.cli import _template_dir, init, update


def test_update_refreshes_example_template_only(tmp_path):
    proj = tmp_path / "my_chip"
    assert init(str(proj)) == 0

    # things the user owns -> must survive update untouched
    (proj / "pdk.py").write_text("USER PDK\n")
    (proj / ".klink").mkdir(exist_ok=True)
    (proj / ".klink" / "net.json").write_text("USER NET TABLE\n")
    (proj / "custom_devices" / "d.py").write_text("USER DEVICE\n")

    # a starter the user (or a stale install) left out of date, plus a starter
    # the package no longer ships
    starter = proj / "example_template" / "gf_mzi_module.py"
    assert starter.exists()
    starter.write_text("STALE\n")
    (proj / "example_template" / "zzz_old_starter.py").write_text("OLD\n")

    assert update(str(proj)) == 0

    pkg = _template_dir() / "example_template" / "gf_mzi_module.py"
    assert starter.read_bytes() == pkg.read_bytes()                    # refreshed
    assert not (proj / "example_template" / "zzz_old_starter.py").exists()  # pruned
    assert (proj / "pdk.py").read_text() == "USER PDK\n"               # untouched
    assert (proj / ".klink" / "net.json").read_text() == "USER NET TABLE\n"
    assert (proj / "custom_devices" / "d.py").read_text() == "USER DEVICE\n"


def test_update_rejects_a_non_project_dir(tmp_path):
    empty = tmp_path / "random"
    empty.mkdir()
    (empty / "notes.txt").write_text("unrelated\n")
    assert update(str(empty)) == 1
    assert not (empty / "example_template").exists()

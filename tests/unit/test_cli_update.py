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
    # the package no longer ships. Starters live in category subfolders now.
    starter = proj / "example_template" / "photonics" / "gf_mzi_module.py"
    assert starter.exists()
    starter.write_text("STALE\n")
    (proj / "example_template" / "zzz_old_starter.py").write_text("OLD\n")

    assert update(str(proj)) == 0

    pkg = _template_dir() / "example_template" / "photonics" / "gf_mzi_module.py"
    assert starter.read_bytes() == pkg.read_bytes()                    # refreshed
    assert not (proj / "example_template" / "zzz_old_starter.py").exists()  # pruned
    # a passive starter is scaffolded into its category subfolder
    assert (proj / "example_template" / "passives" / "saw_idt_filter.py").exists()
    assert (proj / "pdk.py").read_text() == "USER PDK\n"               # untouched
    assert (proj / ".klink" / "net.json").read_text() == "USER NET TABLE\n"
    assert (proj / "custom_devices" / "d.py").read_text() == "USER DEVICE\n"


def test_update_rejects_a_non_project_dir(tmp_path):
    empty = tmp_path / "random"
    empty.mkdir()
    (empty / "notes.txt").write_text("unrelated\n")
    assert update(str(empty)) == 1
    assert not (empty / "example_template").exists()


def test_init_lays_the_two_strata_skeleton(tmp_path):
    # the wheel must carry the SUBDIRECTORY template files: a
    # non-recursive package-data glob silently dropped exactly this
    # kind of file once before (the 0.1.1 starter loss)
    from klink.cli import main

    proj = tmp_path / "proj"
    assert main(["init", str(proj)]) == 0
    toolbox = proj / "custom_devices" / "toolbox" / "__init__.py"
    index = proj / "custom_devices" / "runs" / "INDEX.md"
    assert toolbox.exists() and "Graduation" in toolbox.read_text(
        encoding="utf-8")
    assert index.exists() and "one line per run" in index.read_text(
        encoding="utf-8").lower()


def test_run_new_creates_dated_folder_and_ledger_line(tmp_path):
    from klink.cli import main

    proj = tmp_path / "proj"
    assert main(["init", str(proj)]) == 0
    assert main(["run", "new", "My Array!!", "--project", str(proj)]) == 0
    assert main(["run", "new", "other-task", "--project", str(proj)]) == 0
    runs = proj / "custom_devices" / "runs"
    dirs = sorted(d.name for d in runs.iterdir() if d.is_dir())
    import datetime
    today = datetime.date.today().isoformat()
    assert dirs[0] == f"{today}_My-Array"          # sanitized, dated
    assert dirs[1] == f"{today}_other-task"
    rd = runs / dirs[1]
    assert (rd / "run.py").exists() and (rd / "out").is_dir()
    assert "Verification evidence" in (rd / "notes.md").read_text(
        encoding="utf-8")
    index = (runs / "INDEX.md").read_text(encoding="utf-8")
    anchor = index.find("<!-- newest first -->")
    assert anchor >= 0
    # newest on top, both registered as in progress, below the anchor
    body = index[anchor:]
    assert body.find(dirs[1]) < body.find(dirs[0])
    assert body.count("(in progress)") == 2
    # collision handling: same slug again on the same day gets _2
    assert main(["run", "new", "other-task", "--project", str(proj)]) == 0
    assert (runs / f"{today}_other-task_2").is_dir()
    # refuses outside a project
    assert main(["run", "new", "x", "--project", str(tmp_path)]) == 2

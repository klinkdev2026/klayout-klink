"""Guard: the toolbar-tool DIRECTORY starters (examples_klink/public/features/
tools/<id>/) ship whole and byte-identical into the project template
(klink/templates/project/example_template/tools/<id>/), so a `klink init`
user -- whose agent sees only the template -- gets working examples to copy
and `klink tool install`. Also guards the recipe + the agent pointers that
make the feature discoverable from a scaffolded project.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "examples_klink" / "public"
PROJECT = ROOT / "klink" / "templates" / "project"
TEMPLATE_TOOLS = PROJECT / "example_template" / "tools"

sys.path.insert(0, str(PUBLIC))
from sync_to_template import TOOL_SRC, TOOL_STARTERS  # noqa: E402

RESYNC = "re-run `python examples_klink/public/sync_to_template.py`"


def _files(root: pathlib.Path) -> set:
    return {
        p.relative_to(root) for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }


def test_tool_starters_declared():
    assert TOOL_STARTERS, "sync_to_template declares no TOOL_STARTERS"
    assert len(set(TOOL_STARTERS)) == len(TOOL_STARTERS)


def test_every_tool_starter_folder_matches_source_byte_for_byte():
    for tool_id in TOOL_STARTERS:
        src, dst = TOOL_SRC / tool_id, TEMPLATE_TOOLS / tool_id
        assert (src / "tool.json").is_file() and (src / "run.py").is_file(), (
            f"tool starter source {src} lacks tool.json/run.py")
        assert dst.is_dir(), f"template is missing tools/{tool_id}/; {RESYNC}"
        src_files, dst_files = _files(src), _files(dst)
        assert src_files == dst_files, (
            f"tools/{tool_id}/ file set drifted (src-only "
            f"{sorted(map(str, src_files - dst_files))}, template-only "
            f"{sorted(map(str, dst_files - src_files))}); {RESYNC}")
        for rel in src_files:
            assert (src / rel).read_bytes() == (dst / rel).read_bytes(), (
                f"tools/{tool_id}/{rel} drifted from source; {RESYNC}")


def test_template_tools_dir_has_only_declared_starters():
    found = {p.name for p in TEMPLATE_TOOLS.iterdir() if p.is_dir()
             and p.name != "__pycache__"}
    assert found == set(TOOL_STARTERS), f"stale tool folders: {found - set(TOOL_STARTERS)}; {RESYNC}"


def test_wheel_glob_covers_nested_tool_folders():
    # package-data globs are NOT recursive unless written with **; the tool
    # starters sit two levels below example_template/.
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"templates/project/example_template/**/*"' in text


def test_recipe_and_agent_pointers_exist():
    recipe = PROJECT / "recipes" / "tools.md"
    assert recipe.is_file()
    body = recipe.read_text(encoding="utf-8")
    for needle in ("klink.cli tool install", ".klink/tools/", "tool.json",
                   "run.py", "export", "example_template/tools/"):
        assert needle in body, f"recipes/tools.md lost {needle!r}"
    assert "tools.md" in (PROJECT / "recipes" / "README.md").read_text(encoding="utf-8")
    for doc in ("AGENTS.md", "CLAUDE.md"):
        text = (PROJECT / doc).read_text(encoding="utf-8")
        assert "recipes/tools.md" in text and "domain=tools" in text, doc
    assert "tools/" in (PROJECT / "example_template" / "README.md").read_text(encoding="utf-8")


def test_project_gitignore_keeps_klink_tools_versioned():
    gi = (PROJECT / "gitignore").read_text(encoding="utf-8").splitlines()
    assert ".klink/" not in gi, "a bare .klink/ ignore also hides .klink/tools/"
    assert "!.klink/tools/" in gi


# ---- the command-driven pipeline (tool new -> check -> install -> run -> log) ----

PIPELINE = ("python -m klink.cli tool new ", "python -m klink.cli tool check ",
            "python -m klink.cli tool install ", "python -m klink.cli tool run ",
            "python -m klink.cli tool log ")
PIPELINE_ARROWS = "tool new -> check -> install -> run -> log"


def _assert_in_order(text: str, needles, where: str) -> None:
    pos = -1
    for needle in needles:
        nxt = text.find(needle, pos + 1)
        assert nxt > pos, f"{where}: {needle!r} missing or out of order"
        pos = nxt


def test_recipe_pipeline_commands_in_order_in_both_languages():
    body = (PROJECT / "recipes" / "tools.md").read_text(encoding="utf-8")
    assert "## 中文版" in body, "recipes/tools.md lost its Chinese pipeline"
    en, zh = body.split("## 中文版", 1)
    _assert_in_order(en, PIPELINE, "recipes/tools.md (English)")
    _assert_in_order(zh, PIPELINE, "recipes/tools.md (中文)")
    # every step has a gate, in both languages
    assert en.count("**Gate:**") >= 6 and zh.count("**闸门：**") >= 6
    for needle in ("ok: 0 error(s)", "toolbar: installed=", "ERR_TOOL_PARAMS",
                   "--from example_template/tools/", "--macro", "edit.undo",
                   "no runs recorded", "200+", "tool export"):
        assert needle in en, f"recipes/tools.md lost {needle!r}"
    # location-neutral: a klink init project has no dev-repo paths
    for dev_path in ("examples_klink/", "docs/TOOLS.md", "docs/TOOL_AUTHORING.md"):
        assert dev_path not in body, f"recipes/tools.md names dev-repo path {dev_path!r}"


def test_agent_pointers_name_the_pipeline():
    for doc in ("AGENTS.md", "CLAUDE.md"):
        text = (PROJECT / doc).read_text(encoding="utf-8")
        assert "recipes/tools.md" in text and PIPELINE_ARROWS in text, doc
    readme = (PROJECT / "example_template" / "README.md").read_text(encoding="utf-8")
    assert "tool new <id> --from" in readme


def test_skill_toolbar_section_is_the_pipeline_and_copies_identical():
    shipped = ROOT / "klink" / "mcp" / "skills" / "klayout" / "SKILL.md"
    local = ROOT / ".claude" / "skills" / "klayout" / "SKILL.md"
    assert shipped.read_bytes() == local.read_bytes(), (
        "copy klink/mcp/skills/klayout/SKILL.md to .claude/skills/klayout/SKILL.md")
    text = shipped.read_text(encoding="utf-8")
    section = text.split("## Toolbar buttons from a user's macro", 1)[1].split("\n## ", 1)[0]
    _assert_in_order(section, PIPELINE, "SKILL.md toolbar section")
    assert "recipes/tools.md" in section


def test_example_readmes_offer_a_variant_command():
    for tool_id in TOOL_STARTERS:
        for base in (TOOL_SRC, TEMPLATE_TOOLS):
            text = (base / tool_id / "README.md").read_text(encoding="utf-8")
            assert "tool new <new_id> --from <path to this folder>" in text, (base, tool_id)
            assert "tool install <path to this folder>" in text, (base, tool_id)

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from klink import buttons
from klink.cli import main as cli_main

PLUGIN_PYTHON = Path(__file__).resolve().parents[2] / "klink_plugin" / "python"


@pytest.fixture
def plugin_buttons():
    pytest.importorskip("klayout.db", reason="klayout pip package not installed")
    if str(PLUGIN_PYTHON) not in sys.path:
        sys.path.insert(0, str(PLUGIN_PYTHON))
    from klink_server import buttons as plugin

    return plugin


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("KLINK_REGISTRY_ROOT", str(tmp_path))
    return tmp_path


def test_define_list_remove(registry):
    path = buttons.define("alpha", "LBL", title="Alpha", definition="notes/a.md")
    assert path == registry / "buttons" / "alpha.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "id": "alpha", "label": "LBL", "title": "Alpha", "definition": "notes/a.md"}
    buttons.define("beta", "B")
    listed = buttons.list_defined()
    assert [e["id"] for e in listed] == ["alpha", "beta"]
    assert all(e["problem"] is None for e in listed)
    assert buttons.remove("alpha") is True
    assert buttons.remove("alpha") is False
    assert [e["id"] for e in buttons.list_defined()] == ["beta"]


def test_atomic_write_leaves_no_tmp(registry):
    buttons.define("alpha", "LBL")
    buttons.define("alpha", "LB2")
    assert sorted(p.name for p in (registry / "buttons").iterdir()) == ["alpha.json"]


@pytest.mark.parametrize("bid", ["Alpha", "1abc", "_a", "a-b", "a b", "", "a" * 41, "abc\n", "ab.c"])
def test_define_rejects_bad_ids(bid):
    with pytest.raises(ValueError):
        buttons.define(bid, "LBL")


def test_define_rejects_empty_label():
    with pytest.raises(ValueError):
        buttons.define("alpha", "   ")


CASES = [
    ({"id": "ok", "label": "L"}, "ok"),
    ({"id": "a" * 40, "label": "L"}, "a" * 40),
    ({"id": "a" * 41, "label": "L"}, "a" * 41),
    ({"id": "Up", "label": "L"}, "Up"),
    ({"id": "abc\n", "label": "L"}, "abc\n"),
    ({"id": "x", "label": "L"}, "y"),
    ({"id": "x", "label": ""}, "x"),
    ({"id": "x", "label": 3}, "x"),
    ({"id": "x", "label": "L", "title": 5}, "x"),
    ({"id": "x", "label": "L", "definition": ["a"]}, "x"),
    ({"id": "x_1", "label": "L", "title": None, "extra": 1}, "x_1"),
    ([1, 2], "x"),
    # icon: absolute .png/.svg path (existence is checked by define(), not by the validator)
    ({"id": "x", "label": "L", "icon": str(Path(__file__).resolve().with_name("i.png"))}, "x"),
    ({"id": "x", "label": "L", "icon": str(Path(__file__).resolve().with_name("i.SVG"))}, "x"),
    ({"id": "x", "label": "L", "icon": "icon.png"}, "x"),
    ({"id": "x", "label": "L", "icon": str(Path(__file__).resolve().with_name("i.jpg"))}, "x"),
    ({"id": "x", "label": "L", "icon": 3}, "x"),
    ({"id": "x", "label": "L", "icon": None}, "x"),
    ({"id": "x", "label": "L", "items": [{"id": "a", "icon": str(Path(__file__).resolve().with_name("a.png"))}]}, "x"),
]


@pytest.mark.parametrize("spec,stem", CASES)
def test_validation_parity_with_plugin(spec, stem, plugin_buttons):
    assert buttons.validate(spec, stem) == plugin_buttons.validate_manifest(spec, stem)


def test_regex_identical_to_plugin(plugin_buttons):
    assert buttons.ID_RE.pattern == plugin_buttons.ID_RE.pattern
    assert buttons.ICON_EXTS == plugin_buttons.ICON_EXTS
    assert buttons.ICON_REASON == plugin_buttons.ICON_REASON


def test_cli_add_list_remove(registry, capsys):
    assert cli_main(["button", "add", "spike_x", "--label", "SPK", "--title", "spike",
                     "--definition", "notes/t.md", "--no-reload"]) == 0
    assert (registry / "buttons" / "spike_x.json").exists()
    assert cli_main(["button", "list"]) == 0
    out = capsys.readouterr().out
    assert "spike_x" in out and "SPK" in out and "[ok]" in out
    assert cli_main(["button", "remove", "spike_x", "--no-reload"]) == 0
    assert not (registry / "buttons" / "spike_x.json").exists()
    assert cli_main(["button", "remove", "spike_x", "--no-reload"]) == 1


def test_cli_add_invalid_id(registry, capsys):
    assert cli_main(["button", "add", "Bad-Id", "--label", "X", "--no-reload"]) == 2
    assert "must match" in capsys.readouterr().out
    assert not (registry / "buttons").exists() or not any((registry / "buttons").iterdir())


def test_cli_list_reports_skipped(registry, capsys):
    folder = registry / "buttons"
    folder.mkdir()
    (folder / "broken.json").write_text("{nope", encoding="utf-8")
    assert cli_main(["button", "list"]) == 0
    assert "SKIPPED: unreadable" in capsys.readouterr().out


def test_reload_calls_rpc():
    class C:
        def call(self, method, params):
            self.seen = (method, params)
            return {"installed": []}
    c = C()
    assert buttons.reload(c) == {"installed": []}
    assert c.seen == ("buttons.reload", {})


# ------------------------------------------------------------ dropdowns --
ITEM_CASES = [
    ({"id": "x", "label": "L", "items": []}, "x"),
    ({"id": "x", "label": "L", "items": [{"id": "a"}, {"id": "b", "title": "B", "definition": "notes/b"}]}, "x"),
    ({"id": "x", "label": "L", "items": "a"}, "x"),
    ({"id": "x", "label": "L", "items": {"id": "a"}}, "x"),
    ({"id": "x", "label": "L", "items": ["a"]}, "x"),
    ({"id": "x", "label": "L", "items": [{"title": "no id"}]}, "x"),
    ({"id": "x", "label": "L", "items": [{"id": "Bad"}]}, "x"),
    ({"id": "x", "label": "L", "items": [{"id": "a" * 41}]}, "x"),
    ({"id": "x", "label": "L", "items": [{"id": "a"}, {"id": "a"}]}, "x"),
    ({"id": "x", "label": "L", "items": [{"id": "a", "title": 1}]}, "x"),
    ({"id": "x", "label": "L", "items": [{"id": "a", "definition": ["p"]}]}, "x"),
    ({"id": "x", "label": "L", "items": None}, "x"),
]


@pytest.mark.parametrize("spec,stem", ITEM_CASES)
def test_item_validation_parity_with_plugin(spec, stem, plugin_buttons):
    assert buttons.validate(spec, stem) == plugin_buttons.validate_manifest(spec, stem)


def _on_disk(registry, bid):
    return json.loads((registry / "buttons" / f"{bid}.json").read_text(encoding="utf-8"))


def test_define_with_items_written_as_given(registry):
    items = [{"id": "union", "title": "Union", "definition": "notes/u.md"}, {"id": "diff"}]
    buttons.define("bool_ops", "BOOL", title="Bool", items=items)
    assert _on_disk(registry, "bool_ops") == {"id": "bool_ops", "label": "BOOL", "title": "Bool",
                                              "items": items}


def test_define_rejects_duplicate_items(registry):
    with pytest.raises(ValueError, match="duplicate item id"):
        buttons.define("dd", "DD", items=[{"id": "a"}, {"id": "a"}])
    assert not (registry / "buttons" / "dd.json").exists()


def test_set_item_append_then_replace(registry):
    buttons.define("dd", "DD", items=[{"id": "one", "title": "First"}])
    buttons.set_item("dd", "two", title="Second")
    buttons.set_item("dd", "three", definition="notes/three.md")
    assert [it["id"] for it in _on_disk(registry, "dd")["items"]] == ["one", "two", "three"]
    buttons.set_item("dd", "one", title="Uno", definition="notes/1.md")  # replaced in place
    assert _on_disk(registry, "dd")["items"] == [
        {"id": "one", "title": "Uno", "definition": "notes/1.md"},
        {"id": "two", "title": "Second"}, {"id": "three", "definition": "notes/three.md"}]
    assert sorted(p.name for p in (registry / "buttons").iterdir()) == ["dd.json"]


def test_set_item_on_flat_button_makes_dropdown(registry):
    buttons.define("flat", "F", title="Flat")
    buttons.set_item("flat", "a")
    assert _on_disk(registry, "flat") == {"id": "flat", "label": "F", "title": "Flat", "items": [{"id": "a"}]}


def test_set_item_missing_button(registry):
    with pytest.raises(FileNotFoundError, match="klink button add nope"):
        buttons.set_item("nope", "a")


def test_set_item_bad_id_leaves_file(registry):
    buttons.define("dd", "DD", items=[{"id": "one"}])
    with pytest.raises(ValueError, match="must match"):
        buttons.set_item("dd", "Bad-Id")
    assert _on_disk(registry, "dd")["items"] == [{"id": "one"}]


def test_remove_item(registry):
    buttons.define("dd", "DD", items=[{"id": "one"}, {"id": "two"}])
    assert buttons.remove_item("dd", "one") is True
    assert buttons.remove_item("dd", "one") is False
    assert _on_disk(registry, "dd")["items"] == [{"id": "two"}]
    with pytest.raises(FileNotFoundError):
        buttons.remove_item("nope", "a")


def test_cli_add_with_items(registry, capsys):
    assert cli_main(["button", "add", "bool_ops", "--label", "BOOL",
                     "--item", "union=Union", "--item", "diff=Set: A=B",
                     "--item-definition", "union=notes/proj/u.md", "--no-reload"]) == 0
    assert _on_disk(registry, "bool_ops")["items"] == [
        {"id": "union", "title": "Union", "definition": "notes/proj/u.md"},
        {"id": "diff", "title": "Set: A=B"}]


def test_cli_add_item_errors(registry, capsys):
    assert cli_main(["button", "add", "dd", "--label", "DD", "--item", "noequals", "--no-reload"]) == 2
    assert "expected ID=TITLE" in capsys.readouterr().out
    assert cli_main(["button", "add", "dd", "--label", "DD", "--item", "a=A",
                     "--item-definition", "b=notes/b", "--no-reload"]) == 2
    assert "no --item b=TITLE" in capsys.readouterr().out
    assert cli_main(["button", "add", "dd", "--label", "DD", "--item", "a=A", "--item", "a=B",
                     "--no-reload"]) == 2
    assert not (registry / "buttons" / "dd.json").exists()


def test_cli_item_set_remove_list(registry, capsys):
    assert cli_main(["button", "add", "dd", "--label", "DD", "--title", "Drop",
                     "--item", "one=First", "--no-reload"]) == 0
    assert cli_main(["button", "item", "set", "dd", "two", "--title", "Second",
                     "--definition", "notes/two.md", "--no-reload"]) == 0
    assert cli_main(["button", "list"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    head = next(i for i, ln in enumerate(lines) if ln.strip().startswith("dd "))
    assert lines[head + 1].startswith("      - one") and "First" in lines[head + 1]
    assert lines[head + 2].startswith("      - two") and "notes/two.md" in lines[head + 2]
    assert cli_main(["button", "item", "remove", "dd", "one", "--no-reload"]) == 0
    assert [it["id"] for it in _on_disk(registry, "dd")["items"]] == ["two"]
    assert cli_main(["button", "item", "remove", "dd", "one", "--no-reload"]) == 1
    assert cli_main(["button", "item", "set", "nope", "a", "--no-reload"]) == 1
    assert "klink button add nope" in capsys.readouterr().out


# ------------------------------------------------------------ promotion --
PROMO_CASES = [
    ({"id": "x", "label": "L", "items": [{"id": "a", "label": "AA"}]}, "x"),
    ({"id": "x", "label": "L", "items": [{"id": "a", "label": 3}]}, "x"),
    ({"id": "x", "label": "L", "items": [{"id": "a", "label": None}]}, "x"),
    ({"id": "x", "label": "L", "items": [{"id": "a", "title": "A"}, {"id": "b", "label": "B"}]}, "x"),
    ({"id": "x", "label": "L", "items": [{"id": "a"}], "promote": False}, "x"),
    ({"id": "x", "label": "L", "items": [{"id": "a"}], "promote": True}, "x"),
    ({"id": "x", "label": "L", "items": [{"id": "a"}], "promote": None}, "x"),
    ({"id": "x", "label": "L", "items": [{"id": "a"}], "promote": "false"}, "x"),
    ({"id": "x", "label": "L", "items": [{"id": "a"}], "promote": 0}, "x"),
    ({"id": "x", "label": "L", "promote": 1}, "x"),
]

LABEL_REJECTED = "item 'label' is no longer supported; the entry's 'title' is shown on the parent"


@pytest.mark.parametrize("spec,stem", PROMO_CASES)
def test_item_label_promote_validation_parity_with_plugin(spec, stem, plugin_buttons):
    assert buttons.validate(spec, stem) == plugin_buttons.validate_manifest(spec, stem)


@pytest.mark.parametrize("value", ["AA", 3, None, ""])
def test_item_label_rejected_message(value, plugin_buttons):
    spec = {"id": "x", "label": "L", "items": [{"id": "a", "title": "A", "label": value}]}
    assert buttons.validate(spec, "x") == LABEL_REJECTED
    assert plugin_buttons.validate_manifest(spec, "x") == LABEL_REJECTED
    assert buttons.ITEM_LABEL_REJECTED == plugin_buttons.ITEM_LABEL_REJECTED == LABEL_REJECTED


def test_promote_validation_message():
    assert buttons.validate({"id": "x", "label": "L", "promote": "no"}, "x") \
        == "'promote' must be a boolean"


def test_define_rejects_item_label(registry):
    with pytest.raises(ValueError, match="item 'label' is no longer supported"):
        buttons.define("dd", "DD", items=[{"id": "a", "label": "AA"}])
    assert not (registry / "buttons" / "dd.json").exists()


def test_define_promote_only_when_given(registry):
    buttons.define("dd", "DD", items=[{"id": "a"}])
    assert "promote" not in _on_disk(registry, "dd")
    buttons.define("dd", "DD", items=[{"id": "a"}], promote=False)
    assert _on_disk(registry, "dd")["promote"] is False
    with pytest.raises(ValueError, match="'promote' must be a boolean"):
        buttons.define("dd", "DD", items=[{"id": "a"}], promote="no")


def test_set_item_has_no_label_kwarg(registry):
    buttons.define("dd", "DD", items=[{"id": "one"}])
    with pytest.raises(TypeError):
        buttons.set_item("dd", "one", title="First", label="ONE")
    buttons.set_item("dd", "one", title="First")
    assert _on_disk(registry, "dd")["items"] == [{"id": "one", "title": "First"}]


def test_set_item_on_stale_label_manifest_names_the_reason(registry):
    folder = registry / "buttons"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "dd.json").write_text(json.dumps(
        {"id": "dd", "label": "DD", "items": [{"id": "one", "title": "First", "label": "ONE"}]}),
        encoding="utf-8")
    with pytest.raises(ValueError, match="item 'label' is no longer supported"):
        buttons.set_item("dd", "two", title="Second")


def test_cli_no_promote_and_item_parsing(registry, capsys):
    assert cli_main(["button", "add", "dd", "--label", "DD", "--item", "one=First",
                     "--item", "two=Second", "--no-promote", "--no-reload"]) == 0
    spec = _on_disk(registry, "dd")
    assert spec["promote"] is False
    assert spec["items"] == [{"id": "one", "title": "First"}, {"id": "two", "title": "Second"}]
    assert cli_main(["button", "add", "dd2", "--label", "DD", "--item", "one", "--no-reload"]) == 2
    capsys.readouterr()
    assert cli_main(["button", "add", "dd2", "--label", "DD", "--item", "one=", "--no-reload"]) == 0
    assert "promote" not in _on_disk(registry, "dd2")


def test_cli_has_no_item_label_options(registry, capsys):
    with pytest.raises(SystemExit):
        cli_main(["button", "add", "dd3", "--label", "DD", "--item", "a=A",
                  "--item-label", "a=B", "--no-reload"])
    assert not (registry / "buttons" / "dd3.json").exists()
    assert cli_main(["button", "add", "dd", "--label", "DD", "--item", "one=First",
                     "--no-reload"]) == 0
    with pytest.raises(SystemExit):
        cli_main(["button", "item", "set", "dd", "one", "--label", "ONE", "--no-reload"])
    capsys.readouterr()


def test_cli_item_set_and_list(registry, capsys):
    assert cli_main(["button", "add", "dd", "--label", "DD", "--item", "one=First",
                     "--no-reload"]) == 0
    assert cli_main(["button", "item", "set", "dd", "one", "--title", "Uno",
                     "--no-reload"]) == 0
    assert _on_disk(registry, "dd")["items"] == [{"id": "one", "title": "Uno"}]
    capsys.readouterr()
    assert cli_main(["button", "list"]) == 0
    line = next(ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("      - one"))
    assert line.split() == ["-", "one", "Uno"]


def test_cli_list_shows_stale_label_reason(registry, capsys):
    folder = registry / "buttons"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "dd.json").write_text(json.dumps(
        {"id": "dd", "label": "DD", "items": [{"id": "one", "label": "ONE"}]}), encoding="utf-8")
    assert cli_main(["button", "list"]) == 0
    assert "SKIPPED: item 'label' is no longer supported" in capsys.readouterr().out

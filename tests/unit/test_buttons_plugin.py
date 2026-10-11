from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

PLUGIN_PYTHON = Path(__file__).resolve().parents[2] / "klink_plugin" / "python"
if str(PLUGIN_PYTHON) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PYTHON))

pytest.importorskip("klayout.db", reason="klayout pip package not installed")

from klink_server import buttons  # noqa: E402


# --------------------------------------------------------------- fakes --
class FakeAction:
    def __init__(self):
        self.title = ""
        self.icon_text = ""
        self.tool_tip = ""
        self.on_triggered = None

    def trigger(self):
        self.on_triggered()


class FakePya:
    Action = FakeAction


class FakeMenu:
    """Models KLayout's AbstractMenu enough for flat items and toolbar dropdowns."""

    def __init__(self):
        self.calls = []
        self.nodes = {}   # path -> action, insertion order == menu order
        self.menus = set()  # paths created by insert_menu

    @staticmethod
    def _parent(where):
        return where[: -len(".end")] if where.endswith(".end") else where

    def insert_item(self, where, item_id, action):
        # Like KLayout: "@toolbar.end" is a POSITION; the item lands at "@toolbar.<name>".
        self.calls.append(("insert", where, item_id))
        parent = self._parent(where)
        assert parent == "@toolbar" or parent in self.menus, f"no menu at {parent}"
        self.nodes[f"{parent}.{item_id}"] = action

    def insert_menu(self, where, name, title):
        self.calls.append(("insert_menu", where, name))
        path = f"{self._parent(where)}.{name}"
        action = FakeAction()
        action.title = title
        self.nodes[path] = action
        self.menus.add(path)

    def action(self, path):
        self.calls.append(("action", path))
        return self.nodes[path]

    def items(self, path):
        return [p for p in self.nodes if p.startswith(path + ".") and "." not in p[len(path) + 1:]]

    def is_valid(self, path):
        return path in self.nodes

    def delete_item(self, path):
        self.calls.append(("delete", path))
        for p in [p for p in self.nodes if p == path or p.startswith(path + ".")]:
            del self.nodes[p]
        self.menus = {m for m in self.menus if not (m == path or m.startswith(path + "."))}

    def inserts(self):
        return [c for c in self.calls if c[0] == "insert"]

    def reset_log(self):
        self.calls = []


class FakeEvents:
    def __init__(self):
        self.emitted = []

    def emit(self, channel, data):
        self.emitted.append((channel, data))
        return 1


class FakeServer:
    def __init__(self, session_id="klayout-9999"):
        self.session_id = session_id
        self.events = FakeEvents()


def _fake_server_module(monkeypatch, srv):
    # klink_server.server needs Qt networking classes the pip klayout lacks; stand it in.
    mod = types.ModuleType("klink_server.server")
    mod.instance = lambda: srv
    monkeypatch.setitem(sys.modules, "klink_server.server", mod)


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("KLINK_REGISTRY_ROOT", str(tmp_path))
    buttons._installed.clear()
    yield
    buttons._installed.clear()


def _write(root: Path, stem: str, payload) -> Path:
    path = root / "buttons" / f"{stem}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def _btn(bid, label="LBL", **extra):
    return {"id": bid, "label": label, **extra}


# --------------------------------------------------------------- tests --
def test_add_installs_button(tmp_path):
    _write(tmp_path, "alpha", _btn("alpha", title="Alpha tool", definition="notes/x/alpha.md"))
    menu = FakeMenu()
    report = buttons.install_all(menu, FakePya)
    assert report["installed"] == ["alpha"]
    assert report["updated"] == report["removed"] == report["skipped"] == []
    action = menu.nodes["@toolbar.klink_button_alpha"]
    assert (action.title, action.icon_text, action.tool_tip) == ("Alpha tool", "LBL", "Alpha tool")
    assert report["buttons"] == [{"id": "alpha", "label": "LBL", "title": "Alpha tool",
                                  "definition": "notes/x/alpha.md"}]
    assert buttons.current() == report["buttons"]


def test_defaults_and_label_cut(tmp_path):
    _write(tmp_path, "beta", _btn("beta", label="  VERYLONGLABEL "))
    buttons.install_all(FakeMenu(), FakePya)
    assert buttons.current() == [{"id": "beta", "label": "VERYLO", "title": "beta", "definition": None}]


def test_second_sync_without_change_is_noop(tmp_path):
    _write(tmp_path, "alpha", _btn("alpha"))
    menu = FakeMenu()
    buttons.install_all(menu, FakePya)
    before = list(menu.calls)
    report = buttons.install_all(menu, FakePya)
    assert report["installed"] == report["updated"] == report["removed"] == []
    assert menu.calls == before


def test_edit_updates_in_place(tmp_path):
    _write(tmp_path, "alpha", _btn("alpha"))
    menu = FakeMenu()
    buttons.install_all(menu, FakePya)
    action = buttons._installed["alpha"]["action"]
    _write(tmp_path, "alpha", _btn("alpha", label="NEW", title="New title", definition="d2"))
    report = buttons.install_all(menu, FakePya)
    assert report["updated"] == ["alpha"] and report["installed"] == []
    assert len(menu.inserts()) == 1
    assert buttons._installed["alpha"]["action"] is action
    assert (action.icon_text, action.title, action.tool_tip) == ("NEW", "New title", "New title")
    assert buttons.current()[0]["definition"] == "d2"


def test_deleted_manifest_removes_button(tmp_path):
    path = _write(tmp_path, "alpha", _btn("alpha"))
    _write(tmp_path, "gamma", _btn("gamma"))
    menu = FakeMenu()
    buttons.install_all(menu, FakePya)
    path.unlink()
    report = buttons.install_all(menu, FakePya)
    assert report["removed"] == ["alpha"]
    assert ("delete", "@toolbar.klink_button_alpha") in menu.calls
    assert "@toolbar.klink_button_alpha" not in menu.nodes
    assert [b["id"] for b in buttons.current()] == ["gamma"]


def test_invalid_files_skipped_others_load(tmp_path):
    _write(tmp_path, "good", _btn("good"))
    _write(tmp_path, "Bad", _btn("Bad"))
    _write(tmp_path, "stem", _btn("other"))
    _write(tmp_path, "broken", "{not json")
    _write(tmp_path, "nolabel", {"id": "nolabel", "label": "  "})
    report = buttons.install_all(FakeMenu(), FakePya)
    assert report["installed"] == ["good"]
    reasons = {s["file"]: s["reason"] for s in report["skipped"]}
    assert set(reasons) == {"Bad.json", "stem.json", "broken.json", "nolabel.json"}
    assert "does not match the file name" in reasons["stem.json"]
    assert reasons["broken.json"].startswith("unreadable")


def test_trigger_journals_then_emits(tmp_path, monkeypatch):
    fake = FakeServer()
    _fake_server_module(monkeypatch, fake)
    _write(tmp_path, "alpha", _btn("alpha", title="A", definition="notes/a.md"))
    buttons.install_all(FakeMenu(), FakePya)
    action = buttons._installed["alpha"]["action"]
    action.trigger()
    action.trigger()
    lines = (tmp_path / "journals" / "klayout-9999.buttons.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert [r["seq"] for r in records] == [1, 2]
    r = records[0]
    assert (r["button"], r["definition"], r["title"], r["session_id"]) == ("alpha", "notes/a.md", "A", "klayout-9999")
    assert isinstance(r["ts"], float)
    assert [c for c, _ in fake.events.emitted] == ["button_triggered", "button_triggered"]
    assert fake.events.emitted[1][1]["seq"] == 2


def test_trigger_without_server_journals_unknown(tmp_path, monkeypatch):
    _fake_server_module(monkeypatch, None)
    _write(tmp_path, "alpha", _btn("alpha"))
    buttons.install_all(FakeMenu(), FakePya)
    buttons._installed["alpha"]["action"].trigger()
    rec = json.loads((tmp_path / "journals" / "unknown.buttons.jsonl").read_text(encoding="utf-8"))
    assert rec["seq"] == 1 and rec["session_id"] == "unknown" and rec["definition"] is None


def test_trigger_failure_never_raises(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    _fake_server_module(monkeypatch, None)
    monkeypatch.setattr(buttons, "journal_append", boom)
    buttons._on_trigger({"id": "alpha", "title": "alpha", "definition": None})  # must not raise


def test_restart_path_installs_existing(tmp_path):
    _write(tmp_path, "alpha", _btn("alpha"))
    _write(tmp_path, "beta", _btn("beta"))
    buttons.install_all(FakeMenu(), FakePya)
    buttons._installed.clear()  # new KLayout process: module state is fresh
    menu = FakeMenu()
    report = buttons.install_all(menu, FakePya)
    assert report["installed"] == ["alpha", "beta"]
    # a stale item from a previous macro run is deleted before insert
    assert menu.calls[0] == ("delete", "@toolbar.klink_button_alpha")


def test_missing_dir_is_empty(tmp_path):
    assert buttons.load_manifests(tmp_path / "nowhere") == ([], [])
    assert buttons.install_all(FakeMenu(), FakePya)["buttons"] == []


# ------------------------------------------------------------ dropdowns --
DD = "@toolbar.klink_button_dd"


def _dd(items, **extra):
    return _btn("dd", label="DD", title="Drop", definition="notes/dd.md", items=items, **extra)


def _three():
    return [{"id": "one", "title": "First"}, {"id": "two"},
            {"id": "three", "title": "Third", "definition": "notes/three.md"}]


def test_dropdown_created_with_parent_texts_and_items(tmp_path):
    _write(tmp_path, "dd", _dd(_three()))
    menu = FakeMenu()
    report = buttons.install_all(menu, FakePya)
    assert report["installed"] == ["dd"] and report["items"] == {}
    assert ("insert_menu", "@toolbar.end", "klink_button_dd") in menu.calls
    parent = menu.nodes[DD]
    assert buttons._installed["dd"]["action"] is parent
    # promoted: the first item is current; the parent shows its title verbatim
    assert (parent.title, parent.icon_text, parent.tool_tip) == ("Drop", "First", "Drop: First")
    assert menu.inserts() == [("insert", DD + ".end", s) for s in ("one", "two", "three")]
    assert menu.items(DD) == [DD + ".one", DD + ".two", DD + ".three"]
    assert [menu.nodes[DD + "." + s].title for s in ("one", "two", "three")] == ["First", "two", "Third"]
    subs = buttons._installed["dd"]["items"]
    assert set(subs) == {"one", "two", "three"} and subs["one"] is menu.nodes[DD + ".one"]
    assert buttons.current()[0]["items"] == [
        {"id": "one", "title": "First", "definition": "notes/dd.md"},
        {"id": "two", "title": "two", "definition": "notes/dd.md"},
        {"id": "three", "title": "Third", "definition": "notes/three.md"}]
    assert buttons.current()[0]["current"] == "one" and buttons.current()[0]["promote"] is True


def test_empty_items_is_flat(tmp_path):
    _write(tmp_path, "dd", _dd([]))
    menu = FakeMenu()
    buttons.install_all(menu, FakePya)
    assert menu.inserts() == [("insert", "@toolbar.end", "klink_button_dd")]
    assert "items" not in buttons.current()[0] and "items" not in buttons._installed["dd"]


def _installed_dd(tmp_path, items):
    _write(tmp_path, "dd", _dd(items))
    menu = FakeMenu()
    buttons.install_all(menu, FakePya)
    menu.reset_log()
    return menu


def test_dropdown_noop_resync(tmp_path):
    menu = _installed_dd(tmp_path, _three())
    report = buttons.install_all(menu, FakePya)
    assert report["updated"] == [] and report["items"] == {} and menu.calls == []


def test_add_one_item_touches_only_it(tmp_path):
    menu = _installed_dd(tmp_path, _three())
    keep = dict(buttons._installed["dd"]["items"])
    _write(tmp_path, "dd", _dd(_three() + [{"id": "four", "title": "Fourth"}]))
    report = buttons.install_all(menu, FakePya)
    assert menu.calls == [("insert", DD + ".end", "four")]
    assert report["updated"] == ["dd"] and report["items"] == {"dd": {"items_added": ["four"]}}
    assert menu.items(DD)[-1] == DD + ".four"
    for sid, act in keep.items():
        assert buttons._installed["dd"]["items"][sid] is act


def test_remove_one_item_touches_only_it(tmp_path):
    menu = _installed_dd(tmp_path, _three())
    items = _three()
    del items[1]
    _write(tmp_path, "dd", _dd(items))
    report = buttons.install_all(menu, FakePya)
    assert menu.calls == [("delete", DD + ".two")]
    assert report["items"] == {"dd": {"items_removed": ["two"]}}
    assert menu.items(DD) == [DD + ".one", DD + ".three"]
    assert set(buttons._installed["dd"]["items"]) == {"one", "three"}


def test_retitle_one_item_in_place(tmp_path):
    menu = _installed_dd(tmp_path, _three())
    action = buttons._installed["dd"]["items"]["two"]
    items = _three()
    items[1] = {"id": "two", "title": "Second", "definition": "notes/two.md"}
    _write(tmp_path, "dd", _dd(items))
    report = buttons.install_all(menu, FakePya)
    assert menu.calls == []  # no insert, no delete: text changed on the live action
    assert report["items"] == {"dd": {"items_updated": ["two"]}}
    assert action.title == "Second" and buttons._installed["dd"]["items"]["two"] is action
    assert buttons.current()[0]["items"][1]["definition"] == "notes/two.md"


def test_parent_retitle_keeps_items(tmp_path):
    menu = _installed_dd(tmp_path, _three())
    parent = buttons._installed["dd"]["action"]
    _write(tmp_path, "dd", _btn("dd", label="NEW", title="New drop", definition="notes/dd.md", items=_three()))
    report = buttons.install_all(menu, FakePya)
    assert menu.calls == [] and report["updated"] == ["dd"]
    # items no longer inherit the button label: a parent label change leaves them alone
    assert report["items"] == {}
    assert (parent.icon_text, parent.title, parent.tool_tip) == ("First", "New drop", "New drop: First")


def test_reorder_rebuilds_items_only(tmp_path):
    menu = _installed_dd(tmp_path, _three())
    parent = buttons._installed["dd"]["action"]
    items = _three()
    items.reverse()
    _write(tmp_path, "dd", _dd(items))
    report = buttons.install_all(menu, FakePya)
    assert ("delete", DD) not in menu.calls and not any(c[0] == "insert_menu" for c in menu.calls)
    assert [c for c in menu.calls if c[0] == "delete"] == [("delete", DD + "." + s) for s in ("one", "two", "three")]
    assert menu.inserts() == [("insert", DD + ".end", s) for s in ("three", "two", "one")]
    assert menu.items(DD) == [DD + ".three", DD + ".two", DD + ".one"]
    assert buttons._installed["dd"]["action"] is parent
    assert report["items"]["dd"].get("items_reordered") is True


def test_insert_in_middle_rebuilds_to_keep_file_order(tmp_path):
    menu = _installed_dd(tmp_path, [{"id": "one"}, {"id": "three"}])
    _write(tmp_path, "dd", _dd([{"id": "one"}, {"id": "two"}, {"id": "three"}]))
    report = buttons.install_all(menu, FakePya)
    assert menu.items(DD) == [DD + ".one", DD + ".two", DD + ".three"]
    assert report["items"]["dd"]["items_added"] == ["two"]


def test_flat_to_dropdown_and_back_recreates(tmp_path):
    _write(tmp_path, "dd", _btn("dd"))
    menu = FakeMenu()
    buttons.install_all(menu, FakePya)
    menu.reset_log()
    _write(tmp_path, "dd", _dd(_three()))
    report = buttons.install_all(menu, FakePya)
    assert report["updated"] == ["dd"] and report["installed"] == []
    assert menu.calls[0] == ("delete", DD)
    assert ("insert_menu", "@toolbar.end", "klink_button_dd") in menu.calls
    assert len(menu.items(DD)) == 3
    menu.reset_log()
    _write(tmp_path, "dd", _btn("dd"))
    report = buttons.install_all(menu, FakePya)
    assert report["updated"] == ["dd"]
    assert menu.calls == [("delete", DD), ("insert", "@toolbar.end", "klink_button_dd")]
    assert menu.items(DD) == [] and "items" not in buttons._installed["dd"]


def test_dropdown_manifest_vanished_one_delete(tmp_path):
    menu = _installed_dd(tmp_path, _three())
    (tmp_path / "buttons" / "dd.json").unlink()
    report = buttons.install_all(menu, FakePya)
    assert report["removed"] == ["dd"]
    assert menu.calls == [("delete", DD)]
    assert not any(p.startswith(DD) for p in menu.nodes)


def test_item_trigger_records_item_and_definition(tmp_path, monkeypatch):
    fake = FakeServer()
    _fake_server_module(monkeypatch, fake)
    _write(tmp_path, "dd", _dd(_three()))
    buttons.install_all(FakeMenu(), FakePya)
    subs = buttons._installed["dd"]["items"]
    subs["one"].trigger()
    subs["three"].trigger()
    lines = (tmp_path / "journals" / "klayout-9999.buttons.jsonl").read_text(encoding="utf-8").splitlines()
    r1, r3 = (json.loads(x) for x in lines)
    assert (r1["button"], r1["item"], r1["title"], r1["definition"]) == ("dd", "one", "First", "notes/dd.md")
    assert (r3["item"], r3["title"], r3["definition"]) == ("three", "Third", "notes/three.md")
    assert [r1["seq"], r3["seq"]] == [1, 2]
    assert fake.events.emitted[0] == ("button_triggered", r1)


def test_item_trigger_resolves_current_manifest(tmp_path, monkeypatch):
    _fake_server_module(monkeypatch, None)
    menu = _installed_dd(tmp_path, _three())
    action = buttons._installed["dd"]["items"]["two"]
    items = _three()
    items[1] = {"id": "two", "title": "Second", "definition": "notes/new.md"}
    _write(tmp_path, "dd", _dd(items))
    buttons.install_all(menu, FakePya)
    action.trigger()
    rec = json.loads((tmp_path / "journals" / "unknown.buttons.jsonl").read_text(encoding="utf-8"))
    assert (rec["item"], rec["title"], rec["definition"]) == ("two", "Second", "notes/new.md")


def test_flat_trigger_has_item_none(tmp_path, monkeypatch):
    _fake_server_module(monkeypatch, None)
    _write(tmp_path, "alpha", _btn("alpha"))
    buttons.install_all(FakeMenu(), FakePya)
    buttons._installed["alpha"]["action"].trigger()
    rec = json.loads((tmp_path / "journals" / "unknown.buttons.jsonl").read_text(encoding="utf-8"))
    assert "item" in rec and rec["item"] is None


def test_restart_rebuilds_dropdown(tmp_path):
    _write(tmp_path, "dd", _dd(_three()))
    buttons.install_all(FakeMenu(), FakePya)
    buttons._installed.clear()  # new KLayout process
    menu = FakeMenu()
    report = buttons.install_all(menu, FakePya)
    assert report["installed"] == ["dd"]
    assert menu.calls[0] == ("delete", DD)  # stale entry from a previous macro run
    assert menu.items(DD) == [DD + ".one", DD + ".two", DD + ".three"]


@pytest.mark.parametrize("items,needle", [
    ("x", "'items' must be a list"),
    (["x"], "items[0] is not a JSON object"),
    ([{"id": "Bad"}], "items[0].id must match"),
    ([{"id": "a"}, {"id": "a"}], "duplicate item id 'a'"),
    ([{"id": "a", "title": 3}], "items[0].title must be a string"),
    ([{"id": "a", "definition": 3}], "items[0].definition must be a string"),
])
def test_bad_items_skip_whole_manifest(tmp_path, items, needle):
    _write(tmp_path, "dd", _dd(items))
    report = buttons.install_all(FakeMenu(), FakePya)
    assert report["installed"] == [] and needle in report["skipped"][0]["reason"]


# ------------------------------------------------------------ promotion --
def _promo_items():
    return [{"id": "one", "title": "First"},
            {"id": "two", "title": "Second"},
            {"id": "three", "title": "A rather long entry title"}]


def _records(tmp_path, session="unknown"):
    path = tmp_path / "journals" / f"{session}.buttons.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def _texts(action):
    return action.title, action.icon_text, action.tool_tip


def test_promotion_create_parent_click_and_item_click(tmp_path, monkeypatch):
    _fake_server_module(monkeypatch, None)
    _write(tmp_path, "dd", _dd(_promo_items()))
    menu = FakeMenu()
    buttons.install_all(menu, FakePya)
    entry = buttons._installed["dd"]
    parent = menu.nodes[DD]
    assert entry["current"] == "one"
    assert _texts(parent) == ("Drop", "First", "Drop: First")
    parent.trigger()  # main part of the toolbar button
    rec = _records(tmp_path)[-1]
    assert (rec["item"], rec["via"], rec["title"]) == ("one", "parent", "First")
    entry["items"]["two"].trigger()  # dropdown entry
    rec = _records(tmp_path)[-1]
    assert (rec["item"], rec["via"], rec["title"]) == ("two", "item", "Second")
    assert entry["current"] == "two"
    assert _texts(parent) == ("Drop", "Second", "Drop: Second")
    parent.trigger()
    rec = _records(tmp_path)[-1]
    assert (rec["item"], rec["via"]) == ("two", "parent")
    assert [r["seq"] for r in _records(tmp_path)] == [1, 2, 3]
    assert buttons.current()[0]["current"] == "two"
    entry["items"]["three"].trigger()  # the clicked entry's name goes up uncut
    assert _texts(parent) == ("Drop", "A rather long entry title", "Drop: A rather long entry title")


def test_promoted_text_is_title_or_id(tmp_path, monkeypatch):
    _fake_server_module(monkeypatch, None)
    _write(tmp_path, "dd", _dd([{"id": "one"}, {"id": "two", "title": "Second"}]))
    menu = FakeMenu()
    buttons.install_all(menu, FakePya)
    assert _texts(menu.nodes[DD]) == ("Drop", "one", "Drop: one")
    buttons._installed["dd"]["items"]["two"].trigger()
    assert _texts(menu.nodes[DD]) == ("Drop", "Second", "Drop: Second")
    assert all("label" not in it for it in buttons.current()[0]["items"])


LABEL_REJECTED = "item 'label' is no longer supported; the entry's 'title' is shown on the parent"


def test_stale_item_label_manifest_skipped_with_reason(tmp_path):
    _write(tmp_path, "dd", _dd([{"id": "one", "title": "First", "label": "ONE"}]))
    _write(tmp_path, "alpha", _btn("alpha"))
    report = buttons.install_all(FakeMenu(), FakePya)
    assert report["installed"] == ["alpha"]
    assert report["skipped"] == [{"file": "dd.json", "reason": LABEL_REJECTED}]


def test_no_promote_keeps_texts_but_parent_runs_current(tmp_path, monkeypatch):
    _fake_server_module(monkeypatch, None)
    _write(tmp_path, "dd", _dd(_promo_items(), promote=False))
    menu = FakeMenu()
    buttons.install_all(menu, FakePya)
    parent = menu.nodes[DD]
    assert buttons.current()[0]["promote"] is False
    assert _texts(parent) == ("Drop", "DD", "Drop")
    buttons._installed["dd"]["items"]["two"].trigger()
    assert _texts(parent) == ("Drop", "DD", "Drop")
    assert buttons._installed["dd"]["current"] == "two"
    parent.trigger()
    rec = _records(tmp_path)[-1]
    assert (rec["item"], rec["via"]) == ("two", "parent")


def test_toggle_promote_reapplies_texts(tmp_path):
    menu = _installed_dd(tmp_path, _promo_items())
    parent = buttons._installed["dd"]["action"]
    _write(tmp_path, "dd", _dd(_promo_items(), promote=False))
    buttons.install_all(menu, FakePya)
    assert _texts(parent) == ("Drop", "DD", "Drop") and menu.calls == []
    _write(tmp_path, "dd", _dd(_promo_items()))
    buttons.install_all(menu, FakePya)
    assert _texts(parent) == ("Drop", "First", "Drop: First")


def test_remove_current_item_falls_back_to_first(tmp_path, monkeypatch):
    _fake_server_module(monkeypatch, None)
    menu = _installed_dd(tmp_path, _promo_items())
    buttons._installed["dd"]["items"]["two"].trigger()
    items = _promo_items()
    del items[1]
    _write(tmp_path, "dd", _dd(items))
    buttons.install_all(menu, FakePya)
    assert buttons._installed["dd"]["current"] == "one"
    assert _texts(buttons._installed["dd"]["action"]) == ("Drop", "First", "Drop: First")


def test_retitle_current_item_reapplies_texts(tmp_path):
    menu = _installed_dd(tmp_path, _promo_items())
    parent = buttons._installed["dd"]["action"]
    items = _promo_items()
    items[0] = {"id": "one", "title": "Uno"}
    _write(tmp_path, "dd", _dd(items))
    buttons.install_all(menu, FakePya)
    assert menu.calls == []
    assert _texts(parent) == ("Drop", "Uno", "Drop: Uno")
    assert buttons._installed["dd"]["current"] == "one"


def test_other_item_change_keeps_current_texts(tmp_path):
    menu = _installed_dd(tmp_path, _promo_items())
    parent = buttons._installed["dd"]["action"]
    parent.icon_text = "SENTINEL"  # would be overwritten by a needless re-apply
    items = _promo_items()
    items[1] = {"id": "two", "title": "Zwei"}
    _write(tmp_path, "dd", _dd(items))
    buttons.install_all(menu, FakePya)
    assert parent.icon_text == "SENTINEL"


def test_restart_restores_current_from_journal(tmp_path, monkeypatch):
    _fake_server_module(monkeypatch, FakeServer("klayout-9999"))
    _write(tmp_path, "dd", _dd(_promo_items()))
    buttons.journal_append("klayout-9999", {"button": "dd", "item": "three", "via": "item"})
    buttons.journal_append("klayout-9999", {"button": "other", "item": "x", "via": "item"})
    buttons.journal_append("klayout-9999", {"button": "dd", "item": "two", "via": "item"})
    buttons.journal_append("klayout-9999", {"button": "dd", "item": None, "via": None})
    buttons._installed.clear()  # new KLayout process
    menu = FakeMenu()
    buttons.install_all(menu, FakePya)
    assert buttons._installed["dd"]["current"] == "two"
    assert _texts(menu.nodes[DD]) == ("Drop", "Second", "Drop: Second")


def test_restore_reads_rotated_journal_first(tmp_path, monkeypatch):
    path = buttons.journal_path("klayout-9999")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix(".jsonl.1").write_text(
        json.dumps({"seq": 1, "button": "dd", "item": "three"}) + "\n", encoding="utf-8")
    path.write_text("not json\n" + json.dumps({"seq": 2, "button": "dd", "item": "two"}) + "\n",
                    encoding="utf-8")
    assert buttons._last_items_from_journal("klayout-9999") == {"dd": "two"}


def test_restart_stale_journal_item_uses_first(tmp_path, monkeypatch):
    _fake_server_module(monkeypatch, FakeServer("klayout-9999"))
    _write(tmp_path, "dd", _dd(_promo_items()))
    buttons.journal_append("klayout-9999", {"button": "dd", "item": "gone", "via": "item"})
    buttons.install_all(FakeMenu(), FakePya)
    assert buttons._installed["dd"]["current"] == "one"
    assert buttons._installed["dd"]["action"].icon_text == "First"


def test_journal_read_once_and_only_for_dropdowns(tmp_path, monkeypatch):
    _fake_server_module(monkeypatch, None)
    calls = []
    real = buttons._last_items_from_journal
    monkeypatch.setattr(buttons, "_last_items_from_journal", lambda s: calls.append(s) or real(s))
    _write(tmp_path, "alpha", _btn("alpha"))
    buttons.install_all(FakeMenu(), FakePya)
    assert calls == []
    _write(tmp_path, "dd", _dd(_promo_items()))
    _write(tmp_path, "ee", _btn("ee", items=[{"id": "a"}]))
    buttons._installed.clear()
    buttons.install_all(FakeMenu(), FakePya)
    assert calls == ["unknown"]


def test_flat_to_dropdown_restores_from_journal(tmp_path, monkeypatch):
    _fake_server_module(monkeypatch, None)
    _write(tmp_path, "dd", _btn("dd"))
    menu = FakeMenu()
    buttons.install_all(menu, FakePya)
    buttons.journal_append("unknown", {"button": "dd", "item": "three", "via": "item"})
    _write(tmp_path, "dd", _dd(_promo_items()))
    buttons.install_all(menu, FakePya)
    assert buttons._installed["dd"]["current"] == "three"


def test_flat_button_unaffected(tmp_path, monkeypatch):
    _fake_server_module(monkeypatch, None)
    _write(tmp_path, "alpha", _btn("alpha", title="A"))
    buttons.install_all(FakeMenu(), FakePya)
    assert "current" not in buttons._installed["alpha"]
    assert "current" not in buttons.current()[0] and "promote" not in buttons.current()[0]
    buttons._installed["alpha"]["action"].trigger()
    rec = _records(tmp_path)[-1]
    assert rec["item"] is None and rec["via"] is None


@pytest.mark.parametrize("spec,needle", [
    (_btn("dd", items=[{"id": "a", "label": 3}]), LABEL_REJECTED),
    (_btn("dd", items=[{"id": "a", "label": "AA"}]), LABEL_REJECTED),
    (_btn("dd", items=[{"id": "a"}], promote="yes"), "'promote' must be a boolean"),
    (_btn("dd", promote=1), "'promote' must be a boolean"),
])
def test_bad_label_or_promote_skipped(tmp_path, spec, needle):
    _write(tmp_path, "dd", spec)
    report = buttons.install_all(FakeMenu(), FakePya)
    assert report["installed"] == [] and needle in report["skipped"][0]["reason"]


# ---------------------------------------------------------------- icons --
def _icon(tmp_path, name="i.png") -> str:
    path = tmp_path / "icons" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n")  # content is Qt's business; the plugin only checks it exists
    return str(path.resolve())


def test_no_icon_never_touches_action_icon(tmp_path):
    _write(tmp_path, "alpha", _btn("alpha"))
    _write(tmp_path, "dd", _btn("dd", items=[{"id": "one"}]))
    menu = FakeMenu()
    report = buttons.install_all(menu, FakePya)
    assert "icon" not in vars(menu.nodes["@toolbar.klink_button_alpha"])
    assert "icon" not in vars(menu.nodes["@toolbar.klink_button_dd"])
    assert all("icon" not in b for b in report["buttons"])


def test_icon_set_on_flat_and_dropdown_parent_text_kept(tmp_path):
    icon = _icon(tmp_path)
    _write(tmp_path, "alpha", _btn("alpha", title="Alpha", icon=icon))
    _write(tmp_path, "dd", _btn("dd", label="DD", icon=icon, items=[{"id": "one", "title": "One"}]))
    menu = FakeMenu()
    report = buttons.install_all(menu, FakePya)
    flat = menu.nodes["@toolbar.klink_button_alpha"]
    assert (flat.icon, flat.icon_text, flat.tool_tip) == (icon, "LBL", "Alpha")
    parent = menu.nodes["@toolbar.klink_button_dd"]
    assert parent.icon == icon and parent.icon_text == "One"
    assert "icon" not in vars(menu.nodes["@toolbar.klink_button_dd.one"])  # entries take no icon
    assert {b["id"]: b.get("icon") for b in report["buttons"]} == {"alpha": icon, "dd": icon}


def test_missing_icon_file_warns_and_keeps_text_button(tmp_path):
    gone = str((tmp_path / "nope" / "gone.png").resolve())
    _write(tmp_path, "alpha", _btn("alpha", icon=gone))
    menu = FakeMenu()
    report = buttons.install_all(menu, FakePya)
    assert report["installed"] == ["alpha"] and report["skipped"] == []
    action = menu.nodes["@toolbar.klink_button_alpha"]
    assert "icon" not in vars(action) and action.icon_text == "LBL"


def test_icon_added_changed_and_removed_on_resync(tmp_path):
    a, b = _icon(tmp_path, "a.png"), _icon(tmp_path, "b.svg")
    _write(tmp_path, "alpha", _btn("alpha"))
    _write(tmp_path, "dd", _btn("dd", items=[{"id": "one"}]))
    menu = FakeMenu()
    buttons.install_all(menu, FakePya)
    for icon in (a, b):
        _write(tmp_path, "alpha", _btn("alpha", icon=icon))
        _write(tmp_path, "dd", _btn("dd", icon=icon, items=[{"id": "one"}]))
        assert sorted(buttons.install_all(menu, FakePya)["updated"]) == ["alpha", "dd"]
        assert menu.nodes["@toolbar.klink_button_alpha"].icon == icon
        assert menu.nodes["@toolbar.klink_button_dd"].icon == icon
    _write(tmp_path, "alpha", _btn("alpha"))
    _write(tmp_path, "dd", _btn("dd", items=[{"id": "one"}]))
    buttons.install_all(menu, FakePya)
    assert menu.nodes["@toolbar.klink_button_alpha"].icon == ""
    assert menu.nodes["@toolbar.klink_button_dd"].icon == ""


@pytest.mark.parametrize("icon,needle", [
    ("relative.png", "'icon' must be an absolute path"),
    (str(Path(__file__).resolve().with_name("icon.bmp")), "'icon' must be an absolute path to a .png or .svg"),
    (5, "'icon' must be an absolute path"),
])
def test_bad_icon_key_skips_manifest(tmp_path, icon, needle):
    _write(tmp_path, "alpha", _btn("alpha", icon=icon))
    report = buttons.install_all(FakeMenu(), FakePya)
    assert report["installed"] == [] and needle in report["skipped"][0]["reason"]

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Import from the plugin source tree (klink_plugin/ is the source of truth;
# klink/plugin_payload/ is its synced copy).
PLUGIN_PYTHON = Path(__file__).resolve().parents[2] / "klink_plugin" / "python"
if str(PLUGIN_PYTHON) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PYTHON))

# klink_server imports pya at package level (caught and stubbed out when
# absent), but the offline compat ships with `pip install klayout`. Gate on
# klayout.db (not "pya") like the other klink_server unit tests.
pytest.importorskip("klayout.db", reason="klayout pip package not installed")

from klink_server import companions  # noqa: E402


# --------------------------------------------------------------- helpers --
def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _valid_spec(**overrides) -> dict:
    spec = {
        "command": ["prog", "--port", "{port}"],
        "port": 8787,
        "control_file": "state/control.json",
    }
    spec.update(overrides)
    return spec


# ============================================================== load_descriptors
def test_load_descriptors_missing_dir_returns_empty(tmp_path):
    # tmp_path itself has no "companions" subdirectory
    assert companions.load_descriptors(tmp_path) == []


def test_load_descriptors_valid_file(tmp_path):
    spec = _valid_spec(name="vestigraph", label="HIST")
    path = tmp_path / "companions" / "vestigraph.json"
    _write_json(path, spec)

    found = companions.load_descriptors(tmp_path)

    assert len(found) == 1
    loaded = found[0]
    assert loaded["name"] == "vestigraph"
    assert loaded["label"] == "HIST"
    assert loaded["command"] == ["prog", "--port", "{port}"]
    assert loaded["_path"] == str(path)


def test_load_descriptors_invalid_json_skipped(tmp_path):
    path = tmp_path / "companions" / "broken.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")

    assert companions.load_descriptors(tmp_path) == []


def test_load_descriptors_missing_command_skipped(tmp_path):
    spec = _valid_spec()
    del spec["command"]
    _write_json(tmp_path / "companions" / "nocommand.json", spec)

    assert companions.load_descriptors(tmp_path) == []


def test_load_descriptors_missing_port_skipped(tmp_path):
    spec = _valid_spec()
    del spec["port"]
    _write_json(tmp_path / "companions" / "noport.json", spec)

    assert companions.load_descriptors(tmp_path) == []


def test_load_descriptors_missing_control_file_skipped(tmp_path):
    spec = _valid_spec()
    del spec["control_file"]
    _write_json(tmp_path / "companions" / "nocontrol.json", spec)

    assert companions.load_descriptors(tmp_path) == []


def test_load_descriptors_name_defaults_to_file_stem(tmp_path):
    spec = _valid_spec()  # no "name" key
    path = tmp_path / "companions" / "custom_name.json"
    _write_json(path, spec)

    found = companions.load_descriptors(tmp_path)

    assert len(found) == 1
    assert found[0]["name"] == "custom_name"
    assert found[0]["_path"] == str(path)


# ---- a few extra direct validate_descriptor() rule checks (bonus coverage) --
def test_validate_descriptor_not_a_dict():
    assert companions.validate_descriptor(["not", "a", "dict"]) is not None


def test_validate_descriptor_command_wrong_type():
    spec = _valid_spec(command="not-a-list")
    assert companions.validate_descriptor(spec) is not None


def test_validate_descriptor_port_out_of_range():
    spec = _valid_spec(port=99999)
    assert companions.validate_descriptor(spec) is not None


def test_validate_descriptor_control_file_empty_string():
    spec = _valid_spec(control_file="")
    assert companions.validate_descriptor(spec) is not None


def test_validate_descriptor_valid_spec_ok():
    assert companions.validate_descriptor(_valid_spec()) is None


# ==================================================================== child_env
def test_child_env_strips_python_vars_keeps_others_merges_extra(monkeypatch):
    monkeypatch.setenv("PYTHONHOME", str(Path("klayout") / "python"))
    monkeypatch.setenv("PYTHONPATH", str(Path("klayout") / "lib"))
    monkeypatch.setenv("PYTHONSTARTUP", str(Path("klayout") / "startup.py"))
    monkeypatch.setenv("SOME_OTHER_VAR", "keep-me")

    env = companions.child_env({"EXTRA": 123, "ANOTHER": True})

    assert "PYTHONHOME" not in env
    assert "PYTHONPATH" not in env
    assert "PYTHONSTARTUP" not in env
    assert env.get("SOME_OTHER_VAR") == "keep-me"
    assert env.get("EXTRA") == "123"
    assert env.get("ANOTHER") == "True"


# ========================================================================= ensure
def test_ensure_reuses_running_instance_from_control_file(tmp_path):
    control_path = tmp_path / "control.json"
    _write_json(control_path, {"port": 8790, "secret": "sek", "pid": 4242})
    spec = _valid_spec(name="svc", control_file=str(control_path))

    def _fail_spawn(spec, port):
        raise AssertionError("spawn_fn must not be called when a live control file exists")

    result = companions.ensure(
        spec,
        spawn_fn=_fail_spawn,
        alive=lambda port, health: port == 8790,
        port_is_free=lambda port: True,
    )

    assert result == {"name": "svc", "started": False, "pid": 4242, "port": 8790}


def test_ensure_spawns_on_preferred_port_when_free(tmp_path):
    control_path = tmp_path / "control.json"  # never written -> no control
    spec = _valid_spec(name="svc", port=8787, control_file=str(control_path))
    spawn_calls = []

    def _spawn(spec, port):
        spawn_calls.append(port)
        return 555

    result = companions.ensure(
        spec,
        spawn_fn=_spawn,
        alive=lambda port, health: True,
        port_is_free=lambda port: True,
    )

    assert spawn_calls == [8787]
    assert result == {"name": "svc", "started": True, "pid": 555, "port": 8787}


def test_ensure_takes_next_free_port_when_preferred_is_taken(tmp_path):
    control_path = tmp_path / "control.json"
    spec = _valid_spec(name="svc", port=8787, control_file=str(control_path))
    spawn_calls = []

    def _spawn(spec, port):
        spawn_calls.append(port)
        return 1

    result = companions.ensure(
        spec,
        spawn_fn=_spawn,
        alive=lambda port, health: True,
        port_is_free=lambda port: port != 8787,
    )

    assert spawn_calls == [8788]
    assert result["port"] == 8788


def test_ensure_raises_when_no_free_port_within_max_tries(tmp_path):
    control_path = tmp_path / "control.json"
    spec = _valid_spec(name="svc", port=8787, control_file=str(control_path))

    with pytest.raises(RuntimeError) as excinfo:
        companions.ensure(
            spec,
            spawn_fn=lambda spec, port: 1,
            alive=lambda port, health: True,
            port_is_free=lambda port: False,
            max_port_tries=3,
        )

    message = str(excinfo.value)
    assert "8787" in message
    assert "8789" in message  # preferred + max_port_tries - 1


def test_ensure_raises_when_spawn_fails_with_oserror(tmp_path):
    control_path = tmp_path / "control.json"
    spec = _valid_spec(name="svc", port=8787, control_file=str(control_path))

    def _spawn(spec, port):
        raise OSError("no such file or directory")

    with pytest.raises(RuntimeError) as excinfo:
        companions.ensure(
            spec,
            spawn_fn=_spawn,
            alive=lambda port, health: True,
            port_is_free=lambda port: True,
        )

    assert "prog" in str(excinfo.value)


def test_ensure_raises_when_never_alive_within_wait_s(tmp_path, monkeypatch):
    monkeypatch.setattr(companions.time, "sleep", lambda seconds: None)
    control_path = tmp_path / "control.json"
    spec = _valid_spec(name="svc", port=8787, control_file=str(control_path))

    with pytest.raises(RuntimeError) as excinfo:
        companions.ensure(
            spec,
            spawn_fn=lambda spec, port: 1,
            alive=lambda port, health: False,
            port_is_free=lambda port: True,
            wait_s=0.05,
        )

    assert "8787" in str(excinfo.value)


# ========================================================================== spawn
def test_spawn_substitutes_port_and_detaches(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONHOME", str(Path("klayout") / "python"))
    log_file = tmp_path / "logs" / "service.log"
    control_file = tmp_path / "sub" / "control.json"  # dir does not exist yet
    spec = {
        "command": ["prog", "--port", "{port}", "--tag", "x{port}"],
        "control_file": str(control_file),
        "log_file": str(log_file),
        "env": {"FOO": "bar"},
    }

    calls = []

    class FakeProcess:
        def __init__(self, argv, **kwargs):
            calls.append((argv, kwargs))
            self.pid = 4242

    monkeypatch.setattr(companions.subprocess, "Popen", FakeProcess)

    pid = companions.spawn(spec, 9001)

    assert pid == 4242
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == ["prog", "--port", "9001", "--tag", "x9001"]
    assert "PYTHONHOME" not in kwargs["env"]
    assert kwargs["env"]["FOO"] == "bar"
    assert kwargs["cwd"] == str(control_file.parent)
    assert log_file.exists()


def test_spawn_defaults_cwd_when_no_explicit_cwd_and_no_log_file(tmp_path, monkeypatch):
    control_file = tmp_path / "workdir" / "control.json"
    spec = {
        "command": ["prog"],
        "control_file": str(control_file),
    }
    calls = []

    class FakeProcess:
        def __init__(self, argv, **kwargs):
            calls.append((argv, kwargs))
            self.pid = 7

    monkeypatch.setattr(companions.subprocess, "Popen", FakeProcess)

    companions.spawn(spec, 1234)

    argv, kwargs = calls[0]
    assert kwargs["cwd"] == str(control_file.parent)
    assert kwargs["stdout"] is companions.subprocess.DEVNULL


# ===================================================================== request_link
class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_request_link_nested_data_form(monkeypatch):
    captured = {}

    def _fake_urlopen(request, timeout=None):
        captured["request"] = request
        captured["timeout"] = timeout
        return _FakeResponse({"data": {"link": "http://127.0.0.1:1/x"}})

    monkeypatch.setattr(companions.urllib.request, "urlopen", _fake_urlopen)

    spec = {"name": "svc", "link_path": "/api/v1/auth/issue-link"}
    control = {"port": 8790, "secret": "sekret"}

    link = companions.request_link(spec, control)

    assert link == "http://127.0.0.1:1/x"
    request = captured["request"]
    assert request.get_method() == "POST"
    assert request.full_url == "http://127.0.0.1:8790/api/v1/auth/issue-link"
    assert request.headers.get("X-control-secret") == "sekret"


def test_request_link_top_level_form(monkeypatch):
    def _fake_urlopen(request, timeout=None):
        return _FakeResponse({"link": "http://127.0.0.1:2/y"})

    monkeypatch.setattr(companions.urllib.request, "urlopen", _fake_urlopen)

    link = companions.request_link({"name": "svc"}, {"port": 8790, "secret": "s"})

    assert link == "http://127.0.0.1:2/y"


def test_request_link_missing_link_raises(monkeypatch):
    def _fake_urlopen(request, timeout=None):
        return _FakeResponse({"data": {}})

    monkeypatch.setattr(companions.urllib.request, "urlopen", _fake_urlopen)

    with pytest.raises(RuntimeError):
        companions.request_link({"name": "svc"}, {"port": 8790, "secret": "s"})


# ======================================================================== open_panel
def test_open_panel_polls_control_file_then_opens_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(companions.time, "sleep", lambda seconds: None)
    control_file = tmp_path / "control.json"
    spec = {"name": "svc", "control_file": str(control_file)}

    calls = {"n": 0}

    def _fake_read_control(spec):
        calls["n"] += 1
        if calls["n"] < 3:
            return {"port": 8787, "secret": "stale"}
        return {"port": 8790, "secret": "fresh"}

    monkeypatch.setattr(companions, "read_control", _fake_read_control)
    monkeypatch.setattr(companions, "request_link", lambda spec, control: "http://link/ok")

    opened = []

    def _ensure_fn(spec):
        return {"name": "svc", "started": True, "pid": 1, "port": 8790}

    link = companions.open_panel(spec, browser=opened.append, ensure_fn=_ensure_fn)

    assert link == "http://link/ok"
    assert opened == ["http://link/ok"]
    assert calls["n"] == 3


def test_open_panel_raises_when_control_never_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(companions.time, "sleep", lambda seconds: None)
    control_file = tmp_path / "control.json"
    spec = {"name": "svc", "control_file": str(control_file)}

    monkeypatch.setattr(companions, "read_control", lambda spec: {"port": 8787, "secret": "never"})

    def _ensure_fn(spec):
        return {"name": "svc", "started": True, "pid": 1, "port": 8790}

    with pytest.raises(RuntimeError) as excinfo:
        companions.open_panel(spec, browser=lambda link: None, ensure_fn=_ensure_fn)

    assert "8790" in str(excinfo.value)


# ==================================================================== install_buttons
class _FakeAction:
    def __init__(self):
        self.title = None
        self.icon_text = None
        self.tool_tip = None
        self.on_triggered = None


class _FakePya:
    Action = _FakeAction


class _FakeMenu:
    def __init__(self):
        self.deleted = []
        self.inserted = []

    def delete_item(self, path):
        self.deleted.append(path)

    def insert_item(self, path, button_id, action):
        self.inserted.append((path, button_id, action))


def test_install_buttons_creates_one_action_per_descriptor(tmp_path):
    companions._actions.clear()
    _write_json(tmp_path / "companions" / "alpha.json", _valid_spec(name="alpha", label="A1"))
    _write_json(tmp_path / "companions" / "beta.json", _valid_spec(name="beta"))  # no label

    menu = _FakeMenu()
    ids = companions.install_buttons(menu, _FakePya, root=tmp_path)

    assert ids == ["klink_companion_alpha", "klink_companion_beta"]

    # delete-before-insert for both buttons, in order
    assert menu.deleted == [
        "@toolbar.end.klink_companion_alpha",
        "@toolbar.end.klink_companion_beta",
    ]
    assert [entry[1] for entry in menu.inserted] == ids

    alpha_action = menu.inserted[0][2]
    beta_action = menu.inserted[1][2]
    assert alpha_action.icon_text == "A1"
    assert beta_action.icon_text == "BETA"  # "beta"[:5].upper()

    # kept alive so pya does not drop them
    assert companions._actions["klink_companion_alpha"] is alpha_action
    assert companions._actions["klink_companion_beta"] is beta_action


# ====================================================================== autostart_all
def test_autostart_all_skips_false_starts_true(tmp_path, monkeypatch):
    _write_json(tmp_path / "companions" / "svca.json", _valid_spec(name="svcA"))  # autostart default True
    _write_json(tmp_path / "companions" / "svcb.json", _valid_spec(name="svcB", autostart=False))

    calls = []
    monkeypatch.setattr(companions, "start_if_needed",
                        lambda spec: calls.append(spec["name"]) or {"name": spec["name"], "port": 1, "ready": True})

    started = companions.autostart_all(root=tmp_path)

    assert [s["name"] for s in started] == ["svcA"]
    assert calls == ["svcA"]


# ============================================================ GUI-thread state machine
class _FakeTimer:
    instances = []

    def __init__(self):
        self.timeout = None
        self.interval = None
        self.stopped = False
        _FakeTimer.instances.append(self)

    def start(self, ms):
        self.interval = ms

    def stop(self):
        self.stopped = True


class _FakeQtPya(_FakePya):
    QTimer = _FakeTimer
    opened = []

    class QUrl(str):
        pass

    class QDesktopServices:
        @staticmethod
        def openUrl(url):
            _FakeQtPya.opened.append(str(url))
            return True


def _gui_reset(monkeypatch):
    monkeypatch.setattr(companions, "_pya", _FakeQtPya)
    monkeypatch.setattr(companions, "_timer", None)
    companions._pending.clear()
    _FakeTimer.instances.clear()
    _FakeQtPya.opened.clear()


def test_click_while_service_starting_opens_when_ready_without_threads(tmp_path, monkeypatch):
    """Real-user failure: HIST clicked right after KLayout started. No worker threads: the
    click spawns (or joins the autostart), a QTimer polls, and the panel opens when ready."""
    _gui_reset(monkeypatch)
    spec = _valid_spec(name="svc", control_file=str(tmp_path / "control.json"))
    monkeypatch.setattr(companions, "spawn", lambda s, p: 4242)
    monkeypatch.setattr(companions, "port_free", lambda p: True)
    up = {"alive": False}
    monkeypatch.setattr(companions, "service_alive", lambda port, path="/healthz", timeout=1.5: up["alive"])
    monkeypatch.setattr(companions, "request_link", lambda s, c: f"http://127.0.0.1:{c['port']}/#tok")

    companions._on_open(spec)                       # click 1: spawns, nothing to open yet
    companions._on_open(spec)                       # click 2: joins the pending entry
    assert companions._pending["svc"]["open"] is True
    assert len(_FakeTimer.instances) == 1 and _FakeTimer.instances[0].interval == companions.POLL_MS
    companions._poll()                               # not ready yet
    assert _FakeQtPya.opened == []

    _write_json(tmp_path / "control.json", {"port": 8787, "secret": "s"})
    up["alive"] = True
    companions._poll()                               # ready: one open for both clicks
    assert _FakeQtPya.opened == ["http://127.0.0.1:8787/#tok"]
    assert companions._pending == {} and _FakeTimer.instances[0].stopped
    assert companions._timer is None


def test_click_when_service_already_up_opens_immediately(tmp_path, monkeypatch):
    _gui_reset(monkeypatch)
    spec = _valid_spec(name="svc", control_file=str(tmp_path / "control.json"))
    _write_json(tmp_path / "control.json", {"port": 8790, "secret": "s"})
    monkeypatch.setattr(companions, "service_alive", lambda port, path="/healthz", timeout=1.5: port == 8790)

    def no_spawn(s, p):
        raise AssertionError("must not spawn")
    monkeypatch.setattr(companions, "spawn", no_spawn)
    monkeypatch.setattr(companions, "request_link", lambda s, c: "http://127.0.0.1:8790/#tok")

    companions._on_open(spec)

    assert _FakeQtPya.opened == ["http://127.0.0.1:8790/#tok"]
    assert companions._pending == {} and _FakeTimer.instances == []


def test_poll_gives_up_when_now_equals_deadline(tmp_path, monkeypatch):
    _gui_reset(monkeypatch)
    spec = _valid_spec(name="svc", control_file=str(tmp_path / "control.json"))
    monkeypatch.setattr(companions, "service_alive", lambda port, path="/healthz", timeout=1.5: False)
    monkeypatch.setattr(companions.time, "monotonic", lambda: 100.0)

    companions._watch(spec, 8787, open_when_ready=True, wait_s=0)
    companions._poll()

    assert companions._pending == {} and _FakeQtPya.opened == []


def test_poll_keeps_pending_before_deadline(tmp_path, monkeypatch):
    _gui_reset(monkeypatch)
    spec = _valid_spec(name="svc", control_file=str(tmp_path / "control.json"))
    monkeypatch.setattr(companions, "service_alive", lambda port, path="/healthz", timeout=1.5: False)
    now = {"value": 100.0}
    monkeypatch.setattr(companions.time, "monotonic", lambda: now["value"])

    companions._watch(spec, 8787, open_when_ready=True, wait_s=10)
    now["value"] = 109.999
    companions._poll()

    assert "svc" in companions._pending and _FakeQtPya.opened == []

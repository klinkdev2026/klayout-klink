"""Companion services: external local programs that KLayout starts and links to.

A companion is any loopback web service that wants to ride along with KLayout
(a history recorder, a dashboard, ...). It stays a normal external program in
its own interpreter; klink only owns the two things that must happen INSIDE
KLayout: starting it when KLayout starts and giving it a toolbar button.

Registration is one JSON descriptor per companion under
``<registry_root>/companions/<name>.json`` (see ``default_registry_root``)::

    {
      "name": "vestigraph",
      "label": "HIST",                       # toolbar icon text
      "title": "Vestigraph layout history",  # tooltip
      "command": ["<python-executable>", "-m", "vestigraph", "serve",
                  "--port", "{port}", "--control-file", "--exit-when-idle", "60"],
      "port": 8787,                          # preferred; the next free one is used if taken
      "control_file": "<state-directory>/control.json",
      "health_path": "/healthz",
      "link_path": "/api/v1/auth/issue-link",
      "autostart": true,
      "cwd": "<state-directory>",
      "log_file": "<state-directory>/logs/service.log",
      "env": {}
    }

Control-link protocol (what the companion must implement):

* ``GET <health_path>`` answers 200 while the service is up.
* On startup the service writes ``control_file`` (owner-only) as JSON with at
  least ``{"port": <int>, "secret": "<per-launch secret>"}``.
* ``POST <link_path>`` with header ``X-Control-Secret: <secret>`` answers JSON
  whose ``data.link`` (or top-level ``link``) is a one-time sign-in URL, which
  klink opens in the user's browser.

Ownership rule: a service answering on the preferred port WITHOUT a matching
control file is somebody else's (a manual start, another user) and is never
reused nor disturbed; klink takes the next free port for its own instance.

Stdlib only: this module runs inside KLayout's embedded Python.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .log import get_logger
from .session_registry import default_registry_root

_log = get_logger("companions")

HEALTH_TIMEOUT_S = 25.0
MAX_PORT_TRIES = 20


# ------------------------------------------------------------ descriptors --
def companions_dir(root: str | Path | None = None) -> Path:
    return (Path(root) if root is not None else default_registry_root()) / "companions"


def load_descriptors(root: str | Path | None = None) -> list[dict]:
    """All well-formed descriptors, sorted by file name; broken files are logged and skipped."""
    folder = companions_dir(root)
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.glob("*.json")):
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
            problem = validate_descriptor(spec)
        except (OSError, ValueError) as exc:
            problem = f"unreadable: {exc}"
            spec = None
        if problem:
            _log.warning("companion descriptor %s skipped: %s", path.name, problem)
            continue
        spec.setdefault("name", path.stem)
        spec["_path"] = str(path)
        found.append(spec)
    return found


def validate_descriptor(spec) -> str | None:
    """None when usable, else a one-line reason."""
    if not isinstance(spec, dict):
        return "not a JSON object"
    command = spec.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
        return "'command' must be a non-empty list of strings"
    if not isinstance(spec.get("port"), int) or not (1 <= spec["port"] <= 65535):
        return "'port' must be an integer between 1 and 65535"
    if not isinstance(spec.get("control_file"), str) or not spec["control_file"]:
        return "'control_file' must be a path"
    return None


# ------------------------------------------------------------ environment --
def child_env(extra: dict | None = None) -> dict:
    """KLayout's embedded Python exports PYTHONHOME/PYTHONPATH for ITS interpreter; any other
    Python started from inside KLayout would break on them. Strip them for the child."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONEXECUTABLE", "PYTHONNOUSERSITE"):
        env.pop(key, None)
    for key, value in (extra or {}).items():
        env[str(key)] = str(value)
    return env


def _detached_kwargs() -> dict:
    """Windows: CREATE_NO_WINDOW gives the child a hidden console that its own children (the
    py launcher starts a python.exe) inherit. DETACHED_PROCESS is deliberately NOT used: a
    console child of a console-less parent allocates a brand-new VISIBLE console window
    (real-user report: a cmd window popped up on every start)."""
    if sys.platform == "win32":
        return {"creationflags": (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                                  | getattr(subprocess, "CREATE_NO_WINDOW", 0))}
    return {"start_new_session": True}


# ---------------------------------------------------------------- probing --
def service_alive(port: int, health_path: str = "/healthz", timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}{health_path}", timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", int(port)))
            return True
        except OSError:
            return False


def read_control(spec: dict) -> dict | None:
    try:
        data = json.loads(Path(spec["control_file"]).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("secret") and data.get("port") else None
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------- spawning --
def spawn(spec: dict, port: int) -> int:
    """Start the companion detached from KLayout with ``{port}`` substituted; returns the pid."""
    argv = [part.replace("{port}", str(port)) for part in spec["command"]]
    log_path = spec.get("log_file")
    cwd = spec.get("cwd") or str(Path(spec["control_file"]).parent)
    Path(cwd).mkdir(parents=True, exist_ok=True)
    sink = subprocess.DEVNULL
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        sink = open(log_path, "ab")
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=sink, stderr=sink,
                                   close_fds=True, env=child_env(spec.get("env")), cwd=cwd,
                                   **_detached_kwargs())
    finally:
        if sink is not subprocess.DEVNULL:
            sink.close()
    return process.pid


def start_if_needed(spec: dict, *, spawn_fn=None, alive=None, port_is_free=None,
                    max_port_tries: int = MAX_PORT_TRIES) -> dict:
    """Non-blocking: reuse OUR running instance (control file + health) or spawn a new one and
    return at once. ``{"name", "started": bool, "pid", "port", "ready": bool}``.
    Collaborators default to the module functions at CALL time (monkeypatch-friendly)."""
    spawn_fn = spawn_fn or spawn
    alive = alive or service_alive
    port_is_free = port_is_free or port_free
    name = spec.get("name", "?")
    health = spec.get("health_path", "/healthz")
    control = read_control(spec)
    if control and alive(int(control["port"]), health):
        _log.info("companion %s already running on 127.0.0.1:%s; reusing", name, control["port"])
        return {"name": name, "started": False, "pid": control.get("pid"), "port": int(control["port"]), "ready": True}
    chosen = None
    preferred = int(spec["port"])
    for candidate in range(preferred, preferred + max_port_tries):
        if port_is_free(candidate):
            chosen = candidate
            break
        _log.info("companion %s: port %s is taken by another program; trying the next", name, candidate)
    if chosen is None:
        raise RuntimeError(f"{name}: no free loopback port between {preferred} and {preferred + max_port_tries - 1}")
    try:
        pid = spawn_fn(spec, chosen)
    except OSError as exc:
        raise RuntimeError(f"{name}: cannot start {spec['command'][0]!r}: {exc}") from exc
    _log.info("companion %s spawned pid %s on port %s", name, pid, chosen)
    return {"name": name, "started": True, "pid": pid, "port": chosen, "ready": False}


def is_ready(spec: dict, port: int, *, alive=None) -> bool:
    """Ready = the control file names ``port`` AND health answers there."""
    alive = alive or service_alive
    control = read_control(spec)
    return bool(control and int(control["port"]) == int(port)
                and alive(int(port), spec.get("health_path", "/healthz")))


def _not_ready_error(spec: dict, port: int, wait_s: float) -> RuntimeError:
    return RuntimeError(f"{spec.get('name')}: did not answer on 127.0.0.1:{port} within {wait_s:g} s"
                        + (f"; see {spec['log_file']}" if spec.get("log_file") else ""))


def ensure(spec: dict, *, spawn_fn=None, alive=None, port_is_free=None,
           wait_s: float = HEALTH_TIMEOUT_S, max_port_tries: int = MAX_PORT_TRIES) -> dict:
    """Blocking form of :func:`start_if_needed` (CLI/test use; inside KLayout the GUI timer polls
    instead): wait until the service answers. Raises RuntimeError with a user-readable message."""
    alive = alive or service_alive
    outcome = start_if_needed(spec, spawn_fn=spawn_fn, alive=alive, port_is_free=port_is_free,
                              max_port_tries=max_port_tries)
    if outcome.pop("ready"):
        return outcome
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if alive(outcome["port"], spec.get("health_path", "/healthz")):
            return outcome
        time.sleep(0.25)
    raise _not_ready_error(spec, outcome["port"], wait_s)


# ------------------------------------------------------------------ links --
def request_link(spec: dict, control: dict, timeout: float = 5.0) -> str:
    port = int(control["port"])
    request = urllib.request.Request(f"http://127.0.0.1:{port}{spec.get('link_path', '/api/v1/auth/issue-link')}",
                                     method="POST", headers={"X-Control-Secret": str(control["secret"])})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    link = (payload.get("data") or {}).get("link") if isinstance(payload.get("data"), dict) else None
    link = link or payload.get("link")
    if not isinstance(link, str) or not link:
        raise RuntimeError(f"{spec.get('name')}: link endpoint returned no link")
    return link


def open_panel(spec: dict, *, browser=None, ensure_fn=ensure) -> str:
    """Ensure the companion, trade the control secret for a sign-in link, open the browser."""
    outcome = ensure_fn(spec)
    control = None
    for _ in range(40):  # the control file is written right after the service binds
        control = read_control(spec)
        if control and int(control["port"]) == int(outcome["port"]):
            break
        control = None
        time.sleep(0.25)
    if not control:
        raise RuntimeError(f"{spec.get('name')}: running on port {outcome['port']} but its control file "
                           f"{spec['control_file']} does not match; restart KLayout or the service")
    link = request_link(spec, control)
    (browser or _open_browser)(link)
    return link


def _open_browser(link: str) -> None:
    """Outside KLayout (or when no GUI pump is installed): the platform default browser."""
    if sys.platform == "win32":
        os.startfile(link)  # ShellExecute; webbrowser would fall back to iexplore on failure
        return
    import webbrowser
    webbrowser.open(link)


# -------------------------------------------------------------- KLayout UI --
# No Python threads inside KLayout: a worker thread there is starved of the GIL while the Qt
# loop runs (measured 4-8 s for a 0.5 s start). Everything below runs on the GUI thread and
# never blocks: spawn returns immediately, readiness is polled by a QTimer.
_actions: dict = {}
_pending: dict = {}      # name -> {"spec", "port", "deadline", "open": bool}
_timer = None            # pya.QTimer while something is pending
_pya = None
POLL_MS = 250


def _open_link(spec: dict, link: str) -> None:
    try:
        if _pya is not None and _pya.QDesktopServices.openUrl(_pya.QUrl(link)):
            return
    except Exception as exc:
        _log.warning("Qt could not open the browser (%s); falling back to the OS opener", exc)
    _open_browser(link)


def _open_now(spec: dict, port: int) -> None:
    control = read_control(spec)
    if not control or int(control["port"]) != int(port):
        raise RuntimeError(f"{spec.get('name')}: control file does not name port {port}")
    _open_link(spec, request_link(spec, control))


def _watch(spec: dict, port: int, *, open_when_ready: bool, wait_s: float = HEALTH_TIMEOUT_S) -> None:
    entry = _pending.get(spec["name"])
    if entry and entry["port"] == port:
        entry["open"] = entry["open"] or open_when_ready
        return
    _pending[spec["name"]] = {"spec": spec, "port": port, "deadline": time.monotonic() + wait_s,
                              "open": open_when_ready}
    _ensure_timer()


def _ensure_timer() -> None:
    global _timer
    if _timer is not None or _pya is None:
        return
    try:
        timer = _pya.QTimer()
        timer.timeout = _poll
        timer.start(POLL_MS)
        _timer = timer
    except Exception as exc:
        _log.error("no Qt timer (%s); companions cannot be watched for readiness", exc)


def _poll() -> None:
    """QTimer tick on the GUI thread: promote pending companions, open queued panels."""
    global _timer
    for name in list(_pending):
        entry = _pending[name]
        spec, port = entry["spec"], entry["port"]
        if is_ready(spec, port):
            del _pending[name]
            _log.info("companion %s ready on port %s", name, port)
            if entry["open"]:
                try:
                    _open_now(spec, port)
                except Exception as exc:
                    _log.error("companion %s: cannot open panel: %s", name, exc)
        elif time.monotonic() >= entry["deadline"]:
            del _pending[name]
            _log.error("%s", _not_ready_error(spec, port, HEALTH_TIMEOUT_S))
    if not _pending and _timer is not None:
        try:
            _timer.stop()
        except Exception:
            pass
        _timer = None


def autostart_all(root: str | Path | None = None) -> list[dict]:
    """Spawn every descriptor with ``autostart`` true (default); returns at once."""
    specs = [s for s in load_descriptors(root) if s.get("autostart", True)]
    for spec in specs:
        try:
            outcome = start_if_needed(spec)
            if not outcome["ready"]:
                _watch(spec, outcome["port"], open_when_ready=False)
        except Exception as exc:
            _log.error("companion %s autostart failed: %s", spec.get("name"), exc)
    return specs


def _on_open(spec: dict) -> None:
    """Button click: open right away when the service is up, else spawn and open when ready."""
    try:
        outcome = start_if_needed(spec)
        if outcome["ready"]:
            _open_now(spec, outcome["port"])
        else:
            _watch(spec, outcome["port"], open_when_ready=True)
    except Exception as exc:
        _log.error("companion %s: cannot open panel: %s", spec.get("name"), exc)


def install_buttons(menu, pya, root: str | Path | None = None) -> list[str]:
    """One toolbar button per descriptor; re-runs replace previous buttons. Returns button ids."""
    global _pya
    _pya = pya
    ids = []
    for spec in load_descriptors(root):
        button_id = f"klink_companion_{spec['name']}"
        try:
            menu.delete_item(f"@toolbar.end.{button_id}")
        except Exception:
            pass
        action = pya.Action()
        action.title = str(spec.get("title") or spec["name"])
        action.icon_text = str(spec.get("label") or spec["name"][:5].upper())
        action.tool_tip = str(spec.get("title") or f"Open {spec['name']}")
        action.on_triggered = lambda spec=spec: _on_open(spec)
        menu.insert_item("@toolbar.end", button_id, action)
        _actions[button_id] = action  # keep alive: pya drops unreferenced Actions
        ids.append(button_id)
        _log.info("companion button %s installed (%s)", button_id, action.icon_text)
    return ids

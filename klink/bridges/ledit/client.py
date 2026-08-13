"""File-exchange RPC client for the L-Edit bridge macro (schema 1).

Transport: JSON files under ``<root>/<namespace>/{inbox,outbox}``; the
macro polls the inbox from L-Edit's UI thread and answers atomically
(``.tmp`` + rename). Liveness comes from ``hello.json`` (heartbeat
refreshed roughly every 2 s by the macro).

Errors are instructions: every failure raises :class:`LEditBridgeError`
whose ``next_action`` names the exact next step.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, Optional

_HELLO_MAX_AGE_S = 10.0


class LEditBridgeError(RuntimeError):
    """Bridge failure with an instructive next step."""

    def __init__(self, message: str, next_action: str = "",
                 code: str = "ERR_BRIDGE") -> None:
        super().__init__(message)
        self.next_action = next_action
        self.code = code

    def __str__(self) -> str:  # instructive by default
        base = super().__str__()
        return f"{base}\nnext_action: {self.next_action}" if \
            self.next_action else base


def default_root() -> str:
    env = os.environ.get("KLINK_LEDIT_BRIDGE_ROOT")
    if env:
        return env
    base = os.environ.get("LOCALAPPDATA", r"C:\klink_bridge")
    return os.path.join(base, "klink", "ledit_bridge")


class LEditBridgeClient:
    """Talk to one bridge namespace (one L-Edit instance)."""

    def __init__(self, namespace: str = "default",
                 root: Optional[str] = None,
                 poll_s: float = 0.1) -> None:
        self.namespace = namespace
        self.root = os.path.join(root or default_root(), namespace)
        self.inbox = os.path.join(self.root, "inbox")
        self.outbox = os.path.join(self.root, "outbox")
        self.hello_path = os.path.join(self.root, "hello.json")
        self.poll_s = poll_s

    # -- liveness ----------------------------------------------------------

    def hello(self) -> Dict[str, Any]:
        if not os.path.exists(self.hello_path):
            raise LEditBridgeError(
                f"no hello.json under {self.root}",
                "start L-Edit and load ledit_bridge.cpp "
                "(Tools > Macro > Load Macro...)")
        with open(self.hello_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def hello_age_s(self) -> float:
        return time.time() - os.path.getmtime(self.hello_path)

    def alive(self, max_age_s: float = _HELLO_MAX_AGE_S) -> bool:
        try:
            self.hello()
        except LEditBridgeError:
            return False
        return self.hello_age_s() <= max_age_s

    def require_alive(self, max_age_s: float = _HELLO_MAX_AGE_S) -> None:
        hello = self.hello()  # raises with instructions when missing
        age = self.hello_age_s()
        if age > max_age_s:
            raise LEditBridgeError(
                f"bridge heartbeat is stale ({age:.0f}s old; macro "
                f"version {hello.get('macro_version', '?')})",
                "in L-Edit run Tools > klink: Bridge Start (or reload the "
                "macro); a modal dialog in L-Edit also pauses the timer")

    # -- RPC ---------------------------------------------------------------

    def call(self, cmd: str, params: Optional[Dict[str, Any]] = None,
             timeout: float = 15.0) -> Dict[str, Any]:
        self.require_alive()
        rid = "req_" + uuid.uuid4().hex[:12]
        req = {"schema": 1, "id": rid, "cmd": cmd, "params": params or {}}
        tmp = os.path.join(self.inbox, rid + ".json.tmp")
        final = os.path.join(self.inbox, "req_" + rid + ".json")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(req, f)
        os.replace(tmp, final)

        resp_path = os.path.join(self.outbox, "resp_" + rid + ".json")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(resp_path):
                time.sleep(min(self.poll_s / 2, 0.05))  # let rename settle
                with open(resp_path, "r", encoding="utf-8") as f:
                    resp = json.load(f)
                try:
                    os.remove(resp_path)
                except OSError:
                    pass
                if not resp.get("ok"):
                    err = resp.get("error", {})
                    raise LEditBridgeError(
                        f"{cmd} failed: {err.get('message', 'unknown')}",
                        err.get("next_action", ""),
                        err.get("code", "ERR_BRIDGE"))
                return resp.get("result", {})
            time.sleep(self.poll_s)
        raise LEditBridgeError(
            f"timeout after {timeout:.0f}s waiting for response to {cmd} "
            f"({rid})",
            "check hello.json freshness, close any modal dialog in L-Edit, "
            f"and read {os.path.join(self.root, 'bridge.log')}")

    # -- convenience wrappers (thin; command set = macro capabilities) -----

    def ping(self) -> Dict[str, Any]:
        return self.call("ping")

    def get_layers(self):
        return self.call("get_layers")["layers"]

    def get_selection(self) -> Dict[str, Any]:
        return self.call("get_selection")

    def get_cell(self, cell: str) -> Dict[str, Any]:
        return self.call("get_cell", {"cell": cell})

    def list_cells(self):
        return self.call("list_cells")["cells"]

    def ensure_layer(self, name: str, gds_layer: int = -1,
                     gds_datatype: int = -1) -> Dict[str, Any]:
        return self.call("ensure_layer", {
            "name": name, "gds_layer": gds_layer,
            "gds_datatype": gds_datatype})

    def create_cell(self, name: str) -> Dict[str, Any]:
        return self.call("create_cell", {"name": name})

    def clear_cell(self, cell: str) -> Dict[str, Any]:
        return self.call("clear_cell", {"cell": cell})

    def draw(self, items, cell: Optional[str] = None) -> Dict[str, Any]:
        p: Dict[str, Any] = {"items": items}
        if cell:
            p["cell"] = cell
        return self.call("draw", p)

    def place_instance(self, child: str, **kw) -> Dict[str, Any]:
        return self.call("place_instance", dict(kw, child=child))

    def set_layer_style(self, layer: str, **kw) -> Dict[str, Any]:
        return self.call("set_layer_style", dict(kw, layer=layer))

    def instance_tcell(self, tcell: str, params: Dict[str, Any],
                       cell: Optional[str] = None,
                       x_um: Optional[float] = None,
                       y_um: Optional[float] = None) -> Dict[str, Any]:
        p: Dict[str, Any] = {"tcell": tcell, "params": params}
        if cell:
            p["cell"] = cell
        if x_um is not None:
            p["x_um"] = x_um
        if y_um is not None:
            p["y_um"] = y_um
        return self.call("instance_tcell", p)

    def set_tcell_code(self, cell: str, code: str, language: int = 5,
                       params=None) -> Dict[str, Any]:
        p: Dict[str, Any] = {"cell": cell, "code": code,
                             "language": language}
        if params:
            p["params"] = params
        return self.call("set_tcell_code", p)

    def get_tcell_params(self, cell: str, names) -> Dict[str, Any]:
        return self.call("get_tcell_params",
                         {"cell": cell, "names": list(names)})["params"]

    def get_drc_rules(self):
        return self.call("get_drc_rules")["rules"]

    def _verify_switched(self, out: Dict[str, Any], before: str,
                         what: str) -> Dict[str, Any]:
        # Defense in depth for macros < 0.5.1: they could create/open a
        # design yet leave the previously active one (often the user's)
        # receiving every subsequent write. Macro >= 0.5.1 fails server-side.
        after = self.ping().get("file", "")
        if not after or after == before:
            raise LEditBridgeError(
                f"{what} returned ok but the active design is still "
                f"'{before or 'none'}'",
                "update/reload the bridge macro (>=0.5.1) or switch to the "
                "new design in L-Edit (Window menu); ping must report the "
                "new 'file' before any drawing")
        out.setdefault("file", after)
        return out

    def new_design(self, name: str = "klink_design",
                   setup_from_visible: bool = False) -> Dict[str, Any]:
        before = self.ping().get("file", "")
        out = self.call("new_design", {
            "name": name, "setup_from_visible": setup_from_visible})
        return self._verify_switched(out, before, "new_design")

    def open_design(self, path: str) -> Dict[str, Any]:
        before = self.ping().get("file", "")
        out = self.call("open_design", {"path": path})
        if out.get("file", "") and out["file"] == before:
            return out          # re-opening the already-active design is fine
        return self._verify_switched(out, before, "open_design")

    def save_design(self, path: str = "") -> Dict[str, Any]:
        p: Dict[str, Any] = {}
        if path:
            p["path"] = path
        return self.call("save_design", p)

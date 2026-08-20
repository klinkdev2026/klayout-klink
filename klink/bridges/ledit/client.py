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
import locale
import os
import time
import uuid
from typing import Any, Dict, Optional

_HELLO_MAX_AGE_S = 10.0

# The macro reads a request only if it is at or under this size; anything
# larger is silently ignored AND left in the inbox (ledit_bridge.cpp:414).
# Measured: 250 flattened polygons = 104 KiB, i.e. real layout payloads blow
# past it easily while boxes of the same count do not.
_REQ_CAP_BYTES = 64 * 1024
# Chunking budget for self-splitting commands: leaves room for the request
# envelope and for params (cell name, expect_file) on top of the items.
_REQ_CHUNK_BYTES = 50 * 1024


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


def _read_json(path: str) -> Dict[str, Any]:
    """Read one bridge JSON file, tolerating the macro's ANSI output.

    The macro builds its JSON from L-Edit's ANSI (system-codepage) strings
    and writes the bytes through, so a design or cell whose PATH contains
    non-ASCII characters -- a Chinese OneDrive folder, say -- produces a
    file that is not valid UTF-8. Decoding it strictly raises
    UnicodeDecodeError from inside json.load, which reads as a klink crash
    rather than an encoding mismatch, and takes out every command that
    happens to mention that name (hello, get_cell, list_designs...).

    Try UTF-8 first (correct, and what a macro >= 0.5.4 emits), then the
    system codepage, which recovers the real characters rather than
    mangling them. Only if both fail do we fall back to lossy UTF-8, so a
    caller still gets a usable answer instead of an exception.
    """
    with open(path, "rb") as f:
        raw = f.read()
    for encoding in ("utf-8", locale.getpreferredencoding(False)):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, LookupError):
            continue
        except ValueError:
            break               # decoded fine, but the JSON itself is bad
    return json.loads(raw.decode("utf-8", errors="replace"))


def bundled_macro_path() -> str:
    """Absolute path of the bridge macro shipped inside the package.

    The user has to pick this file in L-Edit's Load Macro dialog by hand, so
    every message that says "load the macro" should be able to say WHERE.
    Telling someone to load `example_template/ledit_bridge/ledit_bridge.cpp`
    leaves them hunting for a 90-character path.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(
        here, "..", "..", "templates", "project", "example_template",
        "ledit_bridge", "ledit_bridge.cpp"))


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
                 # The macro polls its inbox every 15 ms while busy (0.5.2),
                 # so a 100 ms client poll would BE the round-trip latency:
                 # measured 0.153 s/call against a macro answering in ~0.02 s.
                 poll_s: float = 0.01,
                 expect_file: str = "") -> None:
        self.namespace = namespace
        # Design-targeting guard. Every command acts on whatever design is
        # VISIBLE in L-Edit, so a user clicking another window silently
        # redirects writes into their own layout. Bound here, it rides along
        # on every request (including each op inside a batch) and the macro
        # REFUSES rather than misplacing the write.
        self.expect_file = expect_file
        self.root = os.path.join(root or default_root(), namespace)
        self.inbox = os.path.join(self.root, "inbox")
        self.outbox = os.path.join(self.root, "outbox")
        self.hello_path = os.path.join(self.root, "hello.json")
        self.poll_s = poll_s

    # -- liveness ----------------------------------------------------------

    def hello(self) -> Dict[str, Any]:
        if not os.path.exists(self.hello_path):
            raise LEditBridgeError(
                f"the bridge macro has never run here (no hello.json under "
                f"{self.root})",
                "in L-Edit: Tools > Macro > Load Macro... -> "
                f"{bundled_macro_path()} . Running L-Edit is not enough, the "
                "macro must be loaded; it starts polling as soon as it is. "
                "L-Edit can also preload it at launch: ledit64.exe -u <that "
                "path>")
        return _read_json(self.hello_path)

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
                f"the bridge macro is loaded but not ticking (heartbeat "
                f"{age:.0f}s old; macro version "
                f"{hello.get('macro_version', '?')})",
                "in L-Edit run Tools > klink: Bridge Start. If that menu is "
                "gone, L-Edit was restarted and the macro must be loaded "
                f"again: Tools > Macro > Load Macro... -> "
                f"{bundled_macro_path()} . A MODAL DIALOG in L-Edit also "
                "pauses the timer and only a human can close it")

    # -- RPC ---------------------------------------------------------------

    # -- design targeting --------------------------------------------------

    def bind_file(self, name: str) -> "LEditBridgeClient":
        """Guard every later command with ``expect_file``; returns self.

        Passing the guard per call means the convenience wrappers cannot
        carry it (``ensure_layer(name, gds_layer, gds_datatype)`` has no room
        for it), which pushes callers back to raw ``call()`` with hand-built
        dicts — and one forgotten guard is a write into the user's own
        design. Bind once instead.
        """
        self.expect_file = name
        return self

    def bind_active(self) -> "LEditBridgeClient":
        """Bind to whichever design is active right now (and return self)."""
        active = self.ping().get("file", "")
        if not active:
            raise LEditBridgeError(
                "no design is open, so there is nothing to bind to",
                "call new_design {name} to bootstrap one, or open_design "
                "{path}, then bind_active() again")
        return self.bind_file(active)

    def _guarded(self, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        p = dict(params or {})
        if self.expect_file:
            p.setdefault("expect_file", self.expect_file)
        return p

    def _send(self, cmd: str, params: Optional[Dict[str, Any]] = None) -> str:
        """Write one request and return its id — no waiting."""
        rid = "req_" + uuid.uuid4().hex[:12]
        req = {"schema": 1, "id": rid, "cmd": cmd,
               "params": self._guarded(params)}
        body = json.dumps(req)
        # The macro refuses to even READ a request above its cap, and it
        # neither answers nor deletes the file (ledit_bridge.cpp: read_file
        # returns false before the DeleteFileA). Sending one buys a full
        # timeout with a misleading "check the heartbeat" message plus a
        # poison file re-read on every 200 ms tick. Fail here instead, with
        # the actual fix.
        if len(body) > _REQ_CAP_BYTES:
            raise LEditBridgeError(
                f"{cmd} request is {len(body) / 1024:.1f} KiB, over the "
                f"macro's {_REQ_CAP_BYTES // 1024} KiB cap "
                "(it would be dropped without a response)",
                "split the payload into smaller batches -- `draw` chunks "
                "itself, so pass items to draw() rather than call('draw', ...); "
                "for a whole cell or hierarchy, transfer a GDS file instead",
                "ERR_REQUEST_TOO_LARGE")
        tmp = os.path.join(self.inbox, rid + ".json.tmp")
        final = os.path.join(self.inbox, "req_" + rid + ".json")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(body)
            os.replace(tmp, final)
        except OSError as exc:
            # A bare OSError here reads like a klink bug; it is almost always
            # the caller being unable to write LOCALAPPDATA (sandbox, locked
            # profile, redirected home).
            raise LEditBridgeError(
                f"cannot write into the bridge inbox {self.inbox}: {exc}",
                "point BOTH sides at a writable directory: set "
                "KLINK_LEDIT_BRIDGE_ROOT before starting the macro AND in "
                "this process (the macro reads it too), then reload the "
                "macro in L-Edit",
                "ERR_INBOX_UNWRITABLE")
        return rid

    def _unwrap(self, resp: Dict[str, Any], cmd: str) -> Dict[str, Any]:
        if not resp.get("ok"):
            err = resp.get("error", {})
            raise LEditBridgeError(
                f"{cmd} failed: {err.get('message', 'unknown')}",
                err.get("next_action", ""),
                err.get("code", "ERR_BRIDGE"))
        return resp.get("result", {})

    def _await(self, rids, cmds, timeout: float):
        """Collect responses for already-sent requests, in request order."""
        out = [None] * len(rids)
        left = set(range(len(rids)))
        deadline = time.time() + timeout
        while left and time.time() < deadline:
            for i in sorted(left):
                path = os.path.join(self.outbox, "resp_" + rids[i] + ".json")
                if not os.path.exists(path):
                    continue
                time.sleep(min(self.poll_s / 2, 0.05))   # let rename settle
                out[i] = _read_json(path)
                try:
                    os.remove(path)
                except OSError:
                    pass
                left.discard(i)
            if left:
                time.sleep(self.poll_s)
        if left:
            i = sorted(left)[0]
            raise LEditBridgeError(
                f"timeout after {timeout:.0f}s waiting for response to "
                f"{cmds[i]} ({rids[i]}; {len(left)} of {len(rids)} "
                "outstanding)",
                "the macro is busy or stopped: a request already delivered "
                "may still have EXECUTED, so re-sending it would repeat the "
                "work -- check the result with get_cell/list_cells first. "
                "Then check hello.json freshness, close any modal dialog in "
                f"L-Edit, and read {os.path.join(self.root, 'bridge.log')}")
        return out

    def call(self, cmd: str, params: Optional[Dict[str, Any]] = None,
             timeout: float = 15.0) -> Dict[str, Any]:
        self.require_alive()
        rid = self._send(cmd, params)
        return self._unwrap(self._await([rid], [cmd], timeout)[0], cmd)

    def pipeline(self, calls, timeout: float = 60.0):
        """Send INDEPENDENT requests together, then collect all responses.

        One macro tick drains the whole inbox, so N requests cost about one
        poll interval in total instead of N of them — measured 9.9x on 12
        calls. The tick's file order decides execution order, so only use
        this where order does not matter (appends into a cell, reads).
        For an ordered sequence use :meth:`batch`.
        """
        calls = [(c, p) for c, p in calls]
        if not calls:
            return []
        self.require_alive()
        rids = [self._send(cmd, params) for cmd, params in calls]
        cmds = [cmd for cmd, _ in calls]
        return [self._unwrap(resp, cmd)
                for resp, cmd in zip(self._await(rids, cmds, timeout), cmds)]

    def batch(self, ops, timeout: float = 60.0):
        """Run an ORDERED sequence of commands, stopping at the first error.

        ``ops`` is ``[(cmd, params), ...]`` or ``[{"cmd":..., "params":...}]``.
        Needs macro >= 0.5.2. Split across several requests when the payload
        exceeds the cap; ordering still holds because the parts are sent one
        after another.
        """
        # Each op resolves its OWN target design inside the macro, so the
        # guard has to ride on every op — not just on the batch envelope.
        norm = []
        for op in ops:
            if isinstance(op, dict):
                norm.append({"cmd": op["cmd"],
                             "params": self._guarded(op.get("params", {}))})
            else:
                norm.append({"cmd": op[0], "params": self._guarded(op[1])})
        if not norm:
            return []

        overhead = len(json.dumps({"schema": 1, "id": "r" * 16,
                                   "cmd": "batch", "params": {"ops": []}}))
        budget = _REQ_CHUNK_BYTES - overhead
        results = []
        group, used = [], 0
        for i, op in enumerate(norm):
            cost = len(json.dumps(op)) + 1
            if cost > budget:
                raise LEditBridgeError(
                    f"batch ops[{i}] ({op['cmd']}) is {cost / 1024:.1f} KiB "
                    f"on its own, over the {budget / 1024:.0f} KiB budget",
                    "split that one op yourself (draw() chunks items), or "
                    "move bulk geometry as a GDS file via import_gds",
                    "ERR_REQUEST_TOO_LARGE")
            if group and used + cost > budget:
                results.extend(self.call("batch", {"ops": group},
                                         timeout=timeout).get("results", []))
                group, used = [], 0
            group.append(op)
            used += cost
        if group:
            results.extend(self.call("batch", {"ops": group},
                                     timeout=timeout).get("results", []))
        return results

    # -- convenience wrappers (thin; command set = macro capabilities) -----

    def status(self) -> Dict[str, Any]:
        """One call that answers "is this bridge usable, and where?".

        Never raises: every failure mode comes back as fields plus a
        ``next_action``, because the caller asking this question is usually
        the one that just hit a problem. ``macro_alive`` (the poll loop is
        ticking) and ``design_ready`` (a .tdb is open, so commands can act)
        fail differently and are reported separately. When the macro
        supports it, this also reports the open designs and the active
        design's cells, so one call answers "what is in L-Edit right now".
        """
        out: Dict[str, Any] = {
            "root": default_root(),
            "namespace": self.namespace,
            "namespaces": [],
            "macro_alive": False,
            "design_ready": False,
        }
        root = default_root()
        if os.path.isdir(root):
            out["namespaces"] = [
                d for d in sorted(os.listdir(root))
                if os.path.exists(os.path.join(root, d, "hello.json"))]
        try:
            out["hello"] = self.hello()
            out["heartbeat_age_s"] = round(self.hello_age_s(), 1)
            out["macro_alive"] = self.alive()
        except LEditBridgeError as exc:
            out["error"] = str(exc)
            out["next_action"] = exc.next_action
            return out
        if not out["macro_alive"]:
            out["macro_path"] = bundled_macro_path()
            out["next_action"] = (
                "the macro is loaded but not ticking. In L-Edit run Tools > "
                "klink: Bridge Start. If that menu is gone, L-Edit restarted "
                "and the macro must be loaded again: Tools > Macro > Load "
                f"Macro... -> {out['macro_path']} . A MODAL DIALOG in L-Edit "
                "also pauses the timer and only a human can close it")
            return out
        try:
            ping = self.ping()
            out["ping"] = ping
            out["design_ready"] = bool(ping.get("design_ready",
                                                ping.get("file")))
            if not out["design_ready"]:
                out["next_action"] = (
                    "no design open: call new_design {name} to bootstrap, or "
                    "open_design {path}")
        except LEditBridgeError as exc:
            out["error"] = str(exc)
            out["next_action"] = exc.next_action
            return out

        caps = ping.get("capabilities") or []
        if "list_designs" in caps:
            try:
                out["designs"] = self.list_designs()
            except LEditBridgeError as exc:
                out["designs_error"] = str(exc)
        if "list_cells" in caps and out["design_ready"]:
            try:
                out["cells"] = self.list_cells()
            except LEditBridgeError as exc:
                out["cells_error"] = str(exc)
        return out

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

    def draw(self, items, cell: Optional[str] = None,
             expect_file: str = "", timeout: float = 15.0) -> Dict[str, Any]:
        """Draw ``items``, split across as many requests as the macro's size
        cap needs — one logical call for the caller, N on the wire.

        Throughput is not the reason to chunk: measured cost is ~0.02 ms per
        shape, flat as the target cell fills. The cap is. ``drawn`` sums the
        whole run and ``requests`` reports how many it took.
        """
        items = list(items)
        if not items:
            raise LEditBridgeError(
                "draw called with no items",
                "pass items=[{'kind':'box','layer':'Metal1',"
                "'bbox_um':[0,0,10,10]}]")
        base: Dict[str, Any] = {}
        if cell:
            base["cell"] = cell
        if expect_file:
            base["expect_file"] = expect_file
        # measure the request as it will actually be sent (guard included)
        overhead = len(json.dumps({"schema": 1, "id": "r" * 16, "cmd": "draw",
                                   "params": self._guarded(
                                       dict(base, items=[]))}))
        budget = _REQ_CHUNK_BYTES - overhead

        chunks = []
        batch, used = [], 0
        for i, item in enumerate(items):
            cost = len(json.dumps(item)) + 1          # + separator
            if cost > budget:
                raise LEditBridgeError(
                    f"draw items[{i}] is {cost / 1024:.1f} KiB on its own, "
                    f"over the {budget / 1024:.0f} KiB per-request budget",
                    "simplify that shape (fewer points), or transfer the "
                    "cell as a GDS file instead of drawing it",
                    "ERR_REQUEST_TOO_LARGE")
            if batch and used + cost > budget:
                chunks.append(batch)
                batch, used = [], 0
            batch.append(item)
            used += cost
        if batch:
            chunks.append(batch)

        if len(chunks) == 1:
            outs = [self.call("draw", dict(base, items=chunks[0]),
                              timeout=timeout)]
        else:
            # Chunks are appends into one cell, so the order between them does
            # not matter — pipeline them and pay one poll interval, not N.
            outs = self.pipeline(
                [("draw", dict(base, items=c)) for c in chunks],
                timeout=max(timeout, 30.0))

        drawn = layers_created = 0
        for one in outs:
            drawn += int(one.get("drawn", 0) or 0)
            layers_created += int(one.get("layers_created", 0) or 0)
        result = dict(outs[-1])
        result["drawn"] = drawn
        result["layers_created"] = layers_created
        result["requests"] = len(chunks)
        return result

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

    def list_designs(self):
        """Every open design, not just the visible one (macro >= 0.5.2)."""
        return self.call("list_designs")["designs"]

    def activate_design(self, file: str) -> Dict[str, Any]:
        """Switch between ALREADY-OPEN designs by name — no disk path, so an
        unsaved new_design is reachable too (macro >= 0.5.2)."""
        return self.call("activate_design", {"file": file})

    def import_gds(self, path: str, overwrite: str = "all",
                   timeout: float = 300.0,
                   pad_to_block: bool = True) -> Dict[str, Any]:
        """Import a GDS with its cell HIERARCHY intact (macro >= 0.5.2).

        The route for bulk transfer: no flattening, no request-size cap, and
        ``overwrite="all"`` makes a re-import idempotent where ``draw`` only
        appends. Incremental and parametric work stays on the RPC path.

        ``pad_to_block`` handles a real interop trap: L-Edit's reader expects
        the classic Calma 2048-byte block padding, which KLayout does not
        write. Unpadded, the import aborts at EOF ("Unexpected element",
        position == file size), pops a MODAL dialog that freezes the bridge
        until a human closes it, and leaves empty cell shells behind. Rather
        than edit the caller's file, a padded sibling copy is imported.
        """
        src = os.path.abspath(path)
        used = src
        padded_copy = ""
        if pad_to_block and os.path.exists(src):
            size = os.path.getsize(src)
            if size % 2048:
                with open(src, "rb") as f:
                    data = f.read()
                used = padded_copy = os.path.join(
                    os.path.dirname(src),
                    os.path.splitext(os.path.basename(src))[0] + ".ledit.gds")
                with open(used, "wb") as f:
                    f.write(data + b"\x00" * (-len(data) % 2048))
        out = self.call("import_gds",
                        {"path": used, "overwrite": overwrite},
                        timeout=timeout)
        if padded_copy:
            out["padded_copy"] = padded_copy
            out["source"] = src
        return out

    def save_design(self, path: str = "") -> Dict[str, Any]:
        p: Dict[str, Any] = {}
        if path:
            p["path"] = path
        return self.call("save_design", p)

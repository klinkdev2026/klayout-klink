"""
klink recorder: turn live KLayout change-events into a runnable Python
script that replays every manual action through the typed RPC surface.

Usage
-----
* The toolbar button ("REC") calls :func:`Recorder.toggle` on the
  singleton.
* External clients can drive it over the wire via the
  `recorder.start` / `recorder.stop` / `recorder.status` RPCs.

Integration
-----------
The Recorder is a passive sink. :class:`SignalHub._emit` calls into
:func:`Recorder.ingest` for every event it broadcasts. So recording
taps the SAME event stream external subscribers see, including the
`caused_by` field - the recorder just doesn't filter by it (we log
all mutations, whether they came from a human mouse or a prior RPC).

Scope
-----
v1 translates (in order, as events arrive):

* cells_changed.added / removed / renamed -> cell.create / cell.delete
  / cell.rename.  PCell variant cells (`pcell` field present) are
  skipped because they are a side effect of `instance.insert_pcell`.
* layer_list_changed -> layer.ensure (diffed against last snapshot).
* shapes_changed.changed_layers[].added / removed -> shape.insert_*
  (box/polygon/path/text) / shape.delete (best-effort by bbox).
* instances_changed.changed_cells[].added / removed ->
  instance.insert / instance.insert_pcell / instance.delete.
* selection_changed -> selection.clear (v1: set-selection omitted -
  replaying a selection is rarely interesting; emitted as comment).
* viewport_changed -> view.zoom_box (coalesced: only the last
  viewport in a burst is emitted, so scroll/pan noise is harmless).

Output file is a single self-contained script that boots a
KLinkClient and replays every action in order. Comments carry the
original timestamp relative to recording start plus the `caused_by`
shorthand when present.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Optional

from . import menu_hints
from . import recorder_pya
from .log import get_logger

_log = get_logger("recorder")


# Module-level singleton so the toolbar button, RPCs, and SignalHub
# all see the same state. Only one recording at a time.
_REC = None


def instance() -> "Recorder":
    global _REC
    if _REC is None:
        _REC = Recorder()
    return _REC


class Recorder:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._recording = False
        self._started_at: Optional[float] = None
        self._output_path: Optional[str] = None
        self._actions: list[dict] = []  # each: {t, code, raw?}
        self._event_count = 0

        # State for delta computation.
        self._last_layer_set: set = set()
        self._pending_viewport: Optional[dict] = None
        self._pending_viewport_t: Optional[float] = None

        # Bootstrap snapshot captured at start(). The replay script
        # re-creates these cells / layers UP FRONT so that replaying on
        # a fresh layout (or a different layout) still works. Without
        # this, the first line of a recording made on top of an
        # existing layout would fail with "no cell named 'TOP'".
        self._initial_cells: list = []   # [{"name": str, "is_top": bool}]
        self._initial_layers: list = []  # [(layer:int, datatype:int, name|None)]
        self._initial_top_cell: Optional[str] = None

        # File-load context: when layout.show_file (or klive-compat)
        # triggers a file load, a COMMENT with the file path is emitted
        # for context. Individual shape/cell/instance events are always
        # recorded. `_show_file_path` is set by the RPC handler /
        # klive_compat BEFORE loading, and cleared when the first
        # non-file-load event arrives.
        self._show_file_path: Optional[str] = None
        self._show_file_emitted: bool = False

    # ------------------------------------------------------------------
    # public API used by the toolbar button and RPCs
    # ------------------------------------------------------------------
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    def status(self) -> dict:
        with self._lock:
            return {
                "recording": self._recording,
                "started_at": self._started_at,
                "elapsed": (time.monotonic() - self._started_at) if self._started_at else 0.0,
                "event_count": self._event_count,
                "action_count": len(self._actions),
                "output_path": self._output_path,
            }

    def start(self, output_path: Optional[str] = None) -> dict:
        with self._lock:
            if self._recording:
                return self.status()
            self._recording = True
            self._started_at = time.monotonic()
            self._output_path = output_path
            self._actions = []
            self._event_count = 0
            self._pending_viewport = None
            self._pending_viewport_t = None
            self._show_file_path = None
            self._show_file_emitted = False
            # Snapshot current layer list so future layer_list_changed
            # events report a delta rather than replaying the whole
            # initial state.
            self._last_layer_set = self._current_layer_set()
            # Capture existing cells + layers so the replay script is
            # self-contained: it creates them at the top if the replay
            # target is empty, and no-ops if they already exist.
            self._initial_cells, self._initial_top_cell = \
                self._current_cell_list()
            self._initial_layers = self._current_layer_list()
            # Opt-in menu-action observer. DISABLED BY DEFAULT because
            # the initial rollout broke the SignalHub pipeline in some
            # builds (0 events observed during a recording session);
            # the exact mechanism is under investigation. Users who
            # want intent-annotation comments on their recorded
            # scripts can enable it via the env var:
            #     KLINK_MENU_HINTS=1
            # If it's working cleanly we'll flip the default back to
            # on-by-default.
            if os.environ.get("KLINK_MENU_HINTS", "0") == "1":
                try:
                    menu_hints.install_once()
                except Exception as e:
                    _log.warning("menu_hints install skipped: %s", e)
            _log.info(
                "started. output_path=%r (bootstrap: %d cell(s), %d layer(s))",
                output_path, len(self._initial_cells), len(self._initial_layers),
            )
            return self.status()

    def stop(self, output_path: Optional[str] = None) -> dict:
        with self._lock:
            if not self._recording:
                info = self.status()
                info["wrote"] = False
                return info
            self._flush_pending_viewport()
            self._recording = False
            elapsed = (time.monotonic() - (self._started_at or time.monotonic()))
            path = output_path or self._output_path or self._default_path()
            wrote = False
            try:
                self._write_file(path, elapsed)
                wrote = True
                _log.info("wrote %d action(s) to %s", len(self._actions), path)
            except Exception as e:
                _log.error("WRITE FAILED: %s", e, exc_info=True)
            info = {
                "recording": False,
                "started_at": self._started_at,
                "elapsed": elapsed,
                "event_count": self._event_count,
                "action_count": len(self._actions),
                "output_path": path,
                "wrote": wrote,
            }
            # Keep actions around for one `status` read after stop, then
            # garbage-collect on the next start().
            return info

    def toggle(self, output_path: Optional[str] = None) -> dict:
        if self.is_recording():
            return self.stop(output_path)
        return self.start(output_path)

    # ------------------------------------------------------------------
    # SignalHub hook
    # ------------------------------------------------------------------
    def ingest(self, channel: str, data: dict) -> None:
        """Called synchronously from SignalHub._emit. Must NEVER raise
        back into the signal pipeline; swallow and log on any error."""
        with self._lock:
            if not self._recording:
                return
            try:
                self._event_count += 1
                t = time.monotonic() - (self._started_at or time.monotonic())
                caused = _cause_tag(data)

                # If this event was caused by a layout.show_file (RPC or
                # klive-compat), emit a COMMENT recording the file path
                # for context, then let all individual events fall through
                # so every shape/cell/instance is captured.
                cb = data.get("caused_by") or []
                is_show_file = any(
                    c.get("method") == "layout.show_file" for c in cb
                )
                if is_show_file:
                    path = self._show_file_path
                    if path and not self._show_file_emitted:
                        self._actions.append({
                            "t": t,
                            "caused": "layout.show_file",
                            "code": f"#c.layout_show_file({path!r})",
                            "pya": f"# layout.show_file({path!r})",
                        })
                        self._show_file_emitted = True

                # First non-file-load event after a file load: reset
                # burst state so the next file load gets a fresh comment.
                if self._show_file_path is not None and not is_show_file:
                    self._show_file_path = None
                    self._show_file_emitted = False

                _log.debug(
                    "channel=%r is_show_file=%s n_actions_before=%d",
                    channel, is_show_file, len(self._actions),
                )
                if channel == "cells_changed":
                    _log.debug(
                        "cells_changed: added=%d removed=%d",
                        len(data.get("added") or []), len(data.get("removed") or []),
                    )
                    self._on_cells(data, t, caused)
                elif channel == "layer_list_changed":
                    _log.debug(
                        "layer_list_changed: layers=%d last_set_size=%d",
                        len(data.get("layers") or []), len(self._last_layer_set),
                    )
                    self._on_layers(data, t, caused)
                elif channel == "shapes_changed":
                    _log.debug(
                        "shapes_changed: changed_layers=%d",
                        len(data.get("changed_layers") or []),
                    )
                    self._on_shapes(data, t, caused)
                elif channel == "instances_changed":
                    _log.debug(
                        "instances_changed: changed_cells=%d",
                        len(data.get("changed_cells") or []),
                    )
                    self._on_instances(data, t, caused)
                elif channel == "selection_changed":
                    self._on_selection(data, t, caused)
                elif channel == "viewport_changed":
                    self._on_viewport(data, t, caused)
                _log.debug("channel=%r n_actions_after=%d", channel, len(self._actions))
            except Exception as e:
                _log.error("ingest %r failed: %s", channel, e, exc_info=True)

    # ------------------------------------------------------------------
    # per-channel handlers
    # ------------------------------------------------------------------
    def _on_cells(self, data: dict, t: float, caused: str) -> None:
        for c in data.get("added") or []:
            if c.get("pcell"):
                continue  # side-effect of instance.insert_pcell
            name = c.get("name")
            if name:
                # Use _ensure_cell (inlined in the replay script) rather
                # than raw c.cell_create: KLayout's create_cell is NOT
                # idempotent - calling it for an already-taken name
                # yields "NAME$1", leaving subsequent shape.insert calls
                # pointing at the ORIGINAL NAME and NAME$1 as an empty
                # orphan. Replay semantics want "make sure NAME exists";
                # _ensure_cell captures that intent.
                self._append(t, caused,
                             f"_ensure_cell(c, {name!r})",
                             pya=recorder_pya.cell_create(name))
        for c in data.get("removed") or []:
            if c.get("pcell"):
                continue
            name = c.get("name")
            if name:
                self._append(t, caused,
                             f"c.cell_delete({name!r}, recursive=True)",
                             pya=recorder_pya.cell_delete(name))
        for r in data.get("renamed") or []:
            old = r.get("old_name")
            new = r.get("new_name")
            if old and new and old != new:
                self._append(t, caused,
                             f"c.cell_rename({old!r}, {new!r})",
                             pya=recorder_pya.cell_rename(old, new))

    def _on_layers(self, data: dict, t: float, caused: str) -> None:
        layers = data.get("layers") or []
        now_set = {(int(l.get("layer", 0)), int(l.get("datatype", 0)))
                   for l in layers
                   if l.get("layer") is not None}
        added = now_set - self._last_layer_set
        self._last_layer_set = now_set
        for (L, D) in sorted(added):
            self._append(t, caused, f"c.layer_ensure({L}, {D})",
                         pya=recorder_pya.layer_ensure(L, D))

    def _on_shapes(self, data: dict, t: float, caused: str) -> None:
        # IMPORTANT: emit removed BEFORE added. A "move" in KLayout's
        # signal model is a fingerprint change, which diffs to one
        # removed fp + one added fp. The server's shape.delete matches
        # by `each_touching(bbox)`, so if the added (new-position)
        # shape went first the subsequent delete would ALSO match it
        # whenever the old and new bboxes overlap - which is what
        # happens for any small-translation move. Deleting first
        # avoids self-inflicted wounds: at replay time the new shape
        # doesn't exist yet, so only the old one matches.
        for cl in data.get("changed_layers") or []:
            cell = cl.get("cell")
            L = cl.get("layer")
            D = cl.get("datatype", 0)
            if cell is None or L is None:
                continue
            for s in cl.get("removed") or []:
                code = _shape_delete_code(cell, L, D, s)
                pya = recorder_pya.shape_delete(cell, L, D, s)
                if code or pya:
                    self._append(t, caused, code or "", pya=pya)
            for s in cl.get("added") or []:
                code = _shape_insert_code(cell, L, D, s)
                pya = recorder_pya.shape_insert(cell, L, D, s)
                if code or pya:
                    self._append(t, caused, code or "", pya=pya)

    def _on_instances(self, data: dict, t: float, caused: str) -> None:
        # Same rationale as _on_shapes: delete before insert so a move
        # (remove-old-fp + add-new-fp) replays correctly even when the
        # old and new placements overlap.
        for cc in data.get("changed_cells") or []:
            parent = cc.get("cell")
            if parent is None:
                continue
            for inst in cc.get("removed") or []:
                code = _instance_delete_code(parent, inst)
                pya = recorder_pya.instance_delete(parent, inst)
                if code or pya:
                    self._append(t, caused, code or "", pya=pya)
            for inst in cc.get("added") or []:
                code = _instance_insert_code(parent, inst)
                pya = recorder_pya.instance_insert(parent, inst)
                if code or pya:
                    self._append(t, caused, code or "", pya=pya)

    def _on_selection(self, data: dict, t: float, caused: str) -> None:
        count = int(data.get("count", 0))
        if count == 0:
            # De-noise: skip consecutive selection_clear() spam.
            if self._actions and self._actions[-1].get("code") == "c.selection_clear()":
                return
            self._append(t, caused, "c.selection_clear()",
                         pya=recorder_pya.selection_clear())
        else:
            items = data.get("items") or []
            # v1: emit as non-executable comment; reproducing exact
            # selection is rarely interesting and the API (selection.set_box)
            # only covers one common subset.
            preview = ", ".join(_item_brief(it) for it in items[:3])
            more = "" if len(items) <= 3 else f" (+{len(items)-3} more)"
            self._append(t, caused,
                         f"# selection -> {count} item(s): {preview}{more}",
                         pya=recorder_pya.selection_comment(count, items))

    def _on_viewport(self, data: dict, t: float, caused: str) -> None:
        # v1: viewport recording is DISABLED. Two reasons:
        #   1. The `bbox_dbu` field in viewport_changed actually holds
        #      floating-point um values, not dbu integers, so feeding
        #      them straight to view.zoom_box mis-scales the replay.
        #   2. Screen aspect ratio differs between record and replay, so
        #      an exact viewport reproduction rarely matters for human
        #      intent. If someone does care, future versions can emit
        #      a `c.show_cell(...)` hint instead.
        # Intentional no-op. We still drop any pending viewport that
        # was accumulated by an earlier version of the state so stop()
        # has nothing to flush.
        self._pending_viewport = None
        self._pending_viewport_t = None

    # ------------------------------------------------------------------
    # action accumulator
    # ------------------------------------------------------------------
    def _append(self, t: float, caused: str, code: str, pya: str = "") -> None:
        # If there's a pending viewport and a non-viewport event is
        # landing, flush the viewport first so ordering stays honest.
        if self._pending_viewport is not None:
            self._flush_pending_viewport()
        # Consume a menu-hint (user-intent annotation) exactly once per
        # command burst. Because consume_hint() clears the cache on
        # read, the FIRST sub-action of a command (e.g., the first
        # shape.delete in a Boolean Union) gets a hint-prefixed dict
        # and every subsequent sub-action gets hint=None. Output
        # rendering then emits ONE `# user command: ...` line followed
        # by all the actions that came out of that one menu click.
        #
        # 2s TTL, matches the menu_hints default. Rationale: the diff
        # debouncer coalesces signals over ~300-500 ms, so 2s is
        # comfortable headroom even under sluggish Qt redraws, while
        # short enough that a menu click 30s ago never mis-attributes
        # a freshly-drawn box.
        hint: Optional[dict] = None
        try:
            hint = menu_hints.consume_hint(max_age_s=2.0)
        except Exception:
            hint = None
        self._actions.append({
            "t": t,
            "caused": caused,
            "code": code,
            "pya": pya or "",
            "hint": hint,
        })

    def _flush_pending_viewport(self) -> None:
        if self._pending_viewport is None:
            return
        bbox = self._pending_viewport.get("bbox_dbu")
        t = self._pending_viewport_t or 0.0
        self._pending_viewport = None
        self._pending_viewport_t = None
        if not bbox or len(bbox) != 4:
            return
        # bbox_dbu in viewport_changed is floats (um, not integer dbu);
        # view.zoom_box also takes a free-form list so we pass straight
        # through. Skip if degenerate.
        if bbox[0] == bbox[2] or bbox[1] == bbox[3]:
            return
        self._actions.append({
            "t": t,
            "caused": "viewport",
            "code": f"c.zoom_box({list(bbox)!r})",
        })

    # ------------------------------------------------------------------
    # output
    # ------------------------------------------------------------------
    def _default_path(self) -> str:
        try:
            import pya
            docs = os.path.join(os.path.expanduser("~"), "Documents",
                                "klink_recordings")
            os.makedirs(docs, exist_ok=True)
        except Exception:
            docs = os.getcwd()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        return os.path.join(docs, f"klink_record_{stamp}.py")

    # ------------------------------------------------------------------
    # helpers (module-level or thin wrappers)
    # ------------------------------------------------------------------
    @staticmethod
    def _pya_path(rpc_path: str) -> str:
        """Derive pya companion path from the RPC path."""
        if rpc_path.endswith(".py"):
            return rpc_path[:-3] + "_pya.py"
        return rpc_path + "_pya.py"

    def _write_file(self, path: str, elapsed: float) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".",
                    exist_ok=True)
        n = len(self._actions)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        lines: list[str] = []
        lines.append(f'"""klink recording, {stamp}.')
        lines.append(f"{n} action(s) over {elapsed:.1f}s, "
                     f"{self._event_count} event(s) observed.")
        if self._initial_cells:
            top_names = [e["name"] for e in self._initial_cells if e["is_top"]]
            lines.append(
                f"Started from a layout with {len(self._initial_cells)} "
                f"cell(s), tops={top_names!r}, "
                f"active={self._initial_top_cell!r}."
            )
        lines.append('Replay directly: python <this-file>.py')
        lines.append('(needs KLayout running with a layout open, '
                     'klink plugin loaded.)')
        lines.append('"""')
        lines.append("")
        lines.append("import os, sys")
        lines.append("sys.path.insert(0, os.path.dirname(os.path.dirname("
                     "os.path.abspath(__file__))))")
        lines.append("from klink import KLinkClient")
        lines.append("")
        lines.append("")

        # --------------------------------------------------------------
        # _ensure_cell: idempotent cell_create. KLayout's create_cell
        # always creates a NEW cell (auto-suffixed on name clash), so
        # we look it up first. Keeps replay safe across multiple runs
        # on the same layout.
        # --------------------------------------------------------------
        lines.append("def _ensure_cell(c, name):")
        lines.append(
            "    page = c.cell_list(name_prefix=name, limit=5000)"
        )
        lines.append("    for ce in page.get('cells', []):")
        lines.append("        if ce.get('name') == name:")
        lines.append("            return")
        lines.append("    c.cell_create(name)")
        lines.append("")
        lines.append("")

        # --------------------------------------------------------------
        # Bootstrap block: make the replay self-contained.
        #
        # Uses `_ensure_cell` for cells (idempotent). Layers use
        # layer.ensure which IS already idempotent on the server.
        # --------------------------------------------------------------
        lines.append("def _bootstrap(c) -> None:")
        lines.append(
            '    """Recreate the cells/layers that existed at record '
            'time, if missing on the replay target."""'
        )
        if not self._initial_cells and not self._initial_layers:
            lines.append("    return  # nothing to bootstrap")
        else:
            if self._initial_cells:
                names = [e["name"] for e in self._initial_cells]
                lines.append(f"    for nm in {names!r}:")
                lines.append("        _ensure_cell(c, nm)")
            if self._initial_layers:
                lines.append(f"    initial_layers = {self._initial_layers!r}")
                lines.append("    for L, D, name in initial_layers:")
                lines.append("        if name:")
                lines.append("            c.layer_ensure(L, D, name=name)")
                lines.append("        else:")
                lines.append("            c.layer_ensure(L, D)")
        lines.append("")
        lines.append("")

        lines.append("def main() -> None:")
        lines.append("    c = KLinkClient()")
        lines.append("    c.connect()")
        lines.append("    _bootstrap(c)")
        if n == 0:
            lines.append("    # (no actions recorded)")
        else:
            last_cause = None
            for a in self._actions:
                caused = a.get("caused") or ""
                if caused and caused != last_cause:
                    lines.append(f"    # -- {caused} --")
                    last_cause = caused
                # Human-intent annotation: if this action was the first
                # one produced by a menu click (see _append), write a
                # standalone comment naming the command before the
                # code. Format is intentionally LLM-friendly: stable
                # action path (parseable) first, then human title in
                # parens if available.
                hint = a.get("hint")
                if hint:
                    path = hint.get("path") or "?"
                    title = (hint.get("title") or "").strip()
                    if title:
                        lines.append(
                            f"    # user command: {path}  "
                            f"(title: {title!r})"
                        )
                    else:
                        lines.append(f"    # user command: {path}")
                ts = f"+{a['t']:6.2f}s"
                code = a["code"]
                if code.lstrip().startswith("#"):
                    lines.append(f"    {code}  # {ts}")
                else:
                    lines.append(f"    {code}  # {ts}")
        lines.append("    c.close()")
        lines.append("")
        lines.append("")
        lines.append('if __name__ == "__main__":')
        lines.append("    main()")
        lines.append("")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        # Write pya companion file.
        pya_path = self._pya_path(path)
        recorder_pya.render_file(
            pya_path, elapsed,
            event_count=self._event_count,
            initial_cells=self._initial_cells,
            initial_layers=self._initial_layers,
            initial_top_cell=self._initial_top_cell or "",
            actions=self._actions,
        )
        _log.info("wrote pya companion to %s", pya_path)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _current_layer_set(self) -> set:
        try:
            import pya
            mw = pya.Application.instance().main_window()
            if mw is None:
                return set()
            view = mw.current_view()
            if view is None:
                return set()
            cv = view.active_cellview()
            if cv is None or not cv.is_valid():
                return set()
            ly = cv.layout()
            out: set = set()
            for idx in ly.layer_indexes():
                info = ly.get_info(idx)
                out.add((int(info.layer), int(info.datatype)))
            return out
        except Exception:
            return set()

    def _active_layout(self):
        """Return the active pya.Layout, or None if no layout is open.
        Helper for bootstrap snapshots."""
        try:
            import pya
            mw = pya.Application.instance().main_window()
            if mw is None:
                return None
            view = mw.current_view()
            if view is None:
                return None
            cv = view.active_cellview()
            if cv is None or not cv.is_valid():
                return None
            return cv.layout()
        except Exception:
            return None

    def _current_cell_list(self):
        """Snapshot of every cell that exists in the active layout RIGHT
        NOW, plus the name of the currently-active top cell (to use as
        a replay-time default top when the target layout is empty).

        Returns (cells, active_top_name). cells is a list of
        {"name": str, "is_top": bool} entries, sorted with tops first."""
        ly = self._active_layout()
        if ly is None:
            return [], None
        active_top = None
        try:
            import pya
            mw = pya.Application.instance().main_window()
            view = mw.current_view() if mw else None
            if view is not None:
                cv = view.active_cellview()
                if cv is not None and cv.cell is not None:
                    active_top = cv.cell.name
        except Exception:
            pass

        cells: list = []
        try:
            for c in ly.each_cell():
                try:
                    cells.append({
                        "name": c.name,
                        "is_top": bool(c.is_top()),
                    })
                except Exception:
                    continue
        except Exception:
            pass
        # Tops first; stable order within each group.
        cells.sort(key=lambda e: (not e["is_top"], e["name"]))
        return cells, active_top

    def _current_layer_list(self):
        """Snapshot existing layers as a list of (L, D, name|None) tuples."""
        ly = self._active_layout()
        if ly is None:
            return []
        out: list = []
        try:
            for idx in ly.layer_indexes():
                info = ly.get_info(idx)
                try:
                    L = int(info.layer)
                    D = int(info.datatype)
                except Exception:
                    continue
                name = info.name or None
                out.append((L, D, name))
        except Exception:
            pass
        out.sort(key=lambda t: (t[0], t[1]))
        return out


# ----------------------------------------------------------------------
# translation helpers (module-level, stateless)
# ----------------------------------------------------------------------
def _cause_tag(data: dict) -> str:
    cb = data.get("caused_by")
    if not cb:
        return "manual"
    methods = []
    for c in cb:
        m = c.get("method")
        if m and m not in methods:
            methods.append(m)
    if not methods:
        return "rpc"
    return "rpc:" + ",".join(methods)


def _shape_insert_code(cell: str, L: int, D: int, s: dict) -> Optional[str]:
    kind = s.get("type")
    if kind == "box":
        b = s.get("bbox_dbu")
        if not b:
            return None
        return (f"c.shape_insert_box({cell!r}, layer={L}, datatype={D}, "
                f"bbox_dbu={list(b)!r})")
    if kind == "polygon":
        pts = s.get("points_dbu")
        if not pts:
            return None
        return (f"c.shape_insert_polygon({cell!r}, layer={L}, datatype={D}, "
                f"points_dbu={_pts_repr(pts)})")
    if kind == "path":
        pts = s.get("points_dbu")
        w = s.get("width_dbu")
        if not pts or w is None:
            return None
        # Endcap attributes come from pya.Path.bgn_ext / end_ext / round
        # (captured by signals._fingerprint). We ALWAYS emit all three,
        # even when they equal pya's defaults (width/2, width/2, False
        # - "square"), so the replay script is an exact transcript. A
        # user who drew a `flush` path (bgn=end=0) needs those zeros in
        # the output; omitting them would let server-side defaults kick
        # in and silently turn the replay into square endcaps.
        bext = int(s.get("begin_ext_dbu", 0))
        eext = int(s.get("end_ext_dbu", 0))
        rnd = bool(s.get("round_ends", False))
        return (f"c.shape_insert_path({cell!r}, layer={L}, datatype={D}, "
                f"points_dbu={_pts_repr(pts)}, width_dbu={int(w)}, "
                f"begin_ext_dbu={bext}, end_ext_dbu={eext}, "
                f"round_ends={rnd})")
    if kind == "text":
        pos = s.get("position_dbu")
        txt = s.get("string", "")
        if not pos:
            return None
        return (f"c.shape_insert_text({cell!r}, {txt!r}, "
                f"layer={L}, datatype={D}, "
                f"position_dbu={list(pos)!r})")
    return f"# TODO: unsupported shape kind={kind!r} on {cell!r} layer={L}/{D}"


def _shape_delete_code(cell: str, L: int, D: int, s: dict) -> Optional[str]:
    kind = s.get("type")
    # Compute a tight bbox hitting only the removed shape. shape.delete
    # takes bbox_dbu + kinds filter. Ambiguity warning is in the comment
    # because bbox-based deletion will also grab any other same-kind
    # shape whose bbox is fully inside this bbox.
    bbox = None
    if kind == "box":
        bbox = s.get("bbox_dbu")
    elif kind in ("polygon", "path"):
        pts = s.get("points_dbu") or []
        if pts:
            px = int(pts[0][0])
            py = int(pts[0][1])
            pad = 2 + (int(s.get("width_dbu", 0)) // 2 if kind == "path" else 0)
            bbox = [px - pad, py - pad, px + pad, py + pad]
    elif kind == "text":
        p = s.get("position_dbu")
        if p and len(p) == 2:
            bbox = [int(p[0]) - 1, int(p[1]) - 1,
                    int(p[0]) + 1, int(p[1]) + 1]
    if bbox is None:
        return f"# TODO: removed shape could not be located: kind={kind!r}"
    # shape.delete's `kinds` filter uses PLURAL names (matches shape.query
    # and the KLayout Shapes iterator enums): polygons / boxes / paths /
    # texts. The fingerprint / event layer uses singular names, so we
    # translate here rather than at the event boundary to keep the event
    # payload faithful to what a single shape actually is.
    plural = {"box": "boxes", "polygon": "polygons",
              "path": "paths", "text": "texts"}.get(kind)
    kind_filter = f", kinds=[{plural!r}]" if plural else ""
    return (f"c.shape_delete({cell!r}, layer={L}, datatype={D}, "
            f"bbox_dbu={list(bbox)!r}{kind_filter})"
            f"  # may also match overlapping shapes; verify before replay")


def _trans_kwargs(trans: dict) -> dict:
    """Event-payload `trans_dbu` (shape: {disp, angle, mirror, mag})
    -> flat kwargs the KLinkClient wrappers accept
    ({position_dbu, rotation, mirror, magnification}).

    Event `angle` is in DEGREES (float; signals.py normalises both
    pya.Trans 90-deg steps and pya.ICplxTrans free angles into
    degrees), and the client wrapper's `rotation` is also in plain
    degrees, so they line up one-to-one. We ALWAYS emit every field
    (even when it equals the client wrapper's default) so the replay
    script is an exact transcript of what the user did, not an
    interpretation. If anyone later doubts a value, they see it in
    the code."""
    disp = trans.get("disp") or [0, 0]
    angle_deg = float(trans.get("angle", 0.0))
    mirror = bool(trans.get("mirror", False))
    mag = float(trans.get("mag", 1.0))
    # KLayout's ICplxTrans.angle comes back as IEEE-754 double, so a
    # user's 30-degree rotation arrives as 29.999999999999993 and 45
    # as 44.99999999999999. Clip to 9 decimal places: that's 1e-9 deg
    # (sub-pico-radian) of precision, far below anything meaningful
    # for layout, but keeps the generated script readable and avoids
    # "why does my 30 look like 29.9999..." double-takes on review.
    angle_deg = round(angle_deg, 9)
    # Preserve integer-degree rotations as ints for readability (90
    # stays 90, not 90.0) while keeping arbitrary angles intact.
    if angle_deg == int(angle_deg):
        angle_out: "int | float" = int(angle_deg)
    else:
        angle_out = angle_deg
    mag = round(mag, 9)
    return {
        "position_dbu": [int(disp[0]), int(disp[1])],
        "rotation": angle_out,
        "mirror": mirror,
        "magnification": mag,
    }


def _kwargs_str(kw: dict) -> str:
    """Render a flat kwargs dict as ', key=value, ...' source, with
    a leading comma when non-empty. repr() values so nested lists /
    dicts round-trip correctly."""
    if not kw:
        return ""
    return ", " + ", ".join(f"{k}={v!r}" for k, v in kw.items())


def _instance_insert_code(parent: str, inst: dict) -> Optional[str]:
    trans = inst.get("trans_dbu") or {}
    kw = _trans_kwargs(trans)
    # Arrays (CellInstArray with na/nb > 1) used to be silently
    # dropped - the event payload carried the `array` dict but the
    # recorder never threaded it into the generated code, so every
    # replay script placed ONE copy of a N-by-M arrayed instance.
    # Thread it through as an `array=` kwarg: both the server-side
    # instance.insert and instance.insert_pcell methods now accept
    # the {na, nb, a_dbu, b_dbu} vector form.
    array = inst.get("array")
    if array:
        kw["array"] = {
            "na": int(array.get("na", 1)),
            "nb": int(array.get("nb", 1)),
            "a_dbu": [int(array.get("a_dbu", [0, 0])[0]),
                      int(array.get("a_dbu", [0, 0])[1])],
            "b_dbu": [int(array.get("b_dbu", [0, 0])[0]),
                      int(array.get("b_dbu", [0, 0])[1])],
        }
    kw_str = _kwargs_str(kw)
    pcell = inst.get("pcell")
    if pcell:
        lib = pcell.get("lib", "Basic")
        pname = pcell.get("pcell_name") or pcell.get("name")
        params = pcell.get("params") or {}
        params_clean = _clean_pcell_params(params)
        if not pname:
            return "# TODO: pcell instance missing pcell_name"
        return (f"c.instance_insert_pcell({parent!r}, {pname!r}, "
                f"library={lib!r}, params={params_clean!r}{kw_str})")
    target = inst.get("target_cell")
    if not target:
        return "# TODO: instance missing target_cell"
    return f"c.instance_insert({parent!r}, {target!r}{kw_str})"


def _instance_delete_code(parent: str, inst: dict) -> Optional[str]:
    target = inst.get("target_cell")
    trans = inst.get("trans_dbu") or {}
    disp = trans.get("disp") or [0, 0]
    if not target:
        return "# TODO: removed instance missing target_cell"
    # bbox_dbu: +/-1 around origin to hopefully single out this one
    # instance by position. Not bullet-proof; flag in comment.
    bbox = [int(disp[0]) - 1, int(disp[1]) - 1,
            int(disp[0]) + 1, int(disp[1]) + 1]
    return (f"c.instance_delete({parent!r}, child={target!r}, "
            f"bbox_dbu={bbox!r})"
            f"  # may delete multiple instances of {target!r}; verify")


def _clean_pcell_params(params: dict) -> dict:
    """Pass PCell params through verbatim. Recorder deliberately
    does NOT prune anything - even fields marked 'computed' in the
    PCell declaration are kept, because:

    * `server.instance.insert_pcell` already tolerates extra keys
      (unknown params are ignored by pya).
    * Dropping any field risks silent data loss if a future KLayout
      build changes what's an input vs an output.
    * The replay script doubles as a forensic transcript of what
      the user did; completeness beats brevity."""
    return dict(params)


def _pts_repr(pts) -> str:
    return "[" + ", ".join(f"[{int(p[0])}, {int(p[1])}]" for p in pts) + "]"


def _item_brief(it: dict) -> str:
    if it.get("is_cell_inst"):
        return f"inst->{it.get('target_cell')!r}"
    return f"{it.get('shape_type', 'shape')}({it.get('layer')}/{it.get('datatype', 0)})"

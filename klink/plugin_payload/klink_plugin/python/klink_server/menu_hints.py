"""
klink menu-hint listener.

Why this exists
---------------
klink's macro recorder operates on STATE DIFFS: it snapshots the
layout, waits for a change signal, re-snapshots and emits the delta
as shape.insert / shape.delete / instance.insert calls. That's
forensically complete - every geometric change is captured - but the
generated script has no record of the user's INTENT. A Boolean Union,
a Flatten, a Rotate-then-Move, and a manual delete-plus-redraw ALL
produce the same shape of diff (N removed + M added), so a downstream
consumer (an LLM, a human reviewer) can't tell them apart.

This module closes that gap WITHOUT changing the diff-based recording.
We subscribe to every menu Action's `on_triggered` event and cache
the most recently invoked command path; the recorder then tags the
next diff burst with a `# user command: ...` comment line.

Per the KLayout docs (Events And Callbacks, AbstractMenu) the
`action.on_triggered += callback` idiom APPENDS a listener - it does
NOT replace the menu's existing handler. Multiple listeners coexist.
So the menu behavior is untouched; we're purely observing.

Public API
----------
* install_once()     - walks the whole menu tree and hooks every leaf
                        Action. Safe to call repeatedly. Idempotent.
* consume_hint(max_age_s=2.0)
                       - pops the most recent hint if it fired within
                         the given window. Returns dict or None.

Thread safety: there's a module-level lock. Menu callbacks land on
KLayout's main (Qt) thread; recorder ingest runs on the same thread,
so in practice there's no real contention, but the lock keeps the
module safe even if called from an RPC worker thread.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

try:
    import pya  # type: ignore
except ImportError:  # pragma: no cover - klayout-headless test env
    pya = None  # type: ignore


_lock = threading.Lock()
_last_path: Optional[str] = None
_last_title: Optional[str] = None
_last_ts: float = 0.0
_installed: bool = False
_hooked_count: int = 0

# Keep strong refs to every callback we attach. KLayout's `on_triggered
# += cb` may only hold a weak-like ref on the Python side, in which
# case a garbage-collected closure would silently stop firing. We pin
# all callbacks here for the lifetime of the process.
_callbacks: list = []

# Menu paths we deliberately SKIP hooking. View zoom/scroll/redraw fire
# dozens of times a second from mouse wheel scrolling and would flood
# the hint cache with noise that has nothing to do with any layout
# mutation. "file_menu" is likewise boring: open/save/close don't
# correspond to recordable shape events (or if they do, the comment
# doesn't add value).
_BORING_SUBSTRINGS = (
    "zoom",
    "scroll",
    "redraw",
    "refresh",
    "cm_select_all",  # harmless but noisy
)
_BORING_PREFIXES = (
    "file_menu.",
    "help_menu.",
    "@hidden.",
)


def _is_boring(path: str) -> bool:
    p = path.lower()
    for pref in _BORING_PREFIXES:
        if p.startswith(pref):
            return True
    for sub in _BORING_SUBSTRINGS:
        if sub in p:
            return True
    return False


def _record(path: str, title: str) -> None:
    global _last_path, _last_title, _last_ts
    with _lock:
        _last_path = path
        _last_title = title
        _last_ts = time.monotonic()


def consume_hint(max_age_s: float = 2.0) -> Optional[dict]:
    """Return the most recent command hint if it fired within max_age_s
    seconds and reset the cache so the next call returns None.

    Consume-once semantics matter: a single menu click typically
    produces one diff burst which fans out into several recorder
    handlers (cells / shapes / instances). We want ONE annotation for
    the whole burst, not three. Whichever handler gets here first wins
    and annotates; the rest see None and emit plain actions.
    """
    global _last_path, _last_title, _last_ts
    with _lock:
        if _last_path is None:
            return None
        age = time.monotonic() - _last_ts
        if age > max_age_s:
            # Stale: clear and return None. Prevents a menu click from
            # 30s ago mis-attributing a freshly-drawn box as "Boolean
            # Union".
            _last_path = None
            _last_title = None
            return None
        hint = {"path": _last_path, "title": _last_title, "age_s": age}
        _last_path = None
        _last_title = None
        return hint


def _safe_is_menu(menu, path: str) -> bool:
    """pya.AbstractMenu.is_menu (Ruby `is_menu?`) may vary across
    KLayout bindings. We try both names."""
    for nm in ("is_menu", "is_menu?"):
        fn = getattr(menu, nm, None)
        if fn is None:
            continue
        try:
            return bool(fn(path))
        except Exception:
            continue
    return False


def _safe_is_separator(menu, path: str) -> bool:
    for nm in ("is_separator", "is_separator?"):
        fn = getattr(menu, nm, None)
        if fn is None:
            continue
        try:
            return bool(fn(path))
        except Exception:
            continue
    return False


def _walk_and_hook(menu, path: str) -> int:
    """Recursively hook on_triggered for every leaf Action under path.
    Returns count of hooks installed.

    Robustness: every pya call is wrapped in try/except. If a single
    branch of the menu tree blows up (unknown node kind in a minor
    KLayout release), we keep walking the other branches instead of
    aborting the whole install."""
    hooked = 0
    try:
        items = menu.items(path)
    except Exception:
        return 0

    for child in items:
        try:
            if _safe_is_separator(menu, child):
                continue
            if _safe_is_menu(menu, child):
                hooked += _walk_and_hook(menu, child)
                continue
        except Exception:
            continue

        if _is_boring(child):
            continue

        try:
            action = menu.action(child)
        except Exception:
            action = None
        if action is None:
            continue
        if not hasattr(action, "on_triggered"):
            continue

        title = ""
        try:
            raw = getattr(action, "title", "")
            title = str(raw) if raw is not None else ""
        except Exception:
            title = ""

        # Per-leaf closure: bind path/title by default-argument so each
        # callback carries its own identity (no late-binding bug).
        def _make_cb(p: str = child, t: str = title):
            def _cb():
                try:
                    _record(p, t)
                except Exception:
                    pass
            return _cb

        cb = _make_cb()
        try:
            action.on_triggered += cb
            _callbacks.append(cb)
            hooked += 1
        except Exception:
            # Some actions apparently don't accept the += operator on
            # this build. Silent skip; we'll just miss annotations for
            # those paths.
            pass

    return hooked


def install_once() -> bool:
    """Walk the entire menu tree and attach a hint-recording listener
    to every leaf Action. Idempotent: subsequent calls are no-ops.

    Returns True if this call did the install, False if it was already
    installed or the install failed (no MainWindow, pya unavailable).
    """
    global _installed, _hooked_count
    with _lock:
        if _installed:
            return False

    if pya is None:
        return False
    try:
        app = pya.Application.instance()
        if app is None:
            return False
        mw = app.main_window()
        if mw is None:
            return False
        menu = mw.menu()
        if menu is None:
            return False
    except Exception as e:
        print(f"[klink.menu_hints] install: main_window/menu unavailable: {e}")
        return False

    try:
        n = _walk_and_hook(menu, "")
    except Exception as e:
        print(f"[klink.menu_hints] walk failed: {e}")
        return False

    with _lock:
        _installed = True
        _hooked_count = n
    print(f"[klink.menu_hints] installed {n} action hook(s)")
    return True


def stats() -> dict:
    """Diagnostic for tests / debug prints."""
    with _lock:
        return {
            "installed": _installed,
            "hooked_count": _hooked_count,
            "pending_hint": _last_path,
            "pending_hint_age_s": (time.monotonic() - _last_ts) if _last_path else None,
        }

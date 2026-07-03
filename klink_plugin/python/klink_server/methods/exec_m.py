"""
exec.python — run arbitrary Python code in KLayout's main thread (M4).

This is the deliberate "escape hatch" for clients (and LLM agents in
particular): when the typed RPC catalogue isn't sufficient, drop into
raw pya and use the full API. Examples:

    # one-liner expression
    view.box().to_s()

    # multi-line with state persistence
    lib = pya.Library.library_by_name('Basic')
    decl = lib.layout().pcell_declaration('CIRCLE')
    decl.name()

Design points
-------------
* **No sandboxing.** Running code here is equivalent to running a
  KLayout macro - full pya access, full filesystem access, etc. This
  is intentional; klink only listens on 127.0.0.1.
* **Per-connection namespace.** Each `ConnState` lazily gets its own
  dict used as both globals and locals. Variables set in one call
  are visible to the next call on the SAME connection. A different
  client gets a fresh namespace and cannot see or trample yours.
  Closing the connection drops the namespace.
* **Jupyter-style return value.** If the last statement is an
  expression, its value is returned as `return_value`; otherwise
  `had_result=False`. Detected via ast.parse.
* **stdout/stderr capture.** We redirect sys.stdout/sys.stderr to
  StringIO buffers around the exec. Cap at configurable byte limits
  so a runaway `for i in range(1e9): print(i)` doesn't blow up the
  JSON response. Output still prints to KLayout's console in real
  time would require C-level tee-ing, which is overkill for M4.
* **Exceptions are NOT fatal RPC errors.** A user whose code raises
  gets `ok=true` with `result.exception` populated (type, message,
  traceback) plus whatever stdout/stderr was captured before the
  raise. Only genuinely malformed requests (missing `code`, code
  too big, etc.) return `ok=false`.
* **Cellview protection around dangerous pya.** Typed RPCs protect their
  own dangerous paths, but arbitrary pya can still clear/replace/delete
  the displayed cell. For snippets that look like they may mutate layout
  ownership (`delete_cell`, `clear`, `assign`, `read`), exec.python first
  detaches the active cellview from the displayed cell, then checks and
  rebinds it to a safe top cell after execution.
* **Runs synchronously in the Qt main thread.** Same as every other
  klink RPC - no threads, no process_events() yielding. Users who
  want long-running work should wait for M5 (job manager).
"""

from __future__ import annotations

import ast
import io
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout

import pya

from ..errors import ErrorCode, RpcError
from ..registry import method
# NOTE: `..server` is imported lazily inside `_get_conn` because this
# module is loaded from `server.py` during startup (via `from . import
# methods`), at which point `server.instance` doesn't exist yet. A
# top-level import here crashes the whole plugin (port 8765 never
# opens). Same pattern as meta_m.py / events_m.py.

# Hard upper bound on user code size, in bytes. 1 MiB is comfortably
# more than any sane single-shot snippet (larger workloads should go
# through a file loaded by `exec.python` itself).
MAX_CODE_BYTES = 1 * 1024 * 1024

# Default stdout/stderr capture ceilings, in bytes. Overridable per
# call via params. Truncation is flagged in the response.
DEFAULT_STDOUT_LIMIT = 64 * 1024
DEFAULT_STDERR_LIMIT = 64 * 1024

# Cap for free-form repr of a non-JSON-safe return value.
REPR_LIMIT = 16 * 1024


def _json_safe(value, *, depth: int = 0, max_depth: int = 6):
    """Convert `value` into something json.dumps can handle.

    Returns either a json-safe Python value or a marker dict
    ``{"__repr__": ..., "type": ...}`` for values we cannot round-trip.
    Keeps the shape LLM-friendly: basic scalars pass through, pya
    geometry is stringified via `.to_s()`, containers recurse.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if depth >= max_depth:
        return {"__repr__": _clip(repr(value), REPR_LIMIT),
                "type": type(value).__name__}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v, depth=depth + 1, max_depth=max_depth) for v in value]
    if isinstance(value, dict):
        out: dict = {}
        for k, v in value.items():
            key = k if isinstance(k, (str, int, float, bool)) else repr(k)
            out[str(key)] = _json_safe(v, depth=depth + 1, max_depth=max_depth)
        return out
    # pya.Box / pya.Polygon / etc. usually have a meaningful .to_s()
    try:
        s = value.to_s()
        if isinstance(s, str):
            return {"__repr__": _clip(s, REPR_LIMIT),
                    "type": type(value).__name__}
    except Exception:
        pass
    return {"__repr__": _clip(repr(value), REPR_LIMIT),
            "type": type(value).__name__}


def _clip(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n...[truncated, full len={len(s)}]"


def _get_conn(ctx):
    """Resolve the ConnState for this request so we can reach its
    per-connection exec namespace. Returns None if the server isn't
    up or the connection has already been cleaned up."""
    from ..server import instance as _srv_instance  # lazy: see top-of-file note
    srv = _srv_instance()
    if srv is None:
        return None
    return srv.conns.get(ctx.conn_id)


def _build_base_namespace() -> dict:
    """Initial globals the user code sees. We pre-bind the handful of
    things every klink/LLM script ends up importing anyway, so simple
    one-liners like `view.box().to_s()` just work."""
    ns: dict = {
        "__name__": "klink.exec",
        "__builtins__": __builtins__,
        "pya": pya,
    }
    try:
        mw = pya.Application.instance().main_window()
    except Exception:
        mw = None
    ns["mw"] = mw
    view = None
    layout = None
    if mw is not None:
        try:
            from .cell_m import _active_layout
            view, _cv, layout = _active_layout()
        except Exception:
            try:
                view = mw.current_view()
            except Exception:
                view = None
            try:
                cv = view.active_cellview() if view is not None else None
                layout = cv.layout() if cv is not None and cv.is_valid() else None
            except Exception:
                layout = None
    ns["view"] = view
    ns["layout"] = layout
    return ns


def _safe_top_cell(layout):
    """Return a safe cell to show for `layout`, creating TOP if needed."""
    if layout is None:
        return None
    try:
        tops = list(layout.top_cells())
    except Exception:
        tops = []
    if tops:
        return tops[0]
    try:
        return layout.create_cell("TOP")
    except Exception:
        return None


_DANGEROUS_CELLVIEW_TOKENS = (
    "delete_cell",
    "delete_cells",
    ".clear(",
    ".assign(",
    ".read(",
)


def _looks_cellview_dangerous(code: str) -> bool:
    return any(tok in code for tok in _DANGEROUS_CELLVIEW_TOKENS)


def _pre_exec_protect_cellview(ns: dict, code: str, enabled: bool) -> list[dict]:
    """Detach the active cellview before high-risk arbitrary pya.

    Post-exec repair is too late for deleting the currently displayed cell:
    KLayout can crash as soon as Qt observes a dangling cell pointer. The
    least invasive mitigation is to detach only when the snippet contains
    known high-risk layout ownership operations.
    """
    actions: list[dict] = []
    if not enabled:
        return actions
    if not _looks_cellview_dangerous(code):
        return actions

    try:
        mw = pya.Application.instance().main_window()
        view = mw.current_view() if mw is not None else None
        cv = view.active_cellview() if view is not None else None
        if cv is None or not cv.is_valid():
            return actions
        layout = cv.layout()
    except Exception as e:
        actions.append({"status": "skipped", "reason": f"pre-protect lookup failed: {e}"})
        return actions

    old_name = None
    old_index = None
    try:
        cell = cv.cell
    except Exception:
        cell = None

    if cell is None:
        ns["mw"] = mw
        ns["view"] = view
        ns["layout"] = layout
        return actions

    try:
        old_name = cell.name
    except Exception:
        pass
    try:
        old_index = int(cell.cell_index())
    except Exception:
        pass

    # Preserve explicit handles for advanced snippets that need to know what
    # was visible before protection. The normal convenience globals remain
    # `mw`, `view`, and `layout`.
    ns["mw"] = mw
    ns["view"] = view
    ns["layout"] = layout
    ns["active_cell_before_exec"] = cell
    ns["active_cell_name_before_exec"] = old_name
    ns["active_cell_index_before_exec"] = old_index

    try:
        cv.cell = None
        try:
            pya.Application.instance().process_events()
        except Exception:
            pass
        actions.append({
            "status": "detached",
            "reason": "snippet contains high-risk layout ownership operation",
            "old_cell": old_name,
            "old_cell_index": old_index,
        })
    except Exception as e:
        actions.append({
            "status": "failed",
            "reason": f"could not detach active cellview: {e}",
            "old_cell": old_name,
            "old_cell_index": old_index,
        })
    return actions


def _post_exec_repair_cellview(ns: dict) -> list[dict]:
    """Repair obvious active-cellview damage caused by arbitrary pya.

    This deliberately stays narrow. It does not roll back user geometry or
    second-guess valid edits; it only makes the currently active cellview point
    at a valid cell if the user's script cleared/replaced/deleted the displayed
    one. That is the class of damage that can otherwise surface later as a Qt
    repaint or follow-up-RPC crash.
    """
    actions: list[dict] = []
    try:
        mw = pya.Application.instance().main_window()
    except Exception as e:
        actions.append({"status": "skipped", "reason": f"main_window unavailable: {e}"})
        return actions

    if mw is None:
        actions.append({"status": "skipped", "reason": "main_window is None"})
        return actions

    try:
        view = mw.current_view()
    except Exception as e:
        actions.append({"status": "skipped", "reason": f"current_view unavailable: {e}"})
        return actions

    # Keep the convenience globals fresh even when the user switched tabs or
    # loaded/replaced a layout inside exec.python.
    ns["mw"] = mw
    ns["view"] = view

    if view is None:
        ns["layout"] = None
        actions.append({"status": "skipped", "reason": "no current view"})
        return actions

    try:
        cv = view.active_cellview()
    except Exception as e:
        ns["layout"] = None
        actions.append({"status": "skipped", "reason": f"active_cellview unavailable: {e}"})
        return actions

    try:
        if cv is None or not cv.is_valid():
            ns["layout"] = None
            actions.append({"status": "skipped", "reason": "active cellview is invalid"})
            return actions
    except Exception as e:
        ns["layout"] = None
        actions.append({"status": "skipped", "reason": f"cellview validity check failed: {e}"})
        return actions

    try:
        layout = cv.layout()
    except Exception as e:
        ns["layout"] = None
        actions.append({"status": "skipped", "reason": f"cellview layout unavailable: {e}"})
        return actions

    ns["layout"] = layout

    need_rebind = False
    reason = None
    current_name = None
    current_index = None

    try:
        cell = cv.cell
    except Exception as e:
        cell = None
        need_rebind = True
        reason = f"cv.cell access failed: {e}"

    if not need_rebind:
        if cell is None:
            need_rebind = True
            reason = "cv.cell is None"
        else:
            try:
                current_name = cell.name
            except Exception:
                current_name = None
            try:
                current_index = int(cell.cell_index())
            except Exception:
                current_index = None
            if current_index is not None:
                try:
                    if layout.cell(current_index) is None:
                        need_rebind = True
                        reason = f"shown cell index {current_index} no longer exists"
                except Exception as e:
                    need_rebind = True
                    reason = f"shown cell lookup failed: {e}"

    if not need_rebind:
        return actions

    replacement = _safe_top_cell(layout)
    if replacement is None:
        actions.append({
            "status": "failed",
            "reason": reason or "active cell is invalid",
            "action": "could not find or create replacement cell",
        })
        return actions

    try:
        cv.cell = replacement
        try:
            pya.Application.instance().process_events()
        except Exception:
            pass
        actions.append({
            "status": "repaired",
            "reason": reason or "active cell was invalid",
            "action": "rebound active cellview to a valid top cell",
            "old_cell": current_name,
            "new_cell": replacement.name,
        })
    except Exception as e:
        actions.append({
            "status": "failed",
            "reason": reason or "active cell was invalid",
            "action": f"rebind failed: {e}",
        })
    return actions


def _split_last_expr(code: str):
    """Jupyter-style split: if the last top-level statement is an
    expression, peel it off so we can eval() it separately and capture
    its value. Otherwise treat the whole code as pure statements.

    Returns (prelude_module, last_expr_or_None). Either piece can be
    None when there's nothing to execute.
    """
    tree = ast.parse(code, filename="<klink.exec>", mode="exec")
    if not tree.body:
        return None, None
    last = tree.body[-1]
    if isinstance(last, ast.Expr):
        prelude = ast.Module(body=tree.body[:-1], type_ignores=[])
        expr = ast.Expression(body=last.value)
        return prelude, expr
    return tree, None


@method(
    "exec.python",
    description=(
        "Escape hatch: run arbitrary Python code in KLayout's Qt main "
        "thread. Full pya access, full filesystem access - equivalent to "
        "running a macro from the IDE. Pre-bound globals: pya, mw (main "
        "window), view (current LayoutView), layout (active Layout).\n"
        "\n"
        "State persists across calls ON THE SAME CONNECTION. Pass "
        "`reset=true` to wipe the namespace first. stdout/stderr are "
        "captured and returned as strings. If the last top-level "
        "statement is an expression, its value comes back as "
        "`return_value` (Jupyter-style); otherwise had_result=false.\n"
        "\n"
        "Exceptions in user code do NOT fail the RPC - they come back "
        "inside result.exception along with any stdout/stderr captured "
        "before the raise. Only malformed requests (missing `code`, "
        "oversized, syntax error) return ok=false. Callers writing LLM "
        "feedback loops should branch on result.exception."
    ),
    params_schema={
        "type": "object",
        "required": ["code"],
        "properties": {
            "code": {
                "type": "string",
                "description": "Python source to exec. Multi-statement OK; if the last line is an expression its value is returned.",
            },
            "reset": {
                "type": "boolean",
                "description": "Clear this connection's namespace before running. Default false.",
            },
            "stdout_limit": {
                "type": "integer",
                "description": f"Max bytes of stdout to return (truncated if exceeded). Default {DEFAULT_STDOUT_LIMIT}.",
            },
            "stderr_limit": {
                "type": "integer",
                "description": f"Max bytes of stderr to return. Default {DEFAULT_STDERR_LIMIT}.",
            },
            "result_mode": {
                "type": "string",
                "enum": ["auto", "repr", "none"],
                "description": "`auto` (default) tries JSON-safe, falls back to repr; `repr` always forces repr; `none` discards the last expression's value.",
            },
            "protect_cellview": {
                "type": "boolean",
                "description": "When true (default), snippets containing high-risk layout ownership operations detach the active cellview before execution and repair it afterward.",
            },
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "stdout": {"type": "string"},
            "stderr": {"type": "string"},
            "stdout_truncated": {"type": "boolean"},
            "stderr_truncated": {"type": "boolean"},
            "return_value": {},
            "return_value_repr": {"type": ["string", "null"]},
            "return_value_type": {"type": ["string", "null"]},
            "had_result": {"type": "boolean"},
            "exception": {
                "type": ["object", "null"],
                "properties": {
                    "type": {"type": "string"},
                    "message": {"type": "string"},
                    "traceback": {"type": "string"},
                },
            },
            "wall_ms": {"type": "number"},
            "namespace_size": {"type": "integer"},
            "post_exec_repair": {
                "type": "array",
                "description": "Any active cellview repair actions applied after arbitrary pya execution.",
            },
            "pre_exec_protection": {
                "type": "array",
                "description": "Any active cellview protection actions applied before arbitrary pya execution.",
            },
        },
    },
    mutates=True,
    tags=["exec"],
)
def exec_python(params, ctx):
    code = params.get("code")
    if not isinstance(code, str):
        raise RpcError(ErrorCode.BAD_PARAMS, "`code` must be a string")
    if len(code.encode("utf-8", errors="replace")) > MAX_CODE_BYTES:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            f"code exceeds {MAX_CODE_BYTES}-byte limit",
            hint="split into multiple calls or load from a file inside the exec",
        )

    result_mode = params.get("result_mode", "auto")
    if result_mode not in ("auto", "repr", "none"):
        raise RpcError(ErrorCode.BAD_PARAMS,
                       "result_mode must be one of: auto, repr, none")

    try:
        stdout_limit = int(params.get("stdout_limit", DEFAULT_STDOUT_LIMIT))
        stderr_limit = int(params.get("stderr_limit", DEFAULT_STDERR_LIMIT))
    except Exception:
        raise RpcError(ErrorCode.BAD_PARAMS,
                       "stdout_limit / stderr_limit must be integers")

    conn = _get_conn(ctx)
    if conn is None:
        # Shouldn't happen in practice: dispatcher only runs while
        # the conn exists. Safety net so we never crash.
        raise RpcError(ErrorCode.INTERNAL,
                       "connection state not found for this request")

    if bool(params.get("reset")) or conn.exec_namespace is None:
        conn.exec_namespace = _build_base_namespace()
    ns = conn.exec_namespace

    # Parse once up front so we can cleanly separate syntax errors
    # (surfaces as ok=false BAD_PARAMS, since the request itself is
    # malformed) from runtime errors (captured into result.exception).
    #
    # When result_mode is "none" we skip the Jupyter split: every line
    # must run, including expressions whose side-effects matter (e.g.
    # cell.insert(...)).  The split is only meaningful when the caller
    # wants to capture a return value (modes "auto" / "repr").
    if result_mode == "none":
        try:
            prelude = ast.parse(code, filename="<klink.exec>", mode="exec")
        except SyntaxError as e:
            raise RpcError(
                ErrorCode.BAD_PARAMS,
                f"SyntaxError: {e.msg} (line {e.lineno})",
                hint="compile failed before execution; nothing ran and no state changed",
                data={"lineno": e.lineno, "offset": e.offset, "line": e.text},
            )
        last_expr = None
    else:
        try:
            prelude, last_expr = _split_last_expr(code)
        except SyntaxError as e:
            raise RpcError(
                ErrorCode.BAD_PARAMS,
                f"SyntaxError: {e.msg} (line {e.lineno})",
                hint="compile failed before execution; nothing ran and no state changed",
                data={"lineno": e.lineno, "offset": e.offset, "line": e.text},
            )

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    exception: dict | None = None
    return_value = None
    had_result = False
    pre_exec_protection = _pre_exec_protect_cellview(
        ns, code, bool(params.get("protect_cellview", True))
    )

    t0 = time.monotonic()
    # Defensive: some KLayout builds have sys.stderr == None, which
    # redirect_stderr can still cope with but some user code might not.
    # We don't touch it here; just run and capture.
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            if prelude is not None and prelude.body:
                exec(compile(prelude, "<klink.exec>", "exec"), ns, ns)
            if last_expr is not None and result_mode != "none":
                return_value = eval(
                    compile(last_expr, "<klink.exec>", "eval"), ns, ns
                )
                had_result = True
    except BaseException as e:
        # Includes SystemExit / KeyboardInterrupt on purpose: we want
        # the RPC to survive even if user code tries to exit.
        exception = {
            "type": type(e).__name__,
            "message": str(e),
            "traceback": traceback.format_exc(),
        }
    wall_ms = (time.monotonic() - t0) * 1000.0

    post_exec_repair = _post_exec_repair_cellview(ns)

    try:
        from ..server import instance as _srv_instance
        srv = _srv_instance()
        hub = getattr(srv, "signals", None) if srv is not None else None
        if hub is not None:
            hub._schedule_diff(source="exec.python")
    except Exception:
        pass

    stdout_raw = stdout_buf.getvalue()
    stderr_raw = stderr_buf.getvalue()
    stdout_clipped = stdout_raw[:stdout_limit]
    stderr_clipped = stderr_raw[:stderr_limit]

    # Serialise the return value. We hand back BOTH a JSON-safe form
    # (when possible) AND a string repr + type name, so clients that
    # can't tell the two apart get a consistent shape.
    rv_json = None
    rv_repr = None
    rv_type = None
    if had_result:
        rv_type = type(return_value).__name__
        try:
            rv_repr = _clip(repr(return_value), REPR_LIMIT)
        except Exception:
            rv_repr = f"<repr failed for {rv_type}>"
        if result_mode == "repr":
            rv_json = {"__repr__": rv_repr, "type": rv_type}
        else:
            rv_json = _json_safe(return_value)

    return {
        "stdout": stdout_clipped,
        "stderr": stderr_clipped,
        "stdout_truncated": len(stdout_raw) > stdout_limit,
        "stderr_truncated": len(stderr_raw) > stderr_limit,
        "return_value": rv_json,
        "return_value_repr": rv_repr,
        "return_value_type": rv_type,
        "had_result": had_result,
        "exception": exception,
        "wall_ms": wall_ms,
        "namespace_size": len(ns),
        "pre_exec_protection": pre_exec_protection,
        "post_exec_repair": post_exec_repair,
    }


@method(
    "exec.reset",
    description=(
        "Clear the per-connection Python namespace used by exec.python. "
        "Equivalent to calling exec.python with reset=true and no code. "
        "Useful when a client wants a fresh sandbox without running "
        "anything else."
    ),
    params_schema={"type": "object"},
    returns_schema={
        "type": "object",
        "properties": {
            "cleared": {"type": "boolean"},
            "previous_size": {"type": "integer"},
        },
    },
    mutates=True,
    tags=["exec"],
)
def exec_reset(params, ctx):
    conn = _get_conn(ctx)
    if conn is None:
        raise RpcError(ErrorCode.INTERNAL,
                       "connection state not found for this request")
    prev = len(conn.exec_namespace) if conn.exec_namespace is not None else 0
    conn.exec_namespace = None
    return {"cleared": True, "previous_size": prev}

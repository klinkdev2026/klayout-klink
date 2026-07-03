"""
drc.run — run DRC DSL scripts in KLayout's Ruby DRC engine (P3).

This is the DRC escape hatch, analogous to exec.python for Python code.
Accepts DRC DSL source (Ruby-like) and executes it via pya.Macro.
The script runs synchronously on the Qt main thread.

Design points
-------------
* **Ruby DSL, not Python.** DRC scripts use KLayout's Ruby-based DSL.
  They run inside KLayout's integrated Ruby interpreter via pya.Macro.
* **Temp file.** The DSL code is written to a temporary .drc file
  because pya.Macro requires a file path to determine the interpreter.
* **Variable injection.** Optional parameters (input_layout, output_rdb,
  top_cell) are passed as Ruby globals ($input_layout, etc.) so the
  script can reference them without hardcoding paths.
* **RDB parsing.** If output_rdb is specified and the script generates
  it, we read it back via pya.ReportDatabase and return a summary.
* **Exceptions are NOT fatal RPC errors.** Same as exec.python — a DRC
  script that raises (syntax error, rule failure, etc.) comes back as
  `ok=true` with `result.exception` populated. Only malformed requests
  return `ok=false`.
* **No per-connection state.** Unlike exec.python, each drc.run call is
  independent — Ruby interpreter instances don't persist across calls.
"""

from __future__ import annotations

import io
import os
import tempfile
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout

import pya

from ..errors import ErrorCode, RpcError
from ..registry import method

MAX_CODE_BYTES = 1 * 1024 * 1024

DEFAULT_STDOUT_LIMIT = 64 * 1024
DEFAULT_STDERR_LIMIT = 64 * 1024


def _clip(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n...[truncated, full len={len(s)}]"


def _read_rdb_summary(rdb_path: str) -> dict:
    """Read a .lyrdb file and return a violation summary.

    Returns {total_items, categories: [{name, count}]} on success,
    or {error: ...} if the RDB can't be parsed.
    """
    try:
        rdb = pya.ReportDatabase("_klink_drc_result")
        rdb.load(rdb_path)
    except Exception as e:
        return {"error": f"Cannot load RDB: {e}"}

    total = 0
    categories = []
    try:
        for cat in rdb.each_category():
            count = 0
            try:
                for _ in cat.each_item():
                    count += 1
            except Exception:
                pass
            total += count
            cat_name = ""
            cat_desc = ""
            try:
                cat_name = cat.name()
            except Exception:
                pass
            try:
                cat_desc = cat.description()
            except Exception:
                pass
            categories.append({
                "name": cat_name,
                "description": cat_desc,
                "count": count,
            })
    except Exception as e:
        return {"error": f"Cannot iterate categories: {e}", "total_items": 0, "categories": []}

    return {
        "total_items": total,
        "categories": categories,
    }


def _read_rdb_full(rdb_path: str, limit: int = 200) -> dict:
    """Read an RDB file and return detailed item info.

    Returns up to `limit` items with cell name, category, coordinates,
    and violation text.
    """
    summary = _read_rdb_summary(rdb_path)
    if "error" in summary:
        return summary

    items = []
    try:
        rdb = pya.ReportDatabase("_klink_drc_full")
        rdb.load(rdb_path)
        for cat in rdb.each_category():
            cat_name = ""
            try:
                cat_name = cat.name()
            except Exception:
                pass
            for item in cat.each_item():
                if len(items) >= limit:
                    break
                item_info = {"category": cat_name}
                try:
                    item_info["cell"] = item.cell().name()
                except Exception:
                    pass
                try:
                    item_info["text"] = item.value().to_s()
                except Exception:
                    pass
                try:
                    item_info["multiplicity"] = item.multiplicity()
                except Exception:
                    pass
                items.append(item_info)
            if len(items) >= limit:
                break
    except Exception as e:
        return {**summary, "items_error": str(e)}

    return {**summary, "items": items, "items_truncated": len(items) >= limit}


@method(
    "drc.run",
    description=(
        "Escape hatch: run arbitrary DRC DSL script code in KLayout's "
        "integrated Ruby DRC engine. Accepts DRC DSL source (Ruby-like "
        "syntax with source()/input()/report()/etc.) and executes it.\n"
        "\n"
        "If the script includes source() it runs in standalone mode "
        "(against the specified file). If source() is omitted it runs "
        "in interactive mode against the currently loaded layout.\n"
        "\n"
        "Optional variables ($input_layout, $output_rdb, $topcell) are "
        "injected into the Ruby interpreter so the script can reference "
        "them without hardcoding paths.\n"
        "\n"
        "stdout/stderr are captured and returned as strings. If "
        "output_rdb is specified and the script generates it, the RDB "
        "is parsed and a violation summary is returned.\n"
        "\n"
        "Exceptions in DRC scripts do NOT fail the RPC - they come back "
        "inside result.exception with any stdout/stderr captured before "
        "the error. Only malformed requests (missing code, oversized) "
        "return ok=false."
    ),
    params_schema={
        "type": "object",
        "required": ["code"],
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "DRC DSL source code to execute. Full DRC script with "
                    "source()/input()/report() etc. Supports the complete "
                    "KLayout DRC DSL (Ruby-based)."
                ),
            },
            "input_layout": {
                "type": "string",
                "description": (
                    "Optional path to input GDS/OASIS file. Injected as "
                    "Ruby variable $input_layout. The script can use "
                    "source($input_layout) to reference it."
                ),
            },
            "output_rdb": {
                "type": "string",
                "description": (
                    "Optional path for the output report database (.lyrdb) "
                    "file. Injected as Ruby variable $output_rdb. After "
                    "execution, if this file exists it is read back and "
                    "returned as rdb_summary."
                ),
            },
            "top_cell": {
                "type": "string",
                "description": (
                    "Optional top cell name override. Injected as Ruby "
                    "variable $topcell. The script can use "
                    "source($input_layout, $topcell) to reference it."
                ),
            },
            "result_mode": {
                "type": "string",
                "enum": ["summary", "full", "rdb_path_only"],
                "default": "summary",
                "description": (
                    "'summary' returns violation counts by category; "
                    "'full' also returns individual item details (up to "
                    "200 items); 'rdb_path_only' skips RDB parsing."
                ),
            },
            "stdout_limit": {
                "type": "integer",
                "description": f"Max bytes of stdout. Default {DEFAULT_STDOUT_LIMIT}.",
            },
            "stderr_limit": {
                "type": "integer",
                "description": f"Max bytes of stderr. Default {DEFAULT_STDERR_LIMIT}.",
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
            "rdb_file": {"type": ["string", "null"]},
            "rdb_summary": {
                "type": ["object", "null"],
                "properties": {
                    "total_items": {"type": "integer"},
                    "categories": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                                "count": {"type": "integer"},
                            },
                        },
                    },
                },
            },
            "exception": {
                "type": ["object", "null"],
                "properties": {
                    "type": {"type": "string"},
                    "message": {"type": "string"},
                    "traceback": {"type": "string"},
                },
            },
            "wall_ms": {"type": "number"},
        },
    },
    mutates=True,
    long_running=True,
    tags=["drc"],
)
def drc_run(params, ctx):
    code = params.get("code")
    if not isinstance(code, str):
        raise RpcError(ErrorCode.BAD_PARAMS, "`code` must be a string")
    if len(code.encode("utf-8", errors="replace")) > MAX_CODE_BYTES:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            f"code exceeds {MAX_CODE_BYTES}-byte limit",
            hint="split the DRC script into smaller pieces or load via include() from the script",
        )

    result_mode = params.get("result_mode", "summary")
    if result_mode not in ("summary", "full", "rdb_path_only"):
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "result_mode must be one of: summary, full, rdb_path_only",
        )

    input_layout = params.get("input_layout")
    output_rdb = params.get("output_rdb")
    top_cell = params.get("top_cell")

    try:
        stdout_limit = int(params.get("stdout_limit", DEFAULT_STDOUT_LIMIT))
        stderr_limit = int(params.get("stderr_limit", DEFAULT_STDERR_LIMIT))
    except Exception:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "stdout_limit / stderr_limit must be integers",
        )

    # Check pya.Macro availability (requires KLayout >= 0.27.5).
    if not hasattr(pya, "Macro"):
        raise RpcError(
            ErrorCode.INTERNAL,
            "pya.Macro is not available in this KLayout version",
            hint="DRC script execution requires KLayout >= 0.27.5",
        )

    # Write DRC code to a temp .drc file so pya.Macro can select the
    # right interpreter (Ruby DRC DSL).
    fd, temp_path = tempfile.mkstemp(suffix=".drc", prefix="klink_drc_")

    # Initialise all variables that the code AFTER the try/finally block
    # touches, so a failure inside the try (e.g. file write error) doesn't
    # cause an UnboundLocalError in the return-value assembly below.
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    exception = None
    wall_ms = 0.0

    try:
        os.close(fd)

        # Replace injected variables in the source code BEFORE writing.
        # This is simpler and more reliable than pya.Interpreter - the
        # Ruby DRC engine sees string literals just like hardcoded paths.
        if input_layout is not None:
            code = code.replace("$input_layout", repr(input_layout))
        if output_rdb is not None:
            code = code.replace("$output_rdb", repr(output_rdb))
        if top_cell is not None:
            code = code.replace("$topcell", repr(top_cell))

        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(code)

        # Execute synchronously. stdout/stderr capture is Python-level
        # and may not catch all Ruby output, but DRC scripts typically
        # use report() for structured output so this is acceptable.
        t0 = time.monotonic()
        try:
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                macro = pya.Macro(temp_path)
                macro.run()
        except BaseException as e:
            exception = {
                "type": type(e).__name__,
                "message": str(e),
                "traceback": traceback.format_exc(),
            }
        wall_ms = (time.monotonic() - t0) * 1000.0

    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    stdout_raw = stdout_buf.getvalue()
    stderr_raw = stderr_buf.getvalue()

    # Read RDB if the script generated one.
    rdb_file = None
    rdb_summary = None
    if output_rdb is not None and os.path.exists(output_rdb):
        rdb_file = output_rdb
        if result_mode == "full":
            rdb_summary = _read_rdb_full(output_rdb)
        elif result_mode == "summary":
            rdb_summary = _read_rdb_summary(output_rdb)

    return {
        "stdout": _clip(stdout_raw, stdout_limit),
        "stderr": _clip(stderr_raw, stderr_limit),
        "stdout_truncated": len(stdout_raw) > stdout_limit,
        "stderr_truncated": len(stderr_raw) > stderr_limit,
        "rdb_file": rdb_file,
        "rdb_summary": rdb_summary,
        "exception": exception,
        "wall_ms": wall_ms,
    }

"""Deterministic tool RPCs: run an installed tool KLayout-native, list / inspect installed tools."""

from __future__ import annotations

from ..registry import method
from .. import tools


@method(
    "tool.run",
    description=(
        "Run an installed deterministic tool (<registry_root>/tools/<id>/: tool.json + run.py) "
        "inside KLayout on the current view's selection, as ONE undo step. Params resolve as "
        "tool defaults <- preset <- `params`. With interactive=false (default) nothing is asked: a "
        "layer param with a `from` list and one candidate is filled in (result `auto`); otherwise a "
        "param without a value is ERR_TOOL_PARAMS (layer choices in data.candidates). With "
        "interactive=true KLayout opens ONE form with every param pre-filled (layers listed from the "
        "selection/layout, plus `new layer...`); the call blocks while it is open; cancel returns "
        "status 'cancelled'. Errors: ERR_TOOL_NOT_FOUND, "
        "ERR_TOOL_INPUT (e.g. too few shapes selected), ERR_TOOL_PARAMS, ERR_TOOL_RUN (run.py "
        "raised; the edit was rolled back). Install tools with `klink tool install <dir|zip>`."
    ),
    params_schema={
        "type": "object",
        "required": ["id"],
        "properties": {
            "id": {"type": "string", "description": "installed tool id (tool.list)"},
            "preset": {"type": "string", "description": "preset name from tool.get"},
            "params": {"type": "object", "description": "param name -> value; overrides the preset"},
            "interactive": {"type": "boolean", "default": False,
                            "description": "ask the params in one KLayout form"},
        },
    },
    mutates=True,
    tags=["tools"],
)
def tool_run(params, ctx):
    return tools.run_tool(params["id"], preset=params.get("preset"), params=params.get("params"),
                          interactive=bool(params.get("interactive", False)))


@method(
    "tool.list",
    description="List the deterministic tools installed under <registry_root>/tools/ "
                "(id, title, label, presets, problem when unusable).",
    params_schema={"type": "object"},
    tags=["tools"],
)
def tool_list(params, ctx):
    return {"tools": tools.list_tools(), "dir": str(tools.tools_dir())}


@method(
    "tool.get",
    description="Return one installed tool's normalized tool.json (params with defaults, presets, "
                "prompt policy, min_shapes, min_selected) plus its directory and run.py path.",
    params_schema={"type": "object", "required": ["id"],
                   "properties": {"id": {"type": "string"}}},
    tags=["tools"],
)
def tool_get(params, ctx):
    return tools.load_tool(params["id"])

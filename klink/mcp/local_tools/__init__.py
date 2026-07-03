"""Local MCP tool registry + domain handler modules.

These tools live in the external MCP runtime, not inside the KLayout plugin.
KLayout RPC methods are discovered from plugin-side `meta.methods`; local tools
are registered here (via ``@local_tool``) and appended to `tools/list`.

Handlers are functions ``(ctx, arguments)`` where ``ctx`` is the bridge (the
shared state holder); ``ToolRegistry.call_tool`` dispatches them as
``handler(ctx, arguments)``. Domain handler modules live alongside this file
(`session.py`, `interaction.py`, …) and are imported at the bottom so their
``@local_tool`` decorators run on package import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class LocalTool:
    name: str
    description: str
    input_schema: dict
    handler: Callable

    def to_mcp_tool(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


_LOCAL_TOOLS: dict[str, LocalTool] = {}


def local_tool(name: str, description: str, input_schema: dict) -> Callable:
    """Register a local MCP tool.

    The decorated object is stored as the tool's ``handler`` callable with
    signature ``(ctx, arguments)`` (``ctx`` is the bridge instance). This
    decoupling — a stored callable + explicit ``ctx`` instead of
    ``getattr(self, name)`` — lets handlers live in ``local_tools/<domain>.py``
    modules as free functions.
    """
    def _decorate(func: Callable) -> Callable:
        _LOCAL_TOOLS[name] = LocalTool(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=func,
        )
        return func

    return _decorate


def all_local_tools() -> list[LocalTool]:
    return list(_LOCAL_TOOLS.values())


def get_local_tool(name: str) -> LocalTool | None:
    return _LOCAL_TOOLS.get(name)


# ---------------------------------------------------------------------------
# Domain handler modules — imported for their @local_tool registration side
# effects.
# ---------------------------------------------------------------------------
from . import discovery  # noqa: E402,F401
from . import interaction  # noqa: E402,F401
from . import nanodevice  # noqa: E402,F401
from . import photonics  # noqa: E402,F401
from . import routing  # noqa: E402,F401
from . import session  # noqa: E402,F401
from . import structdevice  # noqa: E402,F401

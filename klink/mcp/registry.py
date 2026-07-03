"""Tool listing + dispatch for KLinkMCPBridge.

`list_tools` merges profile-filtered plugin RPCs (from `ctx._tools`) with the
local-tool registry; `call_tool` dispatches local tools through their stored
callable (`handler(ctx, arguments)`) and plugin RPCs over the live client with
timeout selection and connection-error recovery.

Operates on the bridge as `ctx` (shared state holder).
"""

from __future__ import annotations

from ..errors import KLinkError, KLinkServerError, KLinkTransportError
from .catalog import domain_for, domain_tokens
from .local_tools import all_local_tools, get_local_tool
from .profiles import INTENT_PROFILES, normalize_profiles
from .results import _error_result, _json_result

# Always reachable, even under a domain-only profile: the discovery entry point
# and basic connection diagnostics/recovery.
_ALWAYS_ON_LOCAL = {"klink.find_tools", "klink.status", "klink.reconnect"}


_ALWAYS_LONG_RUNNING = {
    "drc.run",
    "exec.python",
    "layout.save_file",
    "layout.show_file",
    "shape.query",
    "view.screenshot",
}


class ToolRegistry:
    def __init__(self, ctx):
        self.ctx = ctx

    # ------------------------------------------------------------------
    # MCP tool listing
    # ------------------------------------------------------------------
    def list_tools(self) -> dict:
        tools = [self.to_mcp_tool(s) for s in self.ctx._tools]
        local = all_local_tools()
        # Local tools are always included for the intent profiles (back-compat).
        # Only when the profile is purely domain selectors (no intent profile)
        # do we also restrict local tools to those domains, so --profile
        # <domain> yields a genuinely focused list. See catalog.py / profiles.py.
        profiles = normalize_profiles(self.ctx._profiles)
        requested_domains = {p for p in profiles if p in set(domain_tokens())}
        if requested_domains and not (set(profiles) & INTENT_PROFILES):
            local = [
                t for t in local
                if domain_for(t.name) in requested_domains or t.name in _ALWAYS_ON_LOCAL
            ]
        tools.extend(tool.to_mcp_tool() for tool in local)
        return {"tools": tools}

    @staticmethod
    def to_mcp_tool(spec: dict) -> dict:
        tool = {
            "name": spec["name"],
            "description": spec.get("description", ""),
            "inputSchema": spec.get("params", {"type": "object"}),
        }
        return tool

    # ------------------------------------------------------------------
    # MCP tool call
    # ------------------------------------------------------------------
    def call_tool(self, name: str, arguments: dict) -> dict:
        ctx = self.ctx
        local = get_local_tool(name)
        if local is not None:
            # handler is a callable taking (ctx, arguments); pass the bridge as
            # ctx. The registry does not couple handlers via getattr(self, name).
            return local.handler(ctx, arguments or {})

        ctx.ensure_connected()
        if ctx._client is None:
            return _error_result("not connected to klink: %s" % (ctx._last_error or "unknown error"))
        try:
            timeout = self.timeout_for(name)
            ctx._last_call = {"name": name, "timeout": timeout}
            result = ctx._client.call(name, arguments, timeout=timeout)
            return _json_result(result)
        except KLinkServerError as e:
            return _error_result(f"{e.code}: {e.message}")
        except KLinkTransportError as e:
            ctx._last_error = str(e)
            ctx.connection.close_client(clear_tools=True)
            return _error_result(str(e))
        except KLinkError as e:
            ctx._last_error = str(e)
            ctx.connection.close_client(clear_tools=True)
            return _error_result(str(e))
        except Exception as e:
            ctx._last_error = str(e)
            ctx.connection.close_client(clear_tools=True)
            return _error_result(str(e))

    def timeout_for(self, name: str) -> float:
        ctx = self.ctx
        spec = ctx._method_specs.get(name) or {}
        if spec.get("long_running") or name in _ALWAYS_LONG_RUNNING:
            return ctx._long_call_timeout
        if name.startswith("drc.") or name.startswith("exec."):
            return ctx._long_call_timeout
        return ctx._call_timeout

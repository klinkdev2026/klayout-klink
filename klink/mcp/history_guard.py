"""Optional local-history preflight for MCP editor writes.

The callback runs before sending a mutation. A recorder exports through its
own ordinary KLinkClient, so it cannot recursively trigger this preflight.
"""
from __future__ import annotations


def attach(ctx, client, specs=None, *, session_id=None):
    session_id = session_id or f"klayout-{ctx._port}"
    resolved_specs = None
    def before_call(method, arguments):
        nonlocal resolved_specs
        if method == "meta.methods":
            return
        if callable(specs):
            if resolved_specs is None:
                resolved_specs = specs()
            method_specs = resolved_specs
        else:
            method_specs = specs if specs is not None else ctx._method_specs
        if not (method_specs.get(method) or {}).get("mutates"):
            return
        if method == "layout.save_file":
            return
        try:
            from vestigraph.agent_client import call, control_path
        except ImportError:
            return
        root = getattr(getattr(ctx, "_sessions", None), "root", None)
        if not control_path(root).is_file():
            return
        result = call("prepare_edit", {"session_id": session_id}, root)
        if result.get("ok") is not True:
            raise RuntimeError("Vestigraph could not retain the current manual edits; the AI write was not sent.")
    client._before_call = before_call

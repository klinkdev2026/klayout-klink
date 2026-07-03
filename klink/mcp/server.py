"""MCP JSON-RPC 2.0 server over stdio."""

from __future__ import annotations

import json
import sys
from typing import Optional

from .bridge import KLinkMCPBridge
from .config import PROTOCOL_VERSION, SERVER_NAME, SERVER_VERSION


class MCPServer:
    def __init__(self, bridge: KLinkMCPBridge):
        self._bridge = bridge

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run(self) -> None:
        self._log(f"{SERVER_NAME} v{SERVER_VERSION} starting on stdin/stdout")
        self._try_connect()

        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as e:
                    self._log(f"invalid JSON on stdin: {e}")
                    continue

                resp = self._dispatch(msg)
                if resp is not None:
                    self._write(resp)
        except KeyboardInterrupt:
            pass
        finally:
            self._bridge.close()
            self._log("shutdown")

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    def _try_connect(self) -> None:
        if self._bridge.ensure_connected():
            self._log(f"connected to klink ({len(self._bridge.list_tools()['tools'])} tools)")
            return
        status = self._bridge.status()
        err = status.get("last_error") or "unknown error"
        self._log(f"klink not available: {err} (serving MCP status tools only)")

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def _dispatch(self, msg: dict) -> Optional[dict]:
        method = msg.get("method")
        req_id = msg.get("id")

        if method == "initialize":
            return self._handle_initialize(req_id, msg.get("params", {}))
        if method == "tools/list":
            return self._handle_list_tools(req_id)
        if method == "tools/call":
            return self._handle_call_tool(req_id, msg.get("params", {}))

        if req_id is not None:
            return _rpc_error(req_id, -32601, f"Method not found: {method}")
        return None

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------
    def _handle_initialize(self, req_id, params: dict) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
                "capabilities": {"tools": {}},
            },
        }

    def _handle_list_tools(self, req_id) -> dict:
        self._try_connect()
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": self._bridge.list_tools(),
        }

    def _handle_call_tool(self, req_id, params: dict) -> dict:
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        result = self._bridge.call_tool(name, arguments)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result,
        }

    # ------------------------------------------------------------------
    # I/O helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _write(msg: dict) -> None:
        data = json.dumps(msg, ensure_ascii=False) + "\n"
        sys.stdout.write(data)
        sys.stdout.flush()

    @staticmethod
    def _log(text: str) -> None:
        print(f"[{SERVER_NAME}] {text}", file=sys.stderr)


def _rpc_error(req_id, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }

"""MCP tool-result envelopes shared by the bridge and its sub-objects.

Extracted so `registry.py` (call_tool) and the local-tool handlers can build
identical result payloads without a circular import through `bridge.py`.
"""

from __future__ import annotations

import json


def _json_result(result: dict) -> dict:
    return {
        "content": [
            {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
        ]
    }


def _error_result(message: str) -> dict:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }

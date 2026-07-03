"""Shared fixtures for unit tests that do not require KLayout."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_registry_root(monkeypatch, tmp_path):
    monkeypatch.setenv("KLINK_REGISTRY_ROOT", str(tmp_path / "registry-default"))


@pytest.fixture
def sample_method_specs():
    """Small meta.methods-like catalogue used by MCP/schema tests."""
    return [
        {
            "name": "meta.ping",
            "description": "Ping the server.",
            "params": {"type": "object"},
            "tags": ["meta", "read"],
        },
        {
            "name": "layout.info",
            "description": "Return active layout information.",
            "params": {"type": "object"},
            "tags": ["layout", "read"],
        },
        {
            "name": "shape.insert_box",
            "description": "Insert a box.",
            "params": {
                "type": "object",
                "required": ["cell"],
                "properties": {"cell": {"type": "string"}},
            },
            "tags": ["shape", "write"],
            "mutates": True,
        },
        {
            "name": "drc.run",
            "description": "Run DRC.",
            "params": {"type": "object", "required": ["code"]},
            "tags": ["drc"],
            "mutates": True,
            "long_running": True,
        },
        {
            "name": "exec.python",
            "description": "Run Python in KLayout.",
            "params": {"type": "object", "required": ["code"]},
            "tags": ["exec"],
            "mutates": True,
        },
        {
            "name": "events.subscribe",
            "description": "Subscribe to events.",
            "params": {"type": "object"},
            "tags": ["events"],
        },
    ]

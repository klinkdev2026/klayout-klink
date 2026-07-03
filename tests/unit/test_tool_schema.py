from __future__ import annotations

import pytest

from klink import KLinkClient


class FakeClient(KLinkClient):
    def __init__(self, method_specs):
        self._method_specs = method_specs

    def methods(self):
        return {"methods": self._method_specs}


def test_generate_openai_tool_schemas(sample_method_specs):
    schemas = FakeClient(sample_method_specs).generate_tool_schemas("openai")

    by_name = {s["function"]["name"]: s for s in schemas}
    assert "layout__info" in by_name
    assert "exec__python" in by_name
    assert by_name["drc__run"]["type"] == "function"
    assert by_name["drc__run"]["function"]["parameters"]["required"] == ["code"]


def test_generate_anthropic_tool_schemas(sample_method_specs):
    schemas = FakeClient(sample_method_specs).generate_tool_schemas("anthropic")

    by_name = {s["name"]: s for s in schemas}
    assert by_name["shape__insert_box"]["input_schema"]["required"] == ["cell"]
    assert by_name["meta__ping"]["description"] == "Ping the server."


def test_generate_raw_tool_schemas_returns_catalogue(sample_method_specs):
    schemas = FakeClient(sample_method_specs).generate_tool_schemas("raw")

    assert schemas == sample_method_specs


def test_generate_tool_schemas_rejects_unknown_flavor(sample_method_specs):
    with pytest.raises(ValueError, match="unknown flavor"):
        FakeClient(sample_method_specs).generate_tool_schemas("unknown")

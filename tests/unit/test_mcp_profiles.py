from __future__ import annotations

from klink.mcp.profiles import filter_methods


def names(specs):
    return {m["name"] for m in specs}


def test_basic_profile_exposes_read_methods(sample_method_specs):
    filtered = filter_methods(sample_method_specs, ["basic"])

    assert names(filtered) == {"meta.ping", "layout.info"}


def test_draw_profile_exposes_write_methods_but_not_drc(sample_method_specs):
    filtered = filter_methods(sample_method_specs, ["draw"])

    assert names(filtered) == {"shape.insert_box"}


def test_drc_profile_exposes_drc_methods(sample_method_specs):
    filtered = filter_methods(sample_method_specs, ["drc"])

    assert names(filtered) == {"drc.run"}


def test_advanced_profile_exposes_exec_and_events(sample_method_specs):
    filtered = filter_methods(sample_method_specs, ["advanced"])

    assert names(filtered) == {"exec.python", "events.subscribe"}


def test_all_profile_returns_everything(sample_method_specs):
    filtered = filter_methods(sample_method_specs, ["all"])

    assert names(filtered) == names(sample_method_specs)


def test_multiple_profiles_are_union_without_duplicates(sample_method_specs):
    filtered = filter_methods(sample_method_specs, ["basic", "advanced", "all"])

    assert len(filtered) == len(sample_method_specs)
    assert names(filtered) == names(sample_method_specs)

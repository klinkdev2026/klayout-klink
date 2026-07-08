"""Offline tests for the profile-derived DRC deck (profile_drc.drc_script).

Pure-Python: no KLayout, no live session. The live gate (deck runs clean on a
drawn layout) lives in the integration suite; here we pin the GENERATED TEXT:
every rule and number must come from the profile (process purity), and the
constructs must stay inside the officially-documented DRC vocabulary the
repo's integration tests exercise (report/input/width/space/enclosed/
polygons/outside/sized/output).
"""
from __future__ import annotations

import re

import pytest

from klink.routing.grid.process_profile import ProcessProfile
from klink.routing.grid.profile_drc import drc_script

from synth_pdk import SYNTH_PROFILE  # tests/public conftest path


@pytest.fixture()
def profile() -> ProcessProfile:
    return SYNTH_PROFILE


def test_script_declares_report_first_and_all_layers(profile):
    script = profile.drc_script()
    lines = script.strip().splitlines()
    assert lines[0].startswith("report(")
    for spec in profile.routing_layers:
        layer, datatype = spec.split("/")
        assert f"input({layer}, {datatype})" in script


def test_width_and_space_rules_carry_profile_numbers(profile):
    script = profile.drc_script()
    for spec in profile.routing_layers:
        tag = spec.replace("/", "_")
        assert f"'width_{tag}'" in script
        assert f"'space_{tag}'" in script
    # numbers come from the profile, as micrometer FLOAT literals (an integer
    # literal would silently mean database units), with the default
    # projection metric (the Manhattan promise a grid router makes)
    assert re.search(rf"\.width\({profile.wire_width_um:g}(\.0)?, projection\)", script)
    assert re.search(rf"\.space\({profile.wire_clear_um:g}(\.0)?, projection\)", script)
    dims = re.findall(r"\.(?:width|space)\(([0-9.e+-]+), \w+\)", script)
    dims += re.findall(r"\.enclosed\(\w+, ([0-9.e+-]+)\)", script)
    assert dims, "no dimension literals found"
    for d in dims:
        assert "." in d, f"non-float dimension literal: {d}"


def test_via_rules_check_cut_enclosure_in_both_metals(profile):
    script = profile.drc_script()
    for lo, cut, up in profile.vias:
        cut_tag = cut.replace("/", "_")
        for metal in (lo, up):
            assert "'enc_%s_in_%s'" % (cut_tag, metal.replace('/', '_')) in script
    assert f"enclosed(" in script


def test_exclude_around_wraps_width_space_but_never_vias(profile):
    script = profile.drc_script(
        exclude_around=(profile.channel_layer, profile.wire_clear_um))
    excl = "excl_l" + profile.channel_layer.replace("/", "_")
    assert f"{excl} = input(" in script
    assert ".sized(" in script
    # width/space go through polygons.outside(excl)
    for line in script.splitlines():
        if ".width(" in line or ".space(" in line:
            assert f".polygons.outside({excl})" in line, line
        if ".enclosed(" in line:
            assert "outside" not in line, line


def test_metrics_option_validated_and_emitted(profile):
    assert ", euclidian)" in profile.drc_script(metrics="euclidian")
    with pytest.raises(ValueError):
        profile.drc_script(metrics="manhattan")


def test_layers_subset_and_no_vias(profile):
    only = profile.routing_layers[0]
    script = profile.drc_script(layers=[only], include_vias=False)
    assert "enclosed(" not in script
    for spec in profile.routing_layers[1:]:
        assert "'width_%s'" % spec.replace('/', '_') not in script


def test_zero_process_data_in_module():
    """Process purity: the mechanism module contains no layer numbers or
    dimensions of its own — strip docstrings/comments and look for numeric
    layer-like or micron-like literals."""
    import inspect
    import klink.routing.grid.profile_drc as mod

    src = inspect.getsource(mod)
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    src = re.sub(r"#.*", "", src)
    # allowed numerals: indexing/formatting only
    for num in re.findall(r"(?<![\w.])(\d{2,})(?![\w.])", src):
        assert False, f"suspicious hardcoded number {num} in profile_drc.py"

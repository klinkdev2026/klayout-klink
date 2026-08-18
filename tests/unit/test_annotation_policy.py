"""Offline tests for the ruler DECISION layer (`klink/annotation.py`).

The plugin hands rulers over as plain dicts; every choice made about them
happens here, so all of it is testable without KLayout.

The rule under the most pressure is the multi-segment one. KLayout 0.28
let a ruler have more than two points, and the old `p1`/`p2` pair still
answers — with the first and last point. Flattening a bent ruler that way
produces a straight cut line the user never drew, and the section that
comes out looks perfectly fine. So the refusal below is the point of the
module, not an edge case.
"""
from __future__ import annotations

import math

import pytest

from klink.annotation import (
    RulerError,
    cut_line_um,
    describe,
    pick_ruler,
    read_rulers,
    segment_lengths_um,
    total_length_um,
)


def ruler(rid, points, **kw):
    d = {"id": rid, "points_um": points, "segments": max(1, len(points) - 1)}
    d.update(kw)
    return d


STRAIGHT = ruler(1, [[0.0, 0.0], [10.0, 0.0]])
BENT = ruler(2, [[0.0, 0.0], [3.0, 0.0], [3.0, 4.0]])


def test_lengths_are_per_segment_not_endpoint_distance():
    assert segment_lengths_um(BENT) == pytest.approx([3.0, 4.0])
    # the polyline is 7 um long; the endpoint distance is 5 um, and
    # reporting that would be a different (wrong) number
    assert total_length_um(BENT) == pytest.approx(7.0)
    assert math.hypot(3.0, 4.0) == pytest.approx(5.0)


def test_two_point_ruler_is_its_own_cut_line():
    assert cut_line_um(STRAIGHT) == [[0.0, 0.0], [10.0, 0.0]]


def test_multi_segment_ruler_is_refused_not_flattened():
    with pytest.raises(RulerError) as excinfo:
        cut_line_um(BENT)
    msg = str(excinfo.value)
    # the error has to be actionable: it names the choices
    assert "segment=0" in msg and "segment=1" in msg
    assert "2 segments" in msg
    # and it must NOT have silently produced the endpoint line
    assert "never drew" in msg


def test_named_segment_cuts_that_leg():
    assert cut_line_um(BENT, segment=0) == [[0.0, 0.0], [3.0, 0.0]]
    assert cut_line_um(BENT, segment=1) == [[3.0, 0.0], [3.0, 4.0]]


def test_segment_out_of_range_says_the_range():
    with pytest.raises(RulerError, match=r"0\.\.1"):
        cut_line_um(BENT, segment=5)


def test_zero_length_segment_is_not_a_cut():
    degenerate = ruler(3, [[1.0, 1.0], [1.0, 1.0]])
    with pytest.raises(RulerError, match="zero length"):
        cut_line_um(degenerate)


def test_missing_points_names_the_right_call():
    with pytest.raises(RulerError, match="do not fall back to p1/p2"):
        cut_line_um({"id": 9})


def test_pick_single_ruler():
    assert pick_ruler([STRAIGHT])["id"] == 1


def test_pick_refuses_to_guess_between_rulers():
    with pytest.raises(RulerError) as excinfo:
        pick_ruler([STRAIGHT, BENT])
    msg = str(excinfo.value)
    assert "ambiguous" in msg
    assert "id=1" in msg and "id=2" in msg      # candidates are listed
    assert "ruler_id" in msg                     # and the way out is named


def test_gui_selection_breaks_the_tie():
    selected = dict(BENT, selected=True)
    assert pick_ruler([STRAIGHT, selected])["id"] == 2


def test_explicit_id_wins_over_selection():
    selected = dict(BENT, selected=True)
    assert pick_ruler([STRAIGHT, selected], ruler_id=1)["id"] == 1


def test_unknown_id_lists_what_exists():
    with pytest.raises(RulerError, match="7"):
        pick_ruler([STRAIGHT, BENT], ruler_id=7)


def test_category_filter_and_its_empty_case():
    tagged = dict(BENT, category="klink")
    assert pick_ruler([STRAIGHT, tagged], category="klink")["id"] == 2
    with pytest.raises(RulerError, match="annotation.update"):
        pick_ruler([STRAIGHT], category="klink")


def test_no_rulers_at_all_says_how_to_get_one():
    with pytest.raises(RulerError) as excinfo:
        pick_ruler([])
    msg = str(excinfo.value)
    assert "annotation.insert" in msg and "ruler tool" in msg


def test_describe_carries_id_label_and_length():
    labelled = dict(BENT, texts=["gate cut"])
    line = describe(labelled)
    assert "id=2" in line and "gate cut" in line and "7.000" in line


def test_read_rulers_unwraps_the_envelope():
    class FakeClient:
        def __init__(self):
            self.sent = []

        def call(self, method, params=None):
            self.sent.append((method, params))
            return {"count": 1, "rulers": [STRAIGHT]}

    c = FakeClient()
    assert read_rulers(c, selected_only=True) == [STRAIGHT]
    assert c.sent == [("annotation.list", {"selected_only": True})]

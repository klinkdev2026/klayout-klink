"""PUBLIC test: the repeat-group fitter (v3) — klink mechanism.

Golden families (test-owned truth generators, byte-exact standard):

- via array (w, pitch, n): n x n vias on an arithmetic grid + a cap plate
  whose edges are linear in the parameters — EXACTLY the shape the v2
  fitter proved unable to learn (the motivating case study) and the shape
  v3 must learn byte-exactly, including count values never sampled.
- alternating rails ((i & 1) parity structure): the canonical REFUSE
  boundary — v3 must refuse it by name, never fit it wrong.
- centered contact column with a floor((W-k)/pitch)+1 count: the
  floor_linear + centered-origin path, with bin-edge exemplars pinning
  the law so off-sample points verify byte-exact.

All arithmetic is integer dbu; the differential harness is the gate.
"""

import pytest

from klink.domains.structdevice import pcell_repeat as R
from klink.domains.structdevice.pcell_diff import verify_differential
from klink.domains.structdevice.pcell_fitter import FitterError


# --------------------------------------------------------------------------- #
# truth generators (test-owned; klink ships no device data)
# --------------------------------------------------------------------------- #

def via_boxes(params):
    """n x n via array + cap plate (margin 600), all int dbu."""
    w, p, n = int(params["w"]), int(params["pitch"]), int(params["n"])
    vias = sorted([i * p, j * p, i * p + w, j * p + w]
                  for i in range(n) for j in range(n))
    ext = (n - 1) * p + w
    return {"40/0": vias, "41/0": [[-600, -600, ext + 600, ext + 600]]}


def alt_rail_boxes(params):
    """Parity-alternating rails: (i & 1) picks a different rail shape —
    the structure v3 must REFUSE (ceil/floor(M/2) counts need coefficient
    1/2, outside integer floor_linear)."""
    m, p = int(params["m"]), 1200
    out = []
    for i in range(m):
        x = i * p
        if i & 1:
            out.append([x, 0, x + 400, 5000])
        else:
            out.append([x, -500, x + 400, 5500])
    return {"49/0": sorted(out)}


def contact_col_boxes(params):
    """Contact column centered on y=0: count = floor((W-300)/470)+1,
    pitch 470, contact 220 square at x=0."""
    W = int(params["W"])
    n = (W - 300) // 470 + 1
    first = -((n - 1) * 470 + 220) // 2
    return {"45/0": sorted([0, first + k * 470, 220, first + k * 470 + 220]
                           for k in range(n))}


def _exemplars(truth, grid):
    return [{"params": dict(p), "boxes": truth(p)} for p in grid]


# --------------------------------------------------------------------------- #
# acceptance gate 1: the via array is LEARNED, byte-exact off-sample
# --------------------------------------------------------------------------- #

_VIA_GRID = [{"w": w, "pitch": 800, "n": n}
             for n in (2, 3, 4, 5) for w in (500, 700)]


def test_via_array_learned_byte_exact_including_unsampled_counts():
    rep = R.analyze_boxes(_exemplars(via_boxes, _VIA_GRID),
                          ["w", "pitch", "n"])
    assert not rep.refusals, rep.summary()
    table = R.fit_table_v3(rep, sample_order=_VIA_GRID)
    assert table["format"] == "klink_fitted_device_pcell_v3"

    render = lambda p: R.render_table(table, p)
    # sampled points AND count values never sampled (n=6, 7) AND off-grid w
    probe = _VIA_GRID + [{"w": 600, "pitch": 800, "n": 6},
                         {"w": 550, "pitch": 800, "n": 7}]
    report = verify_differential(render, via_boxes, probe)
    assert report.all_ok, report.summary()


def test_via_array_structure_vias_are_a_group_plate_is_static():
    rep = R.analyze_boxes(_exemplars(via_boxes, _VIA_GRID),
                          ["w", "pitch", "n"])
    table = R.fit_table_v3(rep)
    style = table["styles"]["default"]
    groups = style["repeat_groups"]
    assert len(groups) == 1                      # the via family
    (group,) = groups.values()
    for axis in ("x", "y"):
        law = group["count"][axis]
        assert law == {"kind": "floor_linear", "num_base": 0,
                       "num_coef": {"w": 0, "pitch": 0, "n": 1},
                       "den": 1, "plus": 0}
        assert group["origin"][axis]["kind"] == "fixed"
    # the cap plate resolves to a plain static role (v2-style edges)
    assert len(style["roles"]) == 1
    (plate,) = style["roles"].values()
    assert plate["layer"] == "41/0"
    assert plate["edges"]["x2"]["kind"] == "parametric"


def test_parametric_pitch_is_learned():
    # pitch is a PARAMETER here (the real KLINK_VIA_ARRAY has it): the
    # position law must track it, not bake a constant
    grid = [{"w": 500, "pitch": p, "n": 3} for p in (800, 900, 1000)]
    rep = R.analyze_boxes(_exemplars(via_boxes, grid), ["w", "pitch", "n"])
    assert not rep.refusals, rep.summary()
    # n never varies -> honesty warning, count pinned to the sampled value
    assert any("param n has a single sampled value" in d
               for d in rep.decisions)
    table = R.fit_table_v3(rep, sample_order=grid)
    render = lambda p: R.render_table(table, p)
    report = verify_differential(
        render, via_boxes, grid + [{"w": 500, "pitch": 1150, "n": 3}])
    assert report.all_ok, report.summary()


# --------------------------------------------------------------------------- #
# acceptance gate 2: alternating structure is REFUSED by name
# --------------------------------------------------------------------------- #

def test_alternating_rails_refused_naming_the_family():
    grid = [{"m": m} for m in (2, 3, 4, 5)]
    rep = R.analyze_boxes(_exemplars(alt_rail_boxes, grid), ["m"])
    assert rep.refusals, "alternating rails must be refused, not fitted"
    assert any("49/0" in r for r in rep.refusals)
    assert "REFUSED" in rep.summary()
    with pytest.raises(FitterError, match=r"(?s)REFUSED.*49/0") as ei:
        R.fit_table_v3(rep)
    # the error is instructive: it names the way out
    assert "verify_differential" in str(ei.value) or \
           "structural parameter" in str(ei.value)


# --------------------------------------------------------------------------- #
# floor_linear count + centered origin (the NFET-contact-column shape)
# --------------------------------------------------------------------------- #

def test_centered_column_with_floor_count_learned():
    # bin-edge exemplars (770 = first W of the n=2 bin, 1239 = last) pin
    # the count law exactly; a sloppier sample set would leave the base
    # under-determined and the off-sample check would catch it
    grid = [{"W": W} for W in (770, 1239, 1240, 1709)]
    rep = R.analyze_boxes(_exemplars(contact_col_boxes, grid), ["W"])
    assert not rep.refusals, rep.summary()
    table = R.fit_table_v3(rep, sample_order=grid)
    (group,) = table["styles"]["default"]["repeat_groups"].values()
    assert group["origin"]["y"]["kind"] == "centered"
    # the learned law must be THE truth law up to the floor identity
    # floor(x/d)+k == floor((x+k*d)/d): compare in canonical (plus=0) form
    law = group["count"]["y"]
    assert law["den"] == 470 and law["num_coef"] == {"W": 1}
    assert law["num_base"] + law["plus"] * 470 == -300 + 1 * 470
    render = lambda p: R.render_table(table, p)
    probe = grid + [{"W": 1000}, {"W": 1500}, {"W": 2100}, {"W": 2650}]
    report = verify_differential(render, contact_col_boxes, probe)
    assert report.all_ok, report.summary()


# --------------------------------------------------------------------------- #
# harness compatibility + guard rails
# --------------------------------------------------------------------------- #

def test_render_table_accepts_v2_tables():
    v2 = {"format": "klink_fitted_device_pcell_v2",
          "param_order": ["w"],
          "sample_order": [],
          "styles": {"default": {"roles": {"r": {"layer": "1/0", "edges": {
              "x1": {"kind": "parametric", "base": 0, "coef": {"w": -500}},
              "y1": {"kind": "parametric", "base": 0, "coef": {}},
              "x2": {"kind": "parametric", "base": 0, "coef": {"w": 500}},
              "y2": {"kind": "parametric", "base": 2000, "coef": {}},
          }}}}}}
    assert R.render_table(v2, {"w": 4}) == {"1/0": [[-2000, 0, 2000, 2000]]}


def test_needs_two_exemplars_and_boxes():
    with pytest.raises(FitterError):
        R.analyze_boxes([], ["w"])
    with pytest.raises(FitterError, match="no boxes"):
        R.analyze_boxes([{"params": {"w": 1}, "boxes": {}},
                         {"params": {"w": 2}, "boxes": {}}], ["w"])


def test_non_grid_positions_refused():
    def diag(params):
        n = int(params["n"])  # boxes on a diagonal: grid product missing
        return {"7/0": sorted([k * 100, k * 100, k * 100 + 50, k * 100 + 50]
                              for k in range(n))}
    grid = [{"n": n} for n in (2, 3, 4)]
    rep = R.analyze_boxes(_exemplars(diag, grid), ["n"])
    assert rep.refusals and any("7/0" in r for r in rep.refusals)


def test_sampled_envelope_recorded_in_table():
    rep = R.analyze_boxes(_exemplars(via_boxes, _VIA_GRID),
                          ["w", "pitch", "n"])
    table = R.fit_table_v3(rep)
    assert table["sampled"]["params"]["n"] == {"min": 2.0, "max": 5.0}
    assert len(table["sampled"]["points"]) == len(_VIA_GRID)

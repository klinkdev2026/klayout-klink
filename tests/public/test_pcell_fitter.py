"""PUBLIC test: the general parametric PCell fitter (klink mechanism).

Pure Python -- no KLayout, no lab data. Exercises the fitter on a SYNTHETIC
3-parameter, multi-layer device to prove it is general (not W/L / transistor
specific): screening (linear / constant / unexplained classification) + the
fully-parametric table it emits.
"""

import pytest

from klink.domains.structdevice import pcell_fitter as F


# A synthetic device with 3 parameters (w, l, h) on 3 layers. Every box edge is
# constructed so we KNOW its true relationship, so we can assert the screening:
#   metal  (10/0): a rectangle that scales with w (x) and l (y)         -> linear
#   contact(20/0): fixed width in x, height scales with h               -> const + linear
#   guard  (30/0): x1 follows no (w,l,h) law (hand-drawn noise)         -> unexplained
_SIZES = [(10, 4, 2), (20, 4, 2), (10, 8, 2), (10, 4, 6), (20, 8, 6)]
_GUARD_X1 = {  # deliberately not a linear function of (w, l, h)
    (10, 4, 2): 0.0, (20, 4, 2): 5.0, (10, 8, 2): -3.0, (10, 4, 6): 2.0, (20, 8, 6): 8.0}


def _exemplars():
    out = []
    for w, l, h in _SIZES:
        roles = {
            "metal": {"layer": "10/0",
                      "box_um": [-w / 2, -l / 2, w / 2, l / 2]},
            "contact": {"layer": "20/0",
                        "box_um": [-3.0, -h, 3.0, h]},
            "guard": {"layer": "30/0",
                      "box_um": [_GUARD_X1[(w, l, h)], -1.0, 4.0, 1.0]},
        }
        out.append({"params": {"w": w, "l": l, "h": h}, "roles": roles})
    return out


def _edge(report, role, edge):
    return next(e for e in report.edges if e.role == role and e.edge == edge)


def test_screens_linear_constant_and_unexplained():
    rep = F.analyze(_exemplars(), ["w", "l", "h"], r2_threshold=0.99)
    assert rep.param_names == ["w", "l", "h"]
    # metal scales cleanly with w/l -> linear, exact slopes (dbu per um)
    mx1 = _edge(rep, "metal", "x1")
    assert mx1.classification == "linear"
    assert mx1.coef_dbu["w"] == pytest.approx(-500.0)      # -w/2 um -> -500 dbu/um
    assert mx1.coef_dbu["l"] == pytest.approx(0.0, abs=1e-6)
    assert mx1.coef_dbu["h"] == pytest.approx(0.0, abs=1e-6)
    my1 = _edge(rep, "metal", "y1")
    assert my1.coef_dbu["l"] == pytest.approx(-500.0)
    # contact: x edges fixed (constant), y edges driven by h (linear)
    assert _edge(rep, "contact", "x1").classification == "constant"
    cy2 = _edge(rep, "contact", "y2")
    assert cy2.classification == "linear"
    assert cy2.coef_dbu["h"] == pytest.approx(1000.0)      # +h um -> 1000 dbu/um
    # guard.x1 follows no law -> unexplained, surfaced as a decision
    gx1 = _edge(rep, "guard", "x1")
    assert gx1.classification == "unexplained"
    assert any("guard.x1" in d for d in rep.decisions_needed)


def test_fit_table_is_canonical_v2_and_parametric():
    rep = F.analyze(_exemplars(), ["w", "l", "h"], r2_threshold=0.99)
    table = F.fit_table(rep, style="default",
                        sample_order=[{"w": w, "l": l, "h": h} for w, l, h in _SIZES],
                        param_units={"w": "um", "l": "um", "h": "um"})
    assert table["format"] == "klink_fitted_device_pcell_v2"
    assert table["param_order"] == ["w", "l", "h"]
    roles = table["styles"]["default"]["roles"]
    assert set(roles) == {"metal", "contact", "guard"}
    # every edge is parametric with integer dbu base + coef (draws at any size)
    for role in roles.values():
        for edge in role["edges"].values():
            assert edge["kind"] == "parametric"
            assert edge["base"] == int(edge["base"])
            assert set(edge["coef"]) == {"w", "l", "h"}
            assert all(c == int(c) for c in edge["coef"].values())
    # the unexplained guard.x1 was pinned to a constant (all-zero coef)
    assert all(c == 0 for c in roles["guard"]["edges"]["x1"]["coef"].values())


def test_keep_roles_drops_unwanted_roles():
    rep = F.analyze(_exemplars(), ["w", "l", "h"])
    table = F.fit_table(rep, keep_roles=["metal", "contact"])
    assert set(table["styles"]["default"]["roles"]) == {"metal", "contact"}


def test_fitted_linear_edge_reproduces_and_extrapolates():
    rep = F.analyze(_exemplars(), ["w", "l", "h"])
    table = F.fit_table(rep)
    mx2 = table["styles"]["default"]["roles"]["metal"]["edges"]["x2"]

    def value_um(edge, w, l, h):
        v = edge["base"] + edge["coef"]["w"] * w + edge["coef"]["l"] * l + edge["coef"]["h"] * h
        return v / 1000.0

    # reproduces exemplar points (metal x2 = w/2) and extrapolates to a new size
    assert value_um(mx2, 10, 4, 2) == pytest.approx(5.0)
    assert value_um(mx2, 20, 8, 6) == pytest.approx(10.0)
    assert value_um(mx2, 100, 4, 2) == pytest.approx(50.0)   # extrapolated


def test_needs_two_exemplars_and_independent_params():
    with pytest.raises(F.FitterError):
        F.analyze([], ["w"])
    with pytest.raises(F.FitterError):
        F.analyze(_exemplars()[:1], ["w", "l", "h"])

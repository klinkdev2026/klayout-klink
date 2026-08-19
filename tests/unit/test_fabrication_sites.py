from __future__ import annotations

import math

import pytest

from klink.domains.fabrication.sites import (
    Site,
    filter_sites,
    footprint_corners_inside_die,
    generate_circular_die_sites,
    generate_grid_sites,
    number_sites,
)


def test_grid_sites_and_numbering_schemes_are_deterministic():
    sites = generate_grid_sites([0, 0, 20, 10], pitch_x_um=10, pitch_y_um=10)

    assert [site.center_um for site in sites] == [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (0.0, 10.0), (10.0, 10.0), (20.0, 10.0)]
    assert [site.site_id for site in number_sites(sites, scheme="rowcol")] == ["R0C0", "R0C1", "R0C2", "R1C0", "R1C1", "R1C2"]
    assert [site.site_id for site in number_sites(sites, scheme="sequential")] == ["0", "1", "2", "3", "4", "5"]
    assert [site.site_id for site in number_sites(sites, scheme="serpentine")] == ["0", "1", "2", "5", "4", "3"]
    assert [site.site_id for site in number_sites(sites, scheme="prefix", prefix="D", width=2)] == ["D00", "D01", "D02", "D03", "D04", "D05"]


def test_circular_die_footprint_corner_exactly_on_boundary_is_kept():
    radius = 10.0
    footprint = (2.0, 4.0)
    x = math.sqrt(radius**2 - 2.0**2) - 1.0

    assert footprint_corners_inside_die([x, 0.0], [0.0, 0.0], radius, footprint) is True

    sites = generate_circular_die_sites([0, 0], 20, pitch_x_um=x, pitch_y_um=99, edge_exclusion_um=0, footprint_um=footprint)
    assert any(abs(site.center_um[0] - x) < 1e-9 and abs(site.center_um[1]) < 1e-9 for site in sites)


def test_circular_die_footprint_corner_epsilon_outside_is_rejected():
    radius = 10.0
    footprint = (2.0, 4.0)
    x = math.sqrt(radius**2 - 2.0**2) - 1.0 + 1e-6

    assert footprint_corners_inside_die([x, 0.0], [0.0, 0.0], radius, footprint) is False


def test_circular_die_diagonal_case_rejects_what_circle_approximation_would_keep():
    radius = 10.0
    footprint = 4.0
    center = 5.6
    circular_approx_would_keep = math.hypot(center, center) + footprint / 2.0 <= radius

    assert circular_approx_would_keep is True
    assert footprint_corners_inside_die([center, center], [0.0, 0.0], radius, footprint) is False


def test_filter_sites_uses_keep_in_and_keep_out_polygons():
    sites = [
        Site("A", 0, 0, (0, 0)),
        Site("B", 0, 1, (5, 5)),
        Site("C", 0, 2, (20, 20)),
    ]
    keep_in = [[[0, 0], [10, 0], [10, 10], [0, 10]]]
    keep_out = [[[4, 4], [6, 4], [6, 6], [4, 6]]]

    assert [site.site_id for site in filter_sites(sites, keep_in, keep_out)] == ["A"]


def test_site_generation_validates_boundaries():
    with pytest.raises(ValueError, match="footprint"):
        generate_circular_die_sites([0, 0], 10, 1, 1, 0, footprint_um=(-1, 1))
    with pytest.raises(ValueError, match="numbering"):
        number_sites([], scheme="bad")

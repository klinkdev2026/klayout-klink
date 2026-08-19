"""Fabrication annotation, alignment mark, and site mapping helpers."""

from .compose import DieTemplate, WaferTemplate, compose_complete_mask
from .mapping import SiteMap, default_site_paths, write_sites_csv, write_sites_json
from .marks import (
    CHIP_MARKS,
    CORNER_COMPOSITE_MARKS,
    FIELD_MARKS,
    GLOBAL_MARKS,
    box_in_box,
    cross,
    cross_in_box,
    from_preset_groups,
    l_mark,
    vernier_pair,
)
from .orchestrators import place_site_array
from .sites import (
    Site,
    filter_sites,
    footprint_corners_inside_die,
    generate_circular_die_sites,
    generate_grid_sites,
    number_sites,
)

__all__ = [
    "CHIP_MARKS",
    "CORNER_COMPOSITE_MARKS",
    "DieTemplate",
    "FIELD_MARKS",
    "GLOBAL_MARKS",
    "Site",
    "SiteMap",
    "WaferTemplate",
    "box_in_box",
    "cross",
    "cross_in_box",
    "compose_complete_mask",
    "default_site_paths",
    "filter_sites",
    "footprint_corners_inside_die",
    "generate_circular_die_sites",
    "generate_grid_sites",
    "from_preset_groups",
    "l_mark",
    "number_sites",
    "place_site_array",
    "vernier_pair",
    "write_sites_csv",
    "write_sites_json",
]

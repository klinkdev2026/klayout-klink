"""Legality / fixed-obstacle surface on TrackGrid Node fields (Stage T5, increment B).

Maps the worker's `CapacityGrid` legality structures onto `TrackGrid` Node fixed-shape /
blocked fields (T1 grid). Design: see the mapping table in this module's docstring, §1
group (B). This is the DRC SUBSTRATE for later increments -- it is NOT DRC itself (no spacing/PRL/
min-area rules yet; that is the DRC step). Adapter-only: writes Node fields + returns the
owner index; no maze, no worker rewrite.

Mapping (CapacityGrid -> TrackGrid Node):
| CapacityGrid                 | meaning                          | TrackGrid Node field |
|------------------------------|----------------------------------|----------------------|
| `wire_blocked_all[z]` (chan) | no wire, ALL nets (hard)         | `blocked_E/N` (+W/S neighbours) + `fsc_planar_*` = BLOCK |
| `pad_cells[z][owner]` / `pad_owner` | foreign metal: owner OK, others keep-out | `fsc_planar_*` = BLOCK + owner side-index |
| `via_blocked` (device body)  | no via may land                  | `fsc_via` = BLOCK    |

The owner-allowed nuance is net-specific, so it lives in a side index (OpenROAD's Node
is net-agnostic too: own-net shapes are sources, not obstacles). The Node carries the
net-agnostic PRESENCE of fixed metal (`fsc_planar`/`fsc_via` saturated); the adapter
predicate combines it with the owner index.
"""

from __future__ import annotations

from typing import Dict, Iterable, Tuple

from klink.routing.backends.pnr_multilayer.grid.track_grid import TrackGrid

BLOCK = 255   # saturated fixed-shape cost == a hard obstacle (cost_bits = 8)
_MULTI_OWNER = "\x00__multi_owner__\x00"   # cell owned by 2+ nets -> keep-out for ALL
                                           # (matches flexdr._invert_pad_cells; == the
                                           # owner-aware predicate never lets a foreign
                                           # net onto another net's metal, incl. PDN halos)

Cell = Tuple[int, int, int]            # (xi, yi, zi)
PadCell = Tuple[int, int, int, str]    # (xi, yi, zi, owner)


def load_legality(
    grid: TrackGrid,
    *,
    channel: Iterable[Cell] = (),
    pads: Iterable[PadCell] = (),
    via_blocked: Iterable[Cell] = (),
) -> Dict[Tuple[int, int, int], str]:
    """Write obstacles onto ``grid`` Node fields and return the pad-owner index
    ``{(zi, xi, yi): owner}``. ``channel`` = all-net wire keep-out, ``pads`` = foreign
    metal (owner may still route), ``via_blocked`` = no via landing (device body)."""
    n = grid.nodes

    def mark_fixed_metal(xi: int, yi: int, zi: int) -> int:
        idx = grid.get_idx(xi, yi, zi)
        n["fsc_planar_h"][idx] = BLOCK
        n["fsc_planar_v"][idx] = BLOCK
        return idx

    # channel: all-net hard block -> also block the planar EDGES touching the node so
    # the maze cannot traverse through it (forward-edge model: edge between x and x+1 is
    # blocked_E on the lower-x node; between y and y+1 is blocked_N on the lower-y node).
    for (xi, yi, zi) in channel:
        idx = mark_fixed_metal(xi, yi, zi)
        n["blocked_E"][idx] = 1
        n["blocked_N"][idx] = 1
        if xi - 1 >= 0:
            n["blocked_E"][grid.get_idx(xi - 1, yi, zi)] = 1
        if yi - 1 >= 0:
            n["blocked_N"][grid.get_idx(xi, yi - 1, zi)] = 1

    owner: Dict[Tuple[int, int, int], str] = {}
    for (xi, yi, zi, who) in pads:
        mark_fixed_metal(xi, yi, zi)
        k = (zi, xi, yi)
        cur = owner.get(k)
        # a cell claimed by 2+ nets is a keep-out for EVERY net (incl. the last
        # writer): otherwise a signal whose pad-halo overlaps a PDN halo would be
        # let onto that cell and short to power. Faithful to engine A's _MULTI_OWNER.
        owner[k] = who if cur is None else (cur if cur == who else _MULTI_OWNER)

    for (xi, yi, zi) in via_blocked:
        n["fsc_via"][grid.get_idx(xi, yi, zi)] = BLOCK

    return owner

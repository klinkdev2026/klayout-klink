"""2.5d display-list derivation — StackSpec + a z table -> view.show_25d.

MECHANISM ONLY (process purity): klink ships no z heights. Layer thickness
and elevation are process facts the caller owns; this module just combines
the caller's ``StackSpec`` (which layers exist, their roles and vertical
relations) with the caller's z table into the display list the
``view.show_25d`` RPC accepts, with instructive errors when they disagree.

The RPC drives KLayout's native 2.5d viewer (official ``D25View`` API,
available since KLayout 0.28 on OpenGL-enabled builds):

    from klink import KLinkClient
    from klink.stack25d import stack_displays

    displays = stack_displays(stack, z_um={
        "31/0": (0.0, 0.5),          # zstart, zstop in microns
        "32/0": (0.5, 1.0),
        "33/0": (1.0, 1.5),
    }, colors={"31/0": 0x2B6CB0})
    with KLinkClient() as c:
        c.show_25d(displays, cell="MY_TOP")
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from klink.process_stack import StackSpec, StackError


def _z_range(value: Any, layer: str) -> Tuple[float, float]:
    if isinstance(value, Mapping):
        try:
            z0, z1 = float(value["zstart_um"]), float(value["zstop_um"])
        except (KeyError, TypeError, ValueError):
            raise StackError(
                f"z_um[{layer!r}] mapping needs numeric zstart_um/zstop_um"
            ) from None
    else:
        try:
            z0, z1 = (float(value[0]), float(value[1]))
        except (TypeError, ValueError, IndexError):
            raise StackError(
                f"z_um[{layer!r}] must be (zstart_um, zstop_um) in microns,"
                f" got {value!r}") from None
    if not z1 > z0:
        raise StackError(
            f"z_um[{layer!r}]: zstop ({z1}) must be above zstart ({z0})")
    return z0, z1


def stack_displays(
    stack: StackSpec,
    z_um: Mapping[str, Any],
    *,
    colors: Optional[Mapping[str, int]] = None,
    names: Optional[Mapping[str, str]] = None,
    include_vias: bool = True,
    extra_layers: Sequence[str] = (),
) -> List[Dict[str, Any]]:
    """Build the ``view.show_25d`` display list for this stack.

    z_um: layer ('L/D') -> (zstart_um, zstop_um) — REQUIRED for every stack
        conductor (and via layer when include_vias). These are process
        facts you own; klink refuses to guess a missing one.
    colors: optional layer -> 0xRRGGBB; omitted layers use the 2.5d
        window's defaults.
    names: optional layer -> material name; defaults to the conductor's
        declared role, else the layer number.
    extra_layers: additional 'L/D' layers (markers, substrate outlines)
        to display; they too need a z_um entry.
    """
    colors = dict(colors or {})
    names = dict(names or {})

    wanted: List[Tuple[str, str]] = []          # (layer, default_name)
    for c in stack.conductors:
        wanted.append((c.layer, c.role or c.layer))
    if include_vias:
        for v in stack.vias:
            if all(v.via_layer != w[0] for w in wanted):
                wanted.append((v.via_layer, f"via {v.a}<->{v.b}"))
    for extra in extra_layers:
        if all(extra != w[0] for w in wanted):
            wanted.append((str(extra), str(extra)))

    missing = [layer for layer, _ in wanted if layer not in z_um]
    if missing:
        raise StackError(
            f"z_um lacks entries for stack layers {missing}; pass "
            "(zstart_um, zstop_um) for each — z heights are process facts "
            "the caller owns, klink does not guess them. Use "
            "include_vias=False to skip via layers.")

    displays: List[Dict[str, Any]] = []
    for layer, default_name in wanted:
        z0, z1 = _z_range(z_um[layer], layer)
        entry: Dict[str, Any] = {
            "layer": layer,
            "zstart_um": z0,
            "zstop_um": z1,
            "name": names.get(layer, default_name),
        }
        if layer in colors:
            entry["color"] = int(colors[layer])
        displays.append(entry)
    displays.sort(key=lambda d: (d["zstart_um"], d["zstop_um"], d["layer"]))
    return displays

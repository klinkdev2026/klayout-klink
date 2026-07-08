"""Profile-derived DRC — generate a KLayout DRC runset from a ProcessProfile.

The same profile instance that drives routing (capacity grid, via drawing)
and LVS (``connectivity_spec``) now also derives the DRC deck, so all three
gates read ONE process declaration: what the router promises, the DRC deck
checks, and LVS extraction assumes — same numbers, same layers, by
construction.

MECHANISM ONLY (process purity): this module holds no layer numbers, no
dimensions. Every emitted rule comes from the profile the caller passes in.

The generated script uses the official KLayout DRC language (Ruby DSL run by
the ``drc.run`` RPC inside KLayout). Constructs used — all documented in the
KLayout DRC reference and exercised by this repo's DRC integration tests:

    report("name")                      open a report database (interactive)
    input(layer, datatype)              read a layout layer (merged polygons)
    layer.width(w)  / layer.space(s)    width / spacing checks (µm floats)
    cut.enclosed(metal, e)              cut must sit >= e inside metal
    check.polygons                      edge-pair markers -> polygons
    polys.outside(region)               keep only markers fully outside region
    x.output("cat", "description")      file results under a report category

Values are emitted as plain floats — the official default unit is
micrometers (integers would mean database units).

Derived rules:

    width  >= profile.wire_width_um     on each routing layer
    space  >= profile.wire_clear_um     on each routing layer
    cut enclosed by both metals >= profile.litho_tol_um   for each via
        (the drawing mechanism cuts via_pad - 2*litho_tol, so per-edge
         enclosure equals litho_tol by construction)

Device-internal geometry (the fitted device's own plates and gaps) follows
DEVICE rules, not routing rules — a source/drain gap smaller than the wire
clearance is a device fact, not a routing violation. Pass
``exclude_around=(layer_spec, size_um)`` (typically the profile's channel /
device-marker layer) to suppress width/space markers that touch the device
regions; via-enclosure checks are never excluded (a bad via is bad anywhere).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

from klink.routing.grid.process_profile import ProcessProfile


def _parse_layer(spec: str) -> Tuple[int, int]:
    """'101/0' -> (101, 0); a bare '101' means datatype 0."""
    parts = str(spec).split("/")
    layer = int(parts[0])
    datatype = int(parts[1]) if len(parts) > 1 else 0
    return layer, datatype


def _um(value: float) -> str:
    """Emit a micrometer value as a float literal (the DRC language's default
    unit for floats; an integer literal would mean database units)."""
    text = f"{float(value):.6g}"
    return text if "." in text or "e" in text else text + ".0"


def _ident(spec: str) -> str:
    return "l" + spec.replace("/", "_")


def drc_script(
    profile: ProcessProfile,
    *,
    layers: Optional[Sequence[str]] = None,
    include_vias: bool = True,
    exclude_around: Optional[Tuple[str, float]] = None,
    metrics: str = "projection",
    report_name: str = "klink profile DRC",
    report_to_output_rdb: bool = False,
) -> str:
    """Build the DRC runset text for this profile.

    layers: routing layers to check (default: profile.routing_layers).
    include_vias: also emit cut-enclosure checks for profile.vias.
    exclude_around: (layer_spec, size_um) device-region marker; width/space
        markers fully inside/touching the sized region are suppressed.
    metrics: distance metric for width/space — 'projection' (default; only
        parallel edge projections count, matching the Manhattan promise a
        grid router actually makes and ignoring right-angle corner
        artifacts), 'euclidian' (stricter, true shortest distance) or
        'square' (official KLayout metrics).
    report_to_output_rdb: emit ``report(name, $output_rdb)`` so the server
        substitutes the ``output_rdb`` request parameter (used by run_drc).
    """
    if metrics not in ("projection", "euclidian", "square"):
        raise ValueError(
            f"metrics must be projection/euclidian/square, got {metrics!r}")
    checked = tuple(layers) if layers is not None else tuple(profile.routing_layers)
    lines: list[str] = []
    if report_to_output_rdb:
        lines.append(f'report({report_name!r}, $output_rdb)')
    else:
        lines.append(f'report({report_name!r})')

    declared: dict[str, str] = {}

    def declare(spec: str) -> str:
        name = declared.get(spec)
        if name is None:
            name = _ident(spec)
            layer, datatype = _parse_layer(spec)
            lines.append(f"{name} = input({layer}, {datatype})")
            declared[spec] = name
        return name

    excl_name = None
    if exclude_around is not None:
        excl_spec, excl_size = exclude_around
        layer, datatype = _parse_layer(excl_spec)
        excl_name = "excl_" + _ident(excl_spec)
        lines.append(
            f"{excl_name} = input({layer}, {datatype}).sized({_um(excl_size)})")

    def emit_check(expr: str, category: str, description: str) -> None:
        if excl_name is not None:
            # edge-pair markers -> polygons, keep only those fully outside
            # the device exclusion region (official: DRC#polygons,
            # Layer#outside).
            lines.append(
                f'{expr}.polygons.outside({excl_name})'
                f'.output({category!r}, {description!r})')
        else:
            lines.append(f'{expr}.output({category!r}, {description!r})')

    for spec in checked:
        name = declare(spec)
        emit_check(
            f"{name}.width({_um(profile.wire_width_um)}, {metrics})",
            f"width_{spec.replace('/', '_')}",
            f"{spec}: width < {_um(profile.wire_width_um)} um "
            f"(profile wire_width_um, {metrics})")
        emit_check(
            f"{name}.space({_um(profile.wire_clear_um)}, {metrics})",
            f"space_{spec.replace('/', '_')}",
            f"{spec}: space < {_um(profile.wire_clear_um)} um "
            f"(profile wire_clear_um, {metrics})")

    if include_vias:
        tol = _um(profile.litho_tol_um)
        for lo, cut, up in profile.vias:
            cut_name = declare(cut)
            for metal in (lo, up):
                if metal not in checked:
                    continue
                metal_name = declare(metal)
                category = (f"enc_{cut.replace('/', '_')}"
                            f"_in_{metal.replace('/', '_')}")
                description = (f"{cut} cut enclosure in {metal} < {tol} um "
                               f"(profile litho_tol_um)")
                # never excluded: a poorly-enclosed cut is a defect anywhere
                lines.append(
                    f"{cut_name}.enclosed({metal_name}, {tol})"
                    f".output({category!r}, {description!r})")

    return "\n".join(lines) + "\n"


def run_drc(
    client,
    profile: ProcessProfile,
    *,
    layers: Optional[Sequence[str]] = None,
    include_vias: bool = True,
    exclude_around: Optional[Tuple[str, float]] = None,
    metrics: str = "projection",
    output_rdb: Optional[str] = None,
    report_name: str = "klink profile DRC",
) -> dict:
    """Run the profile-derived DRC on the client's current layout and return
    a structured verdict:

        {ok, total, categories: [{name, description, count}], rdb_file,
         script, raw}

    ``ok`` is True only when the deck ran without exception AND zero
    violations were filed — the same all-or-nothing shape as the LVS gate.
    The report database is written to ``output_rdb`` (a temp .lyrdb next to
    the system temp dir when not given; localhost RPC shares the filesystem).
    """
    if output_rdb is None:
        output_rdb = str(
            Path(tempfile.gettempdir()) / "klink_profile_drc.lyrdb"
        ).replace("\\", "/")

    script = drc_script(
        profile,
        layers=layers,
        include_vias=include_vias,
        exclude_around=exclude_around,
        metrics=metrics,
        report_name=report_name,
        report_to_output_rdb=True,
    )
    raw = client.drc_run(script, output_rdb=output_rdb, result_mode="summary")

    summary = raw.get("rdb_summary") or {}
    categories = summary.get("categories") or []
    total = int(summary.get("total_items") or 0)
    ok = raw.get("exception") is None and not summary.get("error") and total == 0
    return {
        "ok": bool(ok),
        "total": total,
        "categories": categories,
        "rdb_file": raw.get("rdb_file"),
        "script": script,
        "raw": raw,
    }

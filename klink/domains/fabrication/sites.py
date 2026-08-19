"""Site generation, filtering, and deterministic numbering."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil, floor, hypot
from typing import Sequence


@dataclass(frozen=True)
class Site:
    site_id: str
    row: int
    col: int
    center_um: tuple[float, float]
    rotation_deg: float = 0.0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["center_um"] = list(self.center_um)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Site":
        return cls(
            site_id=str(data["site_id"]),
            row=int(data["row"]),
            col=int(data["col"]),
            center_um=(float(data["center_um"][0]), float(data["center_um"][1])),
            rotation_deg=float(data.get("rotation_deg", 0.0)),
        )


def generate_grid_sites(
    region_bbox_um: Sequence[float],
    pitch_x_um: float,
    pitch_y_um: float,
    margin_um: float = 0.0,
    rotation_deg: float = 0.0,
) -> list[Site]:
    x0, y0, x1, y1 = _bbox(region_bbox_um)
    _require_positive(pitch_x_um=pitch_x_um, pitch_y_um=pitch_y_um)
    margin = float(margin_um)
    if margin < 0:
        raise ValueError("margin_um must be non-negative")
    xmin, xmax = x0 + margin, x1 - margin
    ymin, ymax = y0 + margin, y1 - margin
    if xmin > xmax or ymin > ymax:
        return []
    xs = _axis_positions(xmin, xmax, float(pitch_x_um))
    ys = _axis_positions(ymin, ymax, float(pitch_y_um))
    sites = [Site(_rowcol(row, col), row, col, (x, y), float(rotation_deg)) for row, y in enumerate(ys) for col, x in enumerate(xs)]
    return sites


def generate_circular_die_sites(
    center_um: Sequence[float],
    diameter_um: float,
    pitch_x_um: float,
    pitch_y_um: float,
    edge_exclusion_um: float,
    footprint_um: float | Sequence[float] = 0.0,
) -> list[Site]:
    _require_positive(diameter_um=diameter_um, pitch_x_um=pitch_x_um, pitch_y_um=pitch_y_um)
    if edge_exclusion_um < 0:
        raise ValueError("edge_exclusion_um must be non-negative")
    cx, cy = float(center_um[0]), float(center_um[1])
    usable = float(diameter_um) / 2.0 - float(edge_exclusion_um)
    if usable < 0:
        return []
    fw, fh = _footprint(footprint_um)
    xs = [cx + i * float(pitch_x_um) for i in range(ceil(-usable / pitch_x_um), floor(usable / pitch_x_um) + 1)]
    ys = [cy + i * float(pitch_y_um) for i in range(ceil(-usable / pitch_y_um), floor(usable / pitch_y_um) + 1)]
    sites = []
    for row, y in enumerate(ys):
        for col, x in enumerate(xs):
            if _footprint_corners_inside((x, y), (cx, cy), usable, fw, fh):
                sites.append(Site(_rowcol(row, col), row, col, (x, y)))
    return sites


def number_sites(
    sites: Sequence[Site],
    *,
    scheme: str = "rowcol",
    prefix: str = "S",
    width: int = 3,
    order: str = "bottom_up",
    start: int = 0,
) -> list[Site]:
    """Assign deterministic ids to sites.

    ``order`` sets the counting traversal: ``bottom_up`` (default, row 0
    first — the historical behavior, byte-stable for existing callers) or
    ``top_down`` (highest row first; reading order for labels). ``start``
    offsets the counter for ``sequential``/``prefix``/``serpentine``
    (default 0 — historical behavior); ``rowcol`` ids are positional and
    ignore it.
    """
    if order == "bottom_up":
        row_key = lambda s: (s.row, s.col)  # noqa: E731
        row_iter_reverse = False
    elif order == "top_down":
        row_key = lambda s: (-s.row, s.col)  # noqa: E731
        row_iter_reverse = True
    else:
        raise ValueError(
            f"unknown order {order!r}; supported: bottom_up, top_down")
    start = int(start)
    ordered = sorted(sites, key=row_key)
    if scheme == "rowcol":
        return [Site(_rowcol(s.row, s.col), s.row, s.col, s.center_um, s.rotation_deg) for s in ordered]
    if scheme == "sequential":
        return [Site(str(start + i), s.row, s.col, s.center_um, s.rotation_deg) for i, s in enumerate(ordered)]
    if scheme == "prefix":
        return [Site(f"{prefix}{start + i:0{int(width)}d}", s.row, s.col, s.center_um, s.rotation_deg) for i, s in enumerate(ordered)]
    if scheme == "serpentine":
        grouped: dict[int, list[Site]] = {}
        for site in ordered:
            grouped.setdefault(site.row, []).append(site)
        result = []
        n = start
        for pos, row in enumerate(sorted(grouped, reverse=row_iter_reverse)):
            # bottom_up keeps the HISTORICAL zigzag phase (actual row parity,
            # byte-stable even when whole rows were filtered out); top_down is
            # new and zigzags by traversal position.
            zig = bool(row % 2) if order == "bottom_up" else bool(pos % 2)
            row_sites = sorted(grouped[row], key=lambda s: s.col, reverse=zig)
            for site in row_sites:
                result.append(Site(str(n), site.row, site.col, site.center_um, site.rotation_deg))
                n += 1
        return sorted(result, key=lambda s: (s.row, s.col))
    raise ValueError(f"unknown numbering scheme {scheme!r}; supported: prefix, rowcol, sequential, serpentine")


def filter_sites(
    sites: Sequence[Site],
    keep_in_polygons_um: Sequence[Sequence[Sequence[float]]] | None = None,
    keep_out_polygons_um: Sequence[Sequence[Sequence[float]]] | None = None,
) -> list[Site]:
    result = []
    for site in sites:
        pt = site.center_um
        if keep_in_polygons_um and not any(_point_in_poly(pt, poly) for poly in keep_in_polygons_um):
            continue
        if keep_out_polygons_um and any(_point_in_poly(pt, poly) for poly in keep_out_polygons_um):
            continue
        result.append(site)
    return result


def footprint_corners_inside_die(
    site_center_um: Sequence[float],
    die_center_um: Sequence[float],
    usable_radius_um: float,
    footprint_um: float | Sequence[float] = 0.0,
) -> bool:
    fw, fh = _footprint(footprint_um)
    return _footprint_corners_inside(
        (float(site_center_um[0]), float(site_center_um[1])),
        (float(die_center_um[0]), float(die_center_um[1])),
        float(usable_radius_um),
        fw,
        fh,
    )


def _footprint_corners_inside(site_center, die_center, usable_radius, fw, fh) -> bool:
    sx, sy = site_center
    cx, cy = die_center
    for x in (sx - fw / 2.0, sx + fw / 2.0):
        for y in (sy - fh / 2.0, sy + fh / 2.0):
            if hypot(x - cx, y - cy) > usable_radius + 1e-12:
                return False
    return True


def _axis_positions(min_v: float, max_v: float, pitch: float) -> list[float]:
    count = floor((max_v - min_v) / pitch)
    return [min_v + i * pitch for i in range(count + 1)]


def _footprint(value: float | Sequence[float]) -> tuple[float, float]:
    if isinstance(value, (int, float)):
        w = h = float(value)
    else:
        w, h = float(value[0]), float(value[1])
    if w < 0 or h < 0:
        raise ValueError("footprint_um values must be non-negative")
    return w, h


def _bbox(bbox: Sequence[float]) -> tuple[float, float, float, float]:
    if len(bbox) != 4:
        raise ValueError("region_bbox_um must be [xmin, ymin, xmax, ymax]")
    x0, y0, x1, y1 = [float(v) for v in bbox]
    if x1 < x0 or y1 < y0:
        raise ValueError("bbox max values must be >= min values")
    return x0, y0, x1, y1


def _rowcol(row: int, col: int) -> str:
    return f"R{int(row)}C{int(col)}"


def _point_in_poly(point: Sequence[float], poly: Sequence[Sequence[float]]) -> bool:
    x, y = float(point[0]), float(point[1])
    inside = False
    j = len(poly) - 1
    for i, pi in enumerate(poly):
        xi, yi = float(pi[0]), float(pi[1])
        xj, yj = float(poly[j][0]), float(poly[j][1])
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-30) + xi):
            inside = not inside
        j = i
    return inside


def _require_positive(**values: float) -> None:
    for name, value in values.items():
        if float(value) <= 0:
            raise ValueError(f"{name} must be positive")


# ---------------------------------------------------------------------------
# Pattern-based grid notation (generic engine; the pattern TEXT is always
# caller-supplied — klink hardcodes no notation).
#
#   "{row}+{col}"      -> 1+1, 1+2, ...
#   "{row}-{col}"      -> 1-3 style
#   "R{row}C{col}"     -> R1C2
#   "{row:A}{col}"     -> A1, B3, ... ({...:A} renders the value as
#                          spreadsheet letters: 1->A, 27->AA)
#   "S{index:03d}"     -> S001 (subsumes the prefix scheme)
#
# {row}/{col} are POSITIONAL (independent of which sites were accepted);
# {index} counts ACCEPTED sites in the given traversal order. Standard
# integer format specs apply ("02d" etc.); the single custom spec "A" is
# the letter conversion.
# ---------------------------------------------------------------------------

_PATTERN_FIELDS = ("row", "col", "index")


def _alpha(value: int) -> str:
    if value < 1:
        raise ValueError("letter conversion needs values >= 1 "
                         "(check the axis start)")
    out = ""
    v = value
    while v > 0:
        v, rem = divmod(v - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def _format_pattern(pattern: str, values: dict) -> str:
    import string

    out = []
    for literal, field, spec, conv in string.Formatter().parse(str(pattern)):
        out.append(literal)
        if field is None:
            continue
        if field not in _PATTERN_FIELDS:
            raise ValueError(
                f"unknown pattern field {{{field}}}; supported: "
                + ", ".join("{%s}" % f for f in _PATTERN_FIELDS))
        value = values[field]
        if spec == "A":
            out.append(_alpha(int(value)))
        else:
            out.append(format(int(value), spec or ""))
    return "".join(out)


def _axis_value(pos: int, total: int, axis: dict, reverse_token: str) -> int:
    start = int(axis.get("start", 1))
    step = int(axis.get("step", 1))
    order = str(axis.get("order", ""))
    if order == reverse_token:
        pos = (total - 1) - pos
    return start + pos * step


def pattern_site_ids(
    sites: Sequence[Site],
    *,
    pattern: str,
    rows_total: int,
    cols_total: int,
    row_axis: dict | None = None,
    col_axis: dict | None = None,
    index_axis: dict | None = None,
) -> list[Site]:
    """Assign ids from a caller-supplied grid-notation pattern.

    row_axis/col_axis: {start: 1, step: 1, order: "bottom_up"|"top_down" /
    "left_to_right"|"right_to_left"}. index_axis: {start: 1, order:
    "bottom_up"|"top_down"} — the traversal order for the {index} counter
    over the (accepted) sites, same semantics as number_sites."""
    row_axis = dict(row_axis or {})
    col_axis = dict(col_axis or {})
    index_axis = dict(index_axis or {})
    index_order = str(index_axis.get("order", "bottom_up"))
    if index_order == "top_down":
        key = lambda s: (-s.row, s.col)  # noqa: E731
    elif index_order == "bottom_up":
        key = lambda s: (s.row, s.col)  # noqa: E731
    else:
        raise ValueError(
            f"unknown index order {index_order!r}; supported: bottom_up, "
            "top_down")
    index_start = int(index_axis.get("start", 1))

    result = []
    for pos, site in enumerate(sorted(sites, key=key)):
        values = {
            "row": _axis_value(site.row, rows_total, row_axis, "top_down"),
            "col": _axis_value(site.col, cols_total, col_axis,
                               "right_to_left"),
            "index": index_start + pos,
        }
        result.append(Site(_format_pattern(pattern, values), site.row,
                           site.col, site.center_um, site.rotation_deg))
    return result

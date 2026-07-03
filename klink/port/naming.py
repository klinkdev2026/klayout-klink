"""Port naming policy.

Port names are unique handles. Connectivity belongs in ``net``.
"""

from __future__ import annotations


def direction_prefix(orientation: float) -> str:
    orient = orientation % 360.0
    if orient <= 45 or orient > 315:
        return "E"
    if 45 < orient <= 135:
        return "N"
    if 135 < orient <= 225:
        return "W"
    return "S"


def auto_name(orientation: float, existing_names: set, port_type: str = "",
              index: int = 0, style: str = "index") -> str:
    """Generate a unique port handle."""
    if style == "direction":
        prefix = direction_prefix(orientation)
    elif style == "type":
        if port_type == "optical":
            prefix = "o"
        elif port_type == "placement":
            prefix = "p"
        elif port_type == "electrical":
            prefix = "e"
        else:
            prefix = "P"
    else:
        prefix = "P"

    i = index
    while True:
        candidate = "%s%d" % (prefix, i)
        if candidate not in existing_names:
            return candidate
        i += 1

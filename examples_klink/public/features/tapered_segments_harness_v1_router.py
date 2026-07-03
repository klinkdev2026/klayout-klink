"""Route HarnessPCell_v1 through the generic tapered cell router."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from klink import KLinkClient
from klink.routing.backends.geometric.tapered_segments import route_tapered_hybrid_cell


CELL = "HarnessPCell_v1"


def main() -> None:
    with KLinkClient().connect() as client:
        result = route_tapered_hybrid_cell(client, CELL)
        client.call("view.show_cell", {"cell": CELL, "zoom_fit": False})
        print(result)
        if not result["ok"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()

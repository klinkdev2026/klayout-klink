"""Run the current client-side routing workflow on KLayout cells.

Examples:

  # Dry-run the five built-in routing examples.
  python examples_klink/21_route_run.py

  # Commit routes to 10/0 for all five examples.
  python examples_klink/21_route_run.py --commit

  # Route one hand-edited cell.
  python examples_klink/21_route_run.py --cell ROUTE_05_FANOUT --commit

  # Treat 900/0 as a keepout for this run.
  python examples_klink/21_route_run.py --cell ROUTE_04_OBSTACLE --obstacle-layer 900/0 --commit

  # Try the geometric visibility/Dijkstra router.
  python examples_klink/21_route_run.py --cell ROUTE_04_OBSTACLE --router geometric --obstacle-layer 900/0 --commit
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from klink import KLinkClient
from klink.routing.geom.run import route_cell


DEFAULT_CELLS = [
    "ROUTE_01_STRAIGHT",
    "ROUTE_02_WAYPOINT",
    "ROUTE_03_EDGE_SLIDE",
    "ROUTE_04_OBSTACLE",
    "ROUTE_05_FANOUT",
]


def print_report(report: dict) -> None:
    print("\n%s" % report["cell"])
    print("  algorithm: %s" % report["algorithm"])
    print("  backend:   %s" % report["backend"])
    print("  dry_run:   %s" % report["dry_run"])
    print("  routable:  %s" % report["routable"])
    print("  routes:    %d" % report["route_count"])
    print("  obstacles: %s" % (",".join(report["obstacle_layers"]) or "(none)"))
    print("  crossings: %d" % len(report["crossings"]))
    if report.get("self_crossings"):
        print("  self_x:    %d" % len(report["self_crossings"]))
    print("  obs_hits:  %d" % len(report["obstacle_hits"]))
    if report["errors"]:
        print("  errors:")
        for issue in report["errors"]:
            print("    - %s: %s" % (issue.get("code"), issue.get("message")))
    if report["warnings"]:
        print("  warnings:")
        for issue in report["warnings"]:
            print("    - %s: %s" % (issue.get("code"), issue.get("message")))
    for route in report["routes"]:
        print(
            "    %s: %s -> %s anchors=%s points=%d width=%.3f"
            % (
                route.get("route_id"),
                route.get("source"),
                route.get("target"),
                ",".join(str(a) for a in route.get("anchors", [])) or "-",
                len(route.get("points_um", [])),
                float(route.get("width_um", 0.0)),
            )
        )
    if report["writeback"]:
        print("  writeback: inserted=%d layer=%s" % (
            report["writeback"].get("inserted", 0),
            report["writeback"].get("route_layer", ""),
        ))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run klink routing workflow.")
    parser.add_argument("--cell", action="append", help="Cell to route. Can be repeated.")
    parser.add_argument("--commit", action="store_true", help="Write routes to route layer.")
    parser.add_argument("--route-layer", default="10/0", help="Route output layer.")
    parser.add_argument("--port-layer", default="999/99", help="Port marker layer.")
    parser.add_argument("--anchor-layer", default="999/1", help="Anchor marker layer.")
    parser.add_argument("--obstacle-layer", action="append", default=[], help="Obstacle layer for all selected cells. Can be repeated.")
    parser.add_argument("--router", choices=["semantic", "geometric"], default="semantic", help="Router backend to use.")
    parser.add_argument("--safe-distance", type=float, default=0.0, help="Extra spacing around explicit obstacles.")
    parser.add_argument("--angle-mode", choices=["manhattan", "fortyfive"], default="manhattan", help="Allowed geometric router segment angles.")
    parser.add_argument("--no-clear", action="store_true", help="Do not clear existing paths on route layer before commit.")
    args = parser.parse_args()

    cells = args.cell or DEFAULT_CELLS
    with KLinkClient().connect() as client:
        ok = True
        for cell in cells:
            obstacle_layers = list(args.obstacle_layer)
            report = route_cell(
                client,
                cell,
                port_layer=args.port_layer,
                anchor_layer=args.anchor_layer,
                obstacle_layers=obstacle_layers,
                route_layer=args.route_layer,
                dry_run=not args.commit,
                clear=not args.no_clear,
                router_backend=args.router,
                safe_distance_um=args.safe_distance,
                angle_mode=args.angle_mode,
            )
            print_report(report)
            ok = ok and bool(report["routable"])
        if cells:
            client.show_cell(cells[-1], zoom_fit=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

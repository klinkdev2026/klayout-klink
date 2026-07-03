"""Route KLayout Port markers with the optional gdsfactory backend."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from klink import KLinkClient
from klink.routing.backends.gdsfactory.gdsfactory_ports import route_gdsfactory_ports


def _active_cell(client: KLinkClient) -> str:
    info = client.layout_info()
    cell = info.get("cell")
    if not cell:
        raise SystemExit("No active KLayout cell. Pass --cell explicitly.")
    return str(cell)


def _print_report(report: dict, *, committed: bool) -> None:
    print("\n%s" % report["cell"])
    print("  backend:   %s" % report["backend"])
    print("  committed: %s" % committed)
    print("  output:    %s" % report["output_mode"])
    print("  routes:    %d" % len(report["routes"]))
    print("  crossings: %d" % len(report["crossings"]))
    for route in report["routes"]:
        print(
            "    %s: %s -> %s points=%d width=%.3f length=%.3f"
            % (
                route.get("route_id", ""),
                route.get("source", ""),
                route.get("target", ""),
                len(route.get("points_um", [])),
                float(route.get("width_um", 0.0)),
                float(route.get("length_um", 0.0)),
            )
        )
        print("      points_um=%s" % route.get("points_um", []))
    writeback = report.get("writeback")
    if writeback:
        print("  writeback: inserted=%d" % writeback.get("inserted", 0))


def main() -> int:
    parser = argparse.ArgumentParser(description="Route KLayout Port markers with gdsfactory.route_bundle.")
    # --port MUST exist even though nothing below reads it via abbreviation:
    # argparse allow_abbrev matches the unique prefix `--port` to `--port-layer`,
    # so without a real `--port` option, `--port 8766` silently becomes
    # `--port-layer 8766` and fails deep inside with "layer must be 'L/D'".
    parser.add_argument("--port", type=int, default=8765, help="KLayout session port.")
    parser.add_argument("--cell", help="Cell to route. Defaults to active KLayout cell.")
    parser.add_argument("--source", action="append", default=[], help="Source port name. Can be repeated for bundles.")
    parser.add_argument("--target", action="append", default=[], help="Target port name. Can be repeated for bundles.")
    parser.add_argument("--source-prefix", help="Select source group by port name prefix, for example IN.")
    parser.add_argument("--target-prefix", help="Select target group by port name prefix, for example OUT.")
    parser.add_argument("--source-orientation", type=float, help="Select source group by orientation, for example 0.")
    parser.add_argument("--target-orientation", type=float, help="Select target group by orientation, for example 180.")
    parser.add_argument("--net", help="Route the exactly two ports with this net.")
    parser.add_argument("--all-two-port-nets", action="store_true", help="Route every net that has exactly two ports.")
    parser.add_argument("--multidrop-net", help="Route one multi-port net as a star from --root to every other port.")
    parser.add_argument("--root", help="Root port name for --multidrop-net.")
    parser.add_argument(
        "--pair-by",
        choices=["net", "axis", "order", "name", "distance", "clockwise"],
        default="net",
        help="How to pair selected source/target groups.",
    )
    parser.add_argument("--commit", action="store_true", help="Push routes to KLayout.")
    parser.add_argument("--show", action="store_true", help="Push through gdsfactory c.show()/klive.")
    parser.add_argument("--write-paths", action="store_true", help="Write route backbones as KLayout paths.")
    parser.add_argument(
        "--router",
        choices=["bundle", "single", "sbend", "all-angle", "dubins", "electrical"],
        default="bundle",
        help="gdsfactory routing backend to use.",
    )
    parser.add_argument("--route-layer", default="10/0", help="Route output layer and gdsfactory layer.")
    parser.add_argument("--gf-route-layer", help="Layer used inside gdsfactory before remapping polygons to --route-layer.")
    parser.add_argument("--cross-section", help="gdsfactory cross_section name, for example strip or metal_routing.")
    parser.add_argument("--port-layer", default="999/99", help="Port marker layer.")
    parser.add_argument("--separation", type=float, default=3.0, help="Bundle separation in um.")
    parser.add_argument("--sort-ports", action="store_true", help="Let gdsfactory sort bundle ports.")
    parser.add_argument("--auto-taper", action="store_true", help="Let gdsfactory insert tapers for width-mismatched ports.")
    parser.add_argument("--allow-crossing", action="store_true", help="Allow routes whose backbone crossings are detected.")
    parser.add_argument("--no-clear", action="store_true", help="Do not clear existing route geometry before commit.")
    args = parser.parse_args()

    if bool(args.source) != bool(args.target):
        raise SystemExit("--source and --target must be provided together.")
    if args.source and len(args.source) != len(args.target):
        raise SystemExit("--source and --target counts must match.")
    if args.show and args.write_paths:
        raise SystemExit("--show and --write-paths are mutually exclusive.")

    if args.write_paths:
        output_mode = "klink_paths" if args.commit else "dry_run"
    elif args.show:
        output_mode = "gdsfactory_show" if args.commit else "dry_run"
    else:
        output_mode = "batch_polygons" if args.commit else "dry_run"

    with KLinkClient(port=args.port).connect() as client:
        cell = args.cell or _active_cell(client)
        try:
            report = route_gdsfactory_ports(
                client,
                cell,
                port_layer=args.port_layer,
                route_layer=args.route_layer,
                gf_route_layer=args.gf_route_layer,
                router=args.router,
                cross_section=args.cross_section,
                output_mode=output_mode,
                clear=not args.no_clear,
                allow_crossing=args.allow_crossing,
                separation_um=args.separation,
                sort_ports=args.sort_ports,
                auto_taper=args.auto_taper,
                source=args.source,
                target=args.target,
                source_prefix=args.source_prefix,
                target_prefix=args.target_prefix,
                source_orientation=args.source_orientation,
                target_orientation=args.target_orientation,
                net=args.net,
                all_two_port_nets=args.all_two_port_nets,
                multidrop_net=args.multidrop_net,
                root=args.root,
                pair_by=args.pair_by,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    _print_report(report, committed=bool(args.commit))
    if not args.commit:
        print("\nDry run only. Add --commit to draw the route.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

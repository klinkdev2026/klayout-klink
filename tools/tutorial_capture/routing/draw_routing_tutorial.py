"""Five-scenario routing tutorial capture.

Imports the shipped starter
(examples_klink/public/demos/routing/five_routers.py) and drives its
OWN build/route functions per scenario, screenshotting the routed
result of each — plus one pre-route shot of the obstacle scenario so
the tutorial can show inputs vs outcome. Pictures cannot drift from
the demo: the geometry comes from the starter's code, not a copy.

Owns its tab lifecycle like every capture script.
"""

import argparse
import base64
import importlib.util
from pathlib import Path

from klink import KLinkClient

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO_ROOT / "test_outputs" / "tutorial_capture" / "routing"

_spec = importlib.util.spec_from_file_location(
    "five_routers",
    REPO_ROOT / "examples_klink" / "public" / "demos" / "routing"
    / "five_routers.py")
F = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(F)

BUILDERS = [
    ("step-01-straight", F.build_01_straight),
    ("step-02-waypoint", F.build_02_waypoint),
    ("step-03-edge-slide", F.build_03_edge_slide),
    ("step-04-obstacle", F.build_04_obstacle),
    ("step-05-fanout", F.build_05_fanout),
]


def snap(c, out_dir: Path, name: str, cell: str) -> None:
    c.call("view.show_cell", {"cell": cell})
    c.call("view.hier_levels", {"min": 0, "max": 12})
    c.zoom_fit()
    shot = c.screenshot(mode="base64", width_px=1400)
    data = shot["data_url"].split(",", 1)[1]
    (out_dir / name).write_bytes(base64.b64decode(data))
    print("  shot ->", name)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    with KLinkClient(port=args.port).connect() as c:
        prev = c.call("view.list_tabs", {}).get("current_index", -1)
        c.new_tab(cell_name="ROUTING_TUTORIAL")
        try:
            F.ensure_layers(c)
            problems: list = []
            for stem, builder in BUILDERS:
                scene = builder(c)
                if stem == "step-04-obstacle":
                    snap(c, out_dir, "step-04a-obstacle-inputs.png",
                         scene["cell"])
                routed = F.route_scene(c, scene)
                ok = F.verify_scene(scene, routed, problems)
                print("  %s ok=%s" % (scene["cell"], ok))
                snap(c, out_dir, stem + "-routed.png", scene["cell"])
            if problems:
                print("CAPTURE INVALID - scenarios failed:", problems)
                return 1
        finally:
            try:
                c.call("view.close_tab", {})
                if prev is not None and int(prev) >= 0:
                    c.call("view.activate_tab", {"index": int(prev)})
            except Exception:
                pass
    print("capture complete ->", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Import-closure guard: runtime closure must stay == functional closure.

The decoupling invariant (docs/DEMO_DEPENDENCY_MAP.md): importing a single
routing backend or EBL helper must NOT drag sibling backends, the gdsfactory
bridge, or heavy third-party packages. Each case runs in a clean subprocess so
sys.modules reflects exactly what that one import pulls.

If one of these fails after a change, an eager `from .x import y` (or a
top-level heavy import) crept back into a package __init__ or a shared module.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _loaded_after(snippet: str) -> set[str]:
    code = (
        "import sys, json\n"
        f"sys.path.insert(0, {ROOT!r})\n"
        f"{snippet}\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    out = subprocess.check_output([sys.executable, "-c", code], text=True)
    return set(json.loads(out.strip().splitlines()[-1]))


# (snippet, modules that must NOT be loaded by it)
CASES = {
    "flexdr": (
        "import klink.routing.backends.flexdr.flexdr",
        ("gdsfactory", "cv2", "numpy", "scipy",
         "klink.routing.backends.geometric.damped", "klink.routing.backends.gdsfactory.gdsfactory_ports",
         "klink.routing.backends.gdsfactory.gdsfactory_backend", "klink.routing.backends.geometric.steiner",
         "klink.routing.backends.geometric.global_channel", "klink.routing.backends.geometric.multilayer",
         "klink.routing.backends.geometric.tapered", "klink.routing.backends.geometric.tapered_segments"),
    ),
    "tapered_segments": (
        "import klink.routing.backends.geometric.tapered_segments",
        ("gdsfactory", "cv2", "numpy", "scipy",
         "klink.routing.backends.flexdr.flexdr", "klink.routing.backends.geometric.damped",
         "klink.routing.backends.gdsfactory.gdsfactory_ports", "klink.routing.backends.gdsfactory.gdsfactory_backend",
         "klink.routing.backends.geometric.global_channel", "klink.routing.backends.geometric.multilayer"),
    ),
    "gdsfactory_ports": (
        "import klink.routing.backends.gdsfactory.gdsfactory_ports",
        # gdsfactory itself is call-time lazy: importing the bridge must not load it
        ("gdsfactory", "cv2",
         "klink.routing.backends.flexdr.flexdr", "klink.routing.backends.geometric.damped",
         "klink.routing.backends.geometric.steiner", "klink.routing.backends.geometric.global_channel",
         "klink.routing.backends.geometric.tapered_segments"),
    ),
    "ebl_wraparound": (
        "from klink.domains.nanodevice.devices.wraparound import build_wraparound_demo",
        ("gdsfactory", "cv2", "numpy", "scipy",
         "klink.domains.nanodevice.orchestrators",
         "klink.domains.nanodevice.pipeline",
         "klink.domains.nanodevice.flake.klayoutclaw",
         "klink.domains.nanodevice.flake.detect"),
    ),
    # new layered paths: a backend group must not drag a sibling group
    "flexdr_newpath": (
        "import klink.routing.backends.flexdr.flexdr",
        ("gdsfactory", "cv2", "numpy", "scipy",
         "klink.routing.backends.geometric.damped",
         "klink.routing.backends.geometric.tapered",
         "klink.routing.backends.gdsfactory.gdsfactory_ports",
         "klink.routing.backends.negotiated.negotiated"),
    ),
    "gdsfactory_newpath": (
        "import klink.routing.backends.gdsfactory.gdsfactory_ports",
        ("gdsfactory", "cv2",
         "klink.routing.backends.flexdr.flexdr",
         "klink.routing.backends.geometric.damped",
         "klink.routing.backends.negotiated.negotiated"),
    ),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_import_does_not_drag_siblings(name: str) -> None:
    snippet, forbidden = CASES[name]
    loaded = _loaded_after(snippet)
    leaked = sorted(m for m in forbidden if m in loaded)
    assert not leaked, f"{snippet!r} unexpectedly loaded: {leaked}"


def test_routing_package_backcompat_names_lazy() -> None:
    # Package-level names still resolve (PEP 562), and accessing the gdsfactory
    # bridge name must NOT drag flexdr/damped.
    loaded = _loaded_after(
        "import klink.routing as r\n"
        "assert callable(r.route_gdsfactory_ports)\n"
        "assert callable(r.route_tapered_hybrid_many)\n"
    )
    assert "klink.routing.backends.gdsfactory.gdsfactory_ports" in loaded  # accessed -> loaded
    assert "klink.routing.backends.flexdr.flexdr" not in loaded
    assert "klink.routing.backends.geometric.damped" not in loaded
    assert "gdsfactory" not in loaded  # still call-time lazy


def test_nanodevice_package_backcompat_names_lazy() -> None:
    loaded = _loaded_after(
        "import klink.domains.nanodevice as nd\n"
        "assert callable(nd.plan_writefields)\n"
        "assert callable(nd.build_hallbar)\n"
        "assert callable(nd.generate_wf_patches)\n"
    )
    # touching only EBL/device names must not load the flake pipeline
    assert "klink.domains.nanodevice.flake.klayoutclaw" not in loaded
    assert "cv2" not in loaded

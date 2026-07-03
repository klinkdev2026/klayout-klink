"""Nanodevice and EBL helpers for klink — lazy re-exports (PEP 562).

This package is importable with the standard library only (numpy/opencv are
loaded lazily), and importing a single EBL helper (e.g.
``klink.domains.nanodevice.devices.wraparound``) must not drag the opencv-backed
``flake`` pipeline. The public names below stay
available as package attributes but import their owning submodule only on first
access — so the heavy ``orchestrators`` / ``pipeline`` / ``flake`` chain loads
only when one of its names is actually used.

See ``docs/DEMO_DEPENDENCY_MAP.md`` and ``tests/unit/test_import_closure.py``.
"""

from importlib import import_module

# Public name -> owning submodule (dotted, relative to this package).
_EXPORTS: dict[str, tuple[str, ...]] = {
    "devices.hallbar": ("HallBarSpec", "build_hallbar"),
    "devices.wraparound": ("build_wraparound_demo",),
    "ebl.marks": ("alignment_cross_items", "corner_alignment_marks"),
    "ebl.patching": ("generate_wf_patches",),
    "ebl.validation": (
        "validate_route_centerline_overlaps", "validate_writefield_crossings",
    ),
    "ebl.writefield": ("CrossingWindow", "WritefieldPlan", "plan_writefields"),
    "orchestrators": ("build_and_route_hallbar", "detect_and_commit"),
    "pipeline": ("build_hallbar_bundle", "route_hallbar_offline"),
}

_NAME_TO_MOD = {name: mod for mod, names in _EXPORTS.items() for name in names}
__all__ = sorted(_NAME_TO_MOD)


def __getattr__(name: str):
    mod = _NAME_TO_MOD.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{mod}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

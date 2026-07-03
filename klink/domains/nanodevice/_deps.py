"""Optional dependency loaders for the nanodevice domain.

These keep the nanodevice package importable with the standard library only.
The heavy scientific libraries are loaded lazily and, when absent, raise a
capability error naming the exact ``pip install`` command. klink never installs
these for you; bring your own when a feature needs them.
"""

from __future__ import annotations

import sys


def _capability_error(package: str, purpose: str, install: str = "") -> RuntimeError:
    # Name the EXACT interpreter so the user installs into the one running klink
    # (venvs / multiple Pythons make a bare "pip install" easy to get wrong).
    return RuntimeError(
        f"nanodevice {purpose} requires the optional package {package!r}. "
        f"Install it into THIS interpreter: "
        f'"{sys.executable}" -m pip install {install or package}'
    )


def load_np():
    """Return numpy, raising a capability error when it is missing."""

    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise _capability_error("numpy", "array processing") from exc
    return np


def load_cv():
    """Return cv2, raising a capability error when it is missing."""

    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise _capability_error("opencv-python-headless", "flake detection") from exc
    return cv2


def load_kdb():
    """Return klayout.db for optional GDS/OAS file adapters."""

    try:
        import klayout.db as kdb
    except ImportError:
        try:
            import pya as kdb
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise _capability_error("klayout", "GDS/OAS file reading") from exc
    return kdb

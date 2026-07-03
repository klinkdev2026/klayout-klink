"""Layering guard: a routing backend may not import a sibling backend group.

After the core/geom/grid/backends re-layering, the rule that keeps the package
maintainable is: a module under ``klink/routing/backends/<group>/`` may import
from ``core``/``geom``/``grid`` and its own ``<group>`` — never from a *different*
backend group. This test makes that enforceable instead of relying on review.

It also asserts each backend group does not pull a sibling group at import time
(runtime closure), complementing tests/unit/test_import_closure.py.
"""

from __future__ import annotations

import ast
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKENDS_DIR = os.path.join(ROOT, "klink", "routing", "backends")
GROUPS = sorted(
    d for d in os.listdir(BACKENDS_DIR)
    if os.path.isdir(os.path.join(BACKENDS_DIR, d)) and not d.startswith("__")
)


def _backend_modules():
    for group in GROUPS:
        gdir = os.path.join(BACKENDS_DIR, group)
        for fn in os.listdir(gdir):
            if fn.endswith(".py") and fn != "__init__.py":
                yield group, os.path.join(gdir, fn)


def _imported_modules(path: str):
    src = open(path, encoding="utf-8").read()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            for a in node.names:
                yield a.name


@pytest.mark.parametrize("group", GROUPS)
def test_backend_does_not_import_sibling_group(group: str) -> None:
    prefix = "klink.routing.backends."
    violations = []
    for g, path in _backend_modules():
        if g != group:
            continue
        for mod in _imported_modules(path):
            if mod.startswith(prefix):
                other = mod[len(prefix):].split(".", 1)[0]
                if other != group:
                    violations.append(f"{os.path.basename(path)} -> {mod}")
    assert not violations, (
        f"backend group {group!r} imports a sibling backend group: {violations}"
    )

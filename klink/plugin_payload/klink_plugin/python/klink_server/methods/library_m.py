"""
Library methods.

`library.list`          : enumerate all registered KLayout libraries
`library.refresh`       : re-evaluate library content in all layouts using it
`library.register_file` : register a GDS/OASIS file as a runtime library

Rationale
---------
KLayout libraries (Basic, salt-installed PDKs, runtime-registered device
libraries) are the source of PCells and reusable cells, but until now the
plugin only exposed `pcell.libraries` (names only) and users had to press
the GUI refresh button after a library changed on disk or a PCell was
re-registered.  `library.refresh` wraps the official
`pya.Library#refresh` / `refresh_all` ("updates all layouts using this
library"), so an agent can complete a register -> refresh -> place loop
without manual GUI steps.

`library.register_file` turns an ordinary layout file into a registered
library at runtime.  After that, `instance.insert` / `instance.insert_many`
can place its cells by name through the normal library mechanism instead of
first copying cell trees into the active layout.
"""

from __future__ import annotations

import os

import pya

from ..registry import method
from ..errors import RpcError, ErrorCode

# name -> source file path, provenance for libraries registered through
# `library.register_file` in this KLayout session.  Keep a strong Python
# reference to each Library object: KLayout does not own the Python wrapper,
# and letting it be garbage-collected can drop the library.
_FILE_LIBS: dict = {}
_FILE_LIB_REFS: dict = {}


def _iter_libraries():
    """Yield (name, Library) for every registered library."""
    try:
        ids = list(pya.Library.library_ids())
    except Exception:
        ids = []
    for lid in ids:
        try:
            lib = pya.Library.library_by_id(lid)
        except Exception:
            lib = None
        if lib is None:
            continue
        try:
            name = lib.name()
        except Exception:
            name = None
        if name:
            yield name, lib


def _library_names() -> list:
    return [name for name, _lib in _iter_libraries()]


def _lib_summary(name: str, lib) -> dict:
    entry = {"name": name}
    try:
        entry["id"] = int(lib.id())
    except Exception:
        pass
    try:
        desc = lib.description
        if desc:
            entry["description"] = str(desc)
    except Exception:
        pass
    try:
        entry["technologies"] = [str(t) for t in lib.technologies()]
    except Exception:
        entry["technologies"] = []
    try:
        layout = lib.layout()
        entry["cell_count"] = int(layout.cells())
        try:
            # PCell-only libraries (e.g. Basic) have declarations but no
            # concrete cells until a variant is instantiated, so cell_count
            # 0 is normal there. pcell_names() is the safe modern API;
            # pcell_ids() can deadlock on Python-registered libraries.
            entry["pcell_count"] = len(list(layout.pcell_names()))
        except Exception:
            pass
        tops = []
        for c in layout.top_cells()[:20]:
            try:
                tops.append(layout.cell_name(c.cell_index()))
            except Exception:
                pass
        entry["top_cells"] = tops
    except Exception:
        pass
    if name in _FILE_LIBS:
        entry["source_file"] = _FILE_LIBS[name]
    return entry


@method(
    "library.list",
    description=(
        "List all libraries registered in this KLayout process (Basic, "
        "salt/PDK libraries, runtime-registered device or file libraries). "
        "Each entry has `name`, `id`, `description`, `technologies`, "
        "`cell_count`, `pcell_count` and up to 20 `top_cells` (PCell-only "
        "libraries like Basic legitimately have cell_count 0); libraries "
        "created by "
        "`library.register_file` also carry `source_file`. Use "
        "`pcell.list`/`pcell.info` to inspect PCells inside a library."
    ),
    params_schema={"type": "object", "additionalProperties": False},
    returns_schema={
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "libraries": {"type": "array", "items": {"type": "object"}},
        },
    },
    tags=["library", "read"],
)
def library_list(params, ctx):
    libs = [_lib_summary(name, lib) for name, lib in _iter_libraries()]
    return {"count": len(libs), "libraries": libs}


@method(
    "library.refresh",
    description=(
        "Re-evaluate library content in every layout that uses it (official "
        "pya.Library refresh). Pass `library` to refresh one library by "
        "name, or omit it to refresh ALL registered libraries. Call this "
        "after a library changed (e.g. a PCell was re-registered or a salt "
        "library was updated) instead of asking the user to press the GUI "
        "refresh button. Read-only layouts are left untouched by KLayout "
        "itself."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "library": {
                "type": "string",
                "description": "Library name; omit to refresh all libraries.",
            },
        },
        "additionalProperties": False,
    },
    returns_schema={
        "type": "object",
        "properties": {
            "refreshed": {"type": "array", "items": {"type": "string"}},
            "scope": {"type": "string", "enum": ["one", "all"]},
        },
    },
    mutates=True,
    tags=["library", "write"],
)
def library_refresh(params, ctx):
    name = params.get("library")
    if name is None:
        try:
            pya.Library.refresh_all()
        except Exception as exc:
            raise RpcError(ErrorCode.INTERNAL, "refresh_all failed: %s" % (exc,))
        return {"refreshed": _library_names(), "scope": "all"}

    name = str(name)
    lib = pya.Library.library_by_name(name)
    if lib is None:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "library %r is not registered. Registered libraries: %s. "
            "Call library.list for details, or library.register_file to "
            "register a layout file as a library." % (name, _library_names()),
        )
    try:
        lib.refresh()
    except Exception as exc:
        raise RpcError(ErrorCode.INTERNAL, "refresh of %r failed: %s" % (name, exc))
    return {"refreshed": [name], "scope": "one"}


@method(
    "library.register_file",
    description=(
        "Register a layout file (GDS/OASIS/anything KLayout reads) as a "
        "runtime library, so its cells become placeable by name via "
        "instance.insert / instance.insert_many with `library` set. "
        "`name` defaults to the file stem. The library lives for this "
        "KLayout session only (re-register after restart). Refuses a name "
        "that is already registered — pick a new name; there is no "
        "in-session replace."
    ),
    params_schema={
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string", "description": "Layout file to read."},
            "name": {
                "type": "string",
                "description": "Library name (default: file stem).",
            },
            "technology": {
                "type": "string",
                "description": "Optional technology to associate with.",
            },
            "description": {"type": "string"},
        },
        "additionalProperties": False,
    },
    returns_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "cell_count": {"type": "integer"},
            "top_cells": {"type": "array", "items": {"type": "string"}},
            "path": {"type": "string"},
        },
    },
    mutates=True,
    tags=["library", "write"],
)
def library_register_file(params, ctx):
    path = str(params.get("path") or "")
    if not path or not os.path.isfile(path):
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "path %r does not exist or is not a file; pass an absolute path "
            "to a layout file readable by KLayout (GDS/OASIS/...)." % (path,),
        )
    name = str(params.get("name") or "").strip()
    if not name:
        name = os.path.splitext(os.path.basename(path))[0]
    if pya.Library.library_by_name(name) is not None:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "library name %r is already registered in this KLayout session "
            "(library.list shows all). Pick a different `name`; replacing a "
            "registered library in-session is not supported — restart "
            "KLayout to drop runtime libraries." % (name,),
        )

    lib = pya.Library()
    try:
        lib.description = str(
            params.get("description") or ("klink file library: %s" % path))
    except Exception:
        pass
    try:
        lib.layout().read(path)
    except Exception as exc:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "KLayout could not read %r as a layout file: %s" % (path, exc),
        )
    tech = params.get("technology")
    if tech is not None:
        try:
            lib.technology = str(tech)
        except Exception as exc:
            raise RpcError(
                ErrorCode.BAD_PARAMS,
                "cannot associate technology %r: %s" % (tech, exc),
            )
    lib.register(name)
    _FILE_LIBS[name] = path
    _FILE_LIB_REFS[name] = lib

    layout = lib.layout()
    tops = []
    try:
        for c in layout.top_cells()[:20]:
            tops.append(layout.cell_name(c.cell_index()))
    except Exception:
        pass
    return {
        "name": name,
        "cell_count": int(layout.cells()),
        "top_cells": tops,
        "path": path,
    }

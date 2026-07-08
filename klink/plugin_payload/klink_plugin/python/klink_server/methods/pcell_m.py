"""
PCell introspection methods (M3 Round 4, read-only).

`pcell.libraries` - list available libraries (Basic, your PDK, ...)
`pcell.list`      - list PCells in a given library
`pcell.info`      - describe the parameters of a specific PCell

Rationale
---------
`instance.insert_pcell` needs a parameter dict that matches the PCell's
declaration. The parameter names, types and defaults differ per library
and per KLayout version. Rather than hard-code any of this in the
client, we reflect it at runtime: an LLM agent calls pcell.info first,
sees {"name": "l", "type": "layer", ...}, and then knows exactly which
keys to send.
"""

from __future__ import annotations

from typing import Optional

import pya

from ..registry import method
from ..errors import RpcError, ErrorCode


# KLayout's PCellParameterDeclaration.type_* are integer constants.
# Build the reverse map once so we can turn them into stable strings.
def _pcell_type_names() -> dict:
    m: dict = {}
    try:
        pairs = [
            ("int",     pya.PCellParameterDeclaration.TypeInt),
            ("double",  pya.PCellParameterDeclaration.TypeDouble),
            ("string",  pya.PCellParameterDeclaration.TypeString),
            ("boolean", pya.PCellParameterDeclaration.TypeBoolean),
            ("layer",   pya.PCellParameterDeclaration.TypeLayer),
            ("list",    pya.PCellParameterDeclaration.TypeList),
            ("shape",   pya.PCellParameterDeclaration.TypeShape),
            ("none",    pya.PCellParameterDeclaration.TypeNone),
        ]
    except Exception:
        return m
    for name, val in pairs:
        try:
            m[int(val)] = name
        except Exception:
            pass
    return m


_TYPE_NAMES = _pcell_type_names()


def _json_safe_value(v):
    """Convert a pya parameter default into a JSON-safe representation."""
    if v is None:
        return None
    if isinstance(v, (bool, int, float, str)):
        return v
    # LayerInfo -> {"layer": L, "datatype": D, "name": ...}
    try:
        if isinstance(v, pya.LayerInfo):
            return {
                "layer": int(v.layer),
                "datatype": int(v.datatype),
                "name": v.name or None,
            }
    except Exception:
        pass
    # Lists / tuples
    if isinstance(v, (list, tuple)):
        return [_json_safe_value(x) for x in v]
    # Fallback to str()
    try:
        return str(v)
    except Exception:
        return None


def _param_to_dict(p) -> dict:
    """Serialise a PCellParameterDeclaration conservatively.

    We only touch attributes that are pure C++ (name/type/default). The
    Ruby-side optional attrs (choices/unit/readonly/hidden) have been
    observed to hang the Python->Ruby bridge when the PCell is defined
    in Ruby (notably Basic.*). If an LLM actually needs those, we can
    add a `deep=true` parameter later.
    """
    out: dict = {}
    try:
        n = p.name
        if n:
            out["name"] = str(n)
    except Exception:
        pass
    try:
        t_int = int(p.type)
        out["type"] = _TYPE_NAMES.get(t_int, f"type_{t_int}")
    except Exception:
        pass
    try:
        out["default"] = _json_safe_value(p.default)
    except Exception:
        pass
    try:
        d = p.description
        if d:
            out["description"] = str(d)
    except Exception:
        pass
    return out


def _resolve_library(lib_name: str) -> pya.Library:
    lib = pya.Library.library_by_name(lib_name)
    # KLayout ≥0.27: library_by_name(name) only finds libraries NOT bound
    # to a technology.  SiEPIC and other PDK libraries are technology-bound
    # and will be missed.  Fall back to iterating by ID and matching name.
    # https://github.com/KLayout/klayout/issues/879
    if lib is None:
        try:
            for lid in pya.Library.library_ids():
                try:
                    candidate = pya.Library.library_by_id(lid)
                    if candidate is not None and candidate.name() == lib_name:
                        lib = candidate
                        break
                except Exception:
                    pass
        except Exception:
            pass
        if lib is None:
            try:
                names = list(pya.Library.library_names())
            except Exception:
                names = []
            raise RpcError(
                ErrorCode.NOT_FOUND,
                f"no library named {lib_name!r}",
                hint=f"known libraries: {names}",
            )
    return lib


@method(
    "pcell.libraries",
    description=(
        "List available KLayout PCell libraries (Basic is always there; "
        "PDKs register their own). Use `pcell.list` next to enumerate "
        "PCells in a specific library."
    ),
    params_schema={"type": "object"},
    returns_schema={
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "libraries": {"type": "array", "items": {"type": "string"}},
        },
    },
    tags=["pcell", "read"],
)
def pcell_libraries(params, ctx):
    try:
        names = list(pya.Library.library_names())
    except Exception as e:
        raise RpcError(ErrorCode.INTERNAL, f"cannot enumerate libraries: {e}")
    return {"count": len(names), "libraries": sorted(names)}


@method(
    "pcell.list",
    description=(
        "List all PCells in `library` (default 'Basic'). Returned "
        "entries are just names - call `pcell.info` for parameter "
        "details on a specific one."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "library": {"type": "string", "default": "Basic"},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "library": {"type": "string"},
            "count": {"type": "integer"},
            "pcells": {"type": "array", "items": {"type": "string"}},
        },
    },
    tags=["pcell", "read"],
)
def pcell_list(params, ctx):
    lib_name = params.get("library", "Basic")
    lib = _resolve_library(lib_name)

    ly = lib.layout()
    names = []
    # pcell_names() is the safe modern API (KLayout >= 0.28).  Use it
    # first because pcell_ids() is known to hang on Python-registered
    # libraries (likely a GIL/mutex deadlock in the pya bridge).
    try:
        names = list(ly.pcell_names())
    except Exception:
        # pcell_ids() fallback — works for built-in (Basic) but may hang
        # for Python-registered libraries.
        try:
            for pid in ly.pcell_ids():
                try:
                    decl = ly.pcell_declaration(pid)
                    if decl is not None and decl.name:
                        names.append(decl.name)
                except Exception:
                    pass
        except Exception:
            # Last resort: iterate cells and probe for PCell declarations.
            try:
                for c in ly.each_cell():
                    try:
                        decl = ly.pcell_declaration(c.cell_index())
                        if decl is not None and decl.name:
                            names.append(decl.name)
                    except Exception:
                        pass
            except Exception:
                pass

    return {
        "library": lib_name,
        "count": len(names),
        "pcells": sorted(set(names)),
    }


@method(
    "pcell.info",
    description=(
        "Describe the parameters of one PCell so the caller can build "
        "a valid `params` dict for `instance.insert_pcell`. Each entry "
        "reports {name, type, default, description, choices?}. 'type' "
        "is one of: int, double, string, boolean, layer, list, shape, "
        "none."
    ),
    params_schema={
        "type": "object",
        "required": ["pcell"],
        "properties": {
            "library": {"type": "string", "default": "Basic"},
            "pcell":   {"type": "string"},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "library": {"type": "string"},
            "pcell":   {"type": "string"},
            "description": {"type": ["string", "null"]},
            "params":  {"type": "array"},
        },
    },
    tags=["pcell", "read"],
)
def pcell_info(params, ctx):
    lib_name = params.get("library", "Basic")
    pcell_name = params.get("pcell")
    if not isinstance(pcell_name, str) or not pcell_name:
        raise RpcError(ErrorCode.BAD_PARAMS, "'pcell' is required")
    lib = _resolve_library(lib_name)
    ly = lib.layout()

    # Community-recommended path: look up by name directly. This
    # matches the community examples on klayout.de/forum and is the
    # most widely tested code path in pya.
    decl = None
    try:
        decl = ly.pcell_declaration(pcell_name)
    except Exception:
        decl = None
    if decl is None:
        # Fallback: lookup by id.
        try:
            cand = ly.pcell_id(pcell_name)
            if cand is not None and int(cand) >= 0:
                decl = ly.pcell_declaration(int(cand))
        except Exception:
            pass
    if decl is None:
        raise RpcError(
            ErrorCode.NOT_FOUND,
            f"no pcell named {pcell_name!r} in library {lib_name!r}",
            hint="call pcell.list to see available PCells",
        )

    try:
        decls = list(decl.get_parameters())
    except Exception as e:
        raise RpcError(ErrorCode.INTERNAL, f"cannot read pcell parameters: {e}")

    out = []
    for p in decls:
        try:
            out.append(_param_to_dict(p))
        except Exception:
            # Never hang the RPC on a single bad param entry.
            out.append({"name": "?", "type": "unknown"})

    return {
        "library": lib_name,
        "pcell": pcell_name,
        "params": out,
    }


@method(
    "pcell.register_fitted",
    description=(
        "Register a fitted-device PCell at runtime from a fit table "
        "(format klink_transistor_pcell_fit_v1, produced by the klink "
        "fitter from a user exemplar family). The plugin ships only the "
        "generic machinery; device definitions come from outside via "
        "this call - a new device family needs zero plugin changes and "
        "zero reloads. The PCell lands in library 'klink_structdevice' "
        "and is immediately instantiable via instance.insert_pcell."
    ),
    params_schema={
        "type": "object",
        "required": ["name", "fit_table"],
        "properties": {
            "name": {
                "type": "string",
                "description": "PCell name (simple identifier, unique per session).",
            },
            "fit_table": {
                "type": "string",
                "description": "Absolute path to the fit table JSON.",
            },
        },
    },
    returns_schema={"type": "object"},
    mutates=False,
    tags=["pcell", "write"],
)
def pcell_register_fitted(params, ctx):
    from ..structdevice_pcell import register_fitted_device

    try:
        return register_fitted_device(str(params["name"]),
                                      str(params["fit_table"]))
    except Exception as e:
        raise RpcError(ErrorCode.BAD_PARAMS, str(e))

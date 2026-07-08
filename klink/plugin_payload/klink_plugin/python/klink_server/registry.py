"""
Method registry.

All RPC methods are registered via the `@method` decorator. Each entry
carries enough metadata (description, JSON schemas) to be auto-exposed
through `meta.methods`, which in turn feeds LLM tool/function-calling
layers downstream.

Design notes:
- Method names are lowercase `namespace.verb`, stable across versions
- `description` is written in plain English, LLM-readable (treat it as a
  tool docstring in function-calling schemas)
- `params_schema` / `returns_schema` are JSON Schema objects; None means
  "not yet documented"
- `mutates` flags write operations (reserved for future permission gates)
- `long_running` signals the dispatcher that this method should prefer
  the Job engine (M5) instead of blocking the connection
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass
class MethodSpec:
    name: str
    handler: Callable
    description: str = ""
    params_schema: Optional[dict] = None
    returns_schema: Optional[dict] = None
    mutates: bool = False
    long_running: bool = False
    tags: List[str] = field(default_factory=list)

    def to_public_dict(self) -> dict:
        d = {"name": self.name, "description": self.description}
        if self.params_schema is not None:
            d["params"] = self.params_schema
        if self.returns_schema is not None:
            d["returns"] = self.returns_schema
        if self.mutates:
            d["mutates"] = True
        if self.long_running:
            d["long_running"] = True
        if self.tags:
            d["tags"] = list(self.tags)
        return d


_REGISTRY: Dict[str, MethodSpec] = {}


def method(
    name: str,
    description: str = "",
    params_schema: Optional[dict] = None,
    returns_schema: Optional[dict] = None,
    mutates: bool = False,
    long_running: bool = False,
    tags: Optional[List[str]] = None,
):
    def decorator(fn: Callable) -> Callable:
        spec = MethodSpec(
            name=name,
            handler=fn,
            description=description,
            params_schema=params_schema,
            returns_schema=returns_schema,
            mutates=mutates,
            long_running=long_running,
            tags=tags or [],
        )
        if name in _REGISTRY:
            raise RuntimeError(f"duplicate klink method: {name}")
        _REGISTRY[name] = spec
        return fn

    return decorator


def get(name: str) -> Optional[MethodSpec]:
    return _REGISTRY.get(name)


def all_specs() -> Dict[str, MethodSpec]:
    return dict(_REGISTRY)


def reset_for_reload():
    """Clear the registry. Used when the package is reloaded to avoid
    'duplicate method' errors on Macro IDE re-runs."""
    _REGISTRY.clear()

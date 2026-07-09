"""klink.plugins — the third-party extension point (setuptools entry points).

A separate pip package extends klink WITHOUT modifying it: it declares an
entry point in the ``klink.plugins`` group and klink discovers it at MCP
startup / first use:

    # the extension package's pyproject.toml
    [project.entry-points."klink.plugins"]
    my_pdk = "my_pdk.klink_ext:register"

    # my_pdk/klink_ext.py
    def register(hook):
        hook.add_domain("my_pdk", title="My PDK",
                        summary="process data + helpers for my_pdk")
        hook.add_tool("my_pdk.hello", handler,
                      description="...", input_schema={"type": "object"},
                      domain="my_pdk")
        hook.add_profile("my_pdk_2m", MY_PROFILE)      # named ProcessProfile
        hook.add_resource("recipe", "my_pdk_terms", MY_RECIPE)

Design contract (mirrors docs/AGENT_TOOL_DESIGN.md and process purity):

* klink stays MECHANISM; extension packages carry the process data. Named
  resources (profiles, device libraries, recipes, stacks) are how examples
  and tools resolve a vendor's data by name.
* Discovery is LAZY (first use) and FAULT-ISOLATED: a broken extension
  package degrades to a recorded failure naming the package — never a
  crashed MCP server, never a masked exception. Failures are reported by
  ``klink.status`` and readable via :func:`failures`.
* Zero installed extensions == zero behavior change: the tool list is
  byte-identical and no third-party code is imported.
* Contributed tool handlers follow the local-tool contract
  ``handler(ctx, arguments) -> JSON-serializable dict`` (``ctx`` is the MCP
  bridge); the MCP layer wraps results/errors, so a raising handler yields
  an instructive error naming the owning package.

Tool names must be namespaced ("<token>.<name>") and must not collide with
built-in tools or plugin RPC prefixes; violations are recorded as that
package's failure and the offending tool is skipped.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

ENTRY_POINT_GROUP = "klink.plugins"

#: kinds accepted by add_resource (sugar helpers exist for the common ones)
RESOURCE_KINDS = ("profile", "devices", "recipe", "stack")

# Built-in first name segments an extension may never claim for its tools.
_RESERVED_PREFIXES = {
    "klink", "meta", "hello", "layout", "cell", "layer", "shape", "instance",
    "pcell", "selection", "edit", "view", "events", "exec", "drc", "lvs",
    "recorder", "port", "anchor", "transfer", "session", "interaction",
    "routing", "nanodevice", "photonics", "structdevice", "fabrication",
    "measurement",
}


@dataclass(frozen=True)
class ExtTool:
    name: str
    description: str
    input_schema: dict
    handler: Callable
    domain: str
    package: str

    def to_mcp_tool(self) -> dict:
        return {"name": self.name, "description": self.description,
                "inputSchema": self.input_schema}


@dataclass
class ExtRegistry:
    tools: Dict[str, ExtTool] = field(default_factory=dict)
    domains: Dict[str, dict] = field(default_factory=dict)   # token -> meta
    resources: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    plugins: List[dict] = field(default_factory=list)        # ok packages
    failures: List[dict] = field(default_factory=list)       # broken packages

    def fail(self, package: str, error: str) -> None:
        self.failures.append({"package": package, "error": error})


class PluginHook:
    """The object handed to each extension's ``register(hook)``."""

    def __init__(self, registry: ExtRegistry, package: str):
        self._reg = registry
        self._package = package
        self.record = {"package": package, "tools": [], "domains": [],
                       "resources": []}

    # -- tools -------------------------------------------------------------
    def add_tool(self, name: str, handler: Callable, *, description: str,
                 input_schema: Optional[dict] = None,
                 domain: str = "") -> None:
        name = str(name)
        if "." not in name:
            raise ValueError(
                f"tool {name!r} must be namespaced '<token>.<n>' so it "
                "cannot shadow built-ins")
        prefix = name.split(".", 1)[0]
        if prefix in _RESERVED_PREFIXES:
            raise ValueError(
                f"tool {name!r} claims reserved prefix {prefix!r}; use your "
                "extension's own token")
        if name in self._reg.tools:
            raise ValueError(
                f"tool {name!r} already registered by "
                f"{self._reg.tools[name].package!r}")
        if not callable(handler):
            raise ValueError(f"tool {name!r} handler is not callable")
        self._reg.tools[name] = ExtTool(
            name=name, description=str(description),
            input_schema=dict(input_schema or {"type": "object"}),
            handler=handler,
            domain=str(domain or prefix), package=self._package)
        self.record["tools"].append(name)

    # -- find_tools domains --------------------------------------------------
    def add_domain(self, token: str, *, title: str, summary: str,
                   usage: str = "") -> None:
        token = str(token)
        if token in self._reg.domains:
            raise ValueError(f"domain {token!r} already registered")
        self._reg.domains[token] = {
            "title": str(title), "summary": str(summary),
            "usage": str(usage), "prefixes": [token],
            "package": self._package,
        }
        self.record["domains"].append(token)

    # -- named resources -----------------------------------------------------
    def add_resource(self, kind: str, name: str, obj: Any) -> None:
        kind = str(kind)
        if kind not in RESOURCE_KINDS:
            raise ValueError(
                f"resource kind {kind!r} unknown; use one of {RESOURCE_KINDS}")
        bucket = self._reg.resources.setdefault(kind, {})
        if name in bucket:
            raise ValueError(f"{kind} resource {name!r} already registered")
        bucket[str(name)] = obj
        self.record["resources"].append(f"{kind}:{name}")

    def add_profile(self, name: str, profile: Any) -> None:
        self.add_resource("profile", name, profile)

    def add_devices(self, name: str, library: Any) -> None:
        self.add_resource("devices", name, library)

    def add_recipe(self, name: str, recipe: Any) -> None:
        self.add_resource("recipe", name, recipe)

    def add_stack(self, name: str, stack: Any) -> None:
        self.add_resource("stack", name, stack)


_LOCK = threading.Lock()
_REGISTRY: Optional[ExtRegistry] = None


def _package_of(ep) -> str:
    dist = getattr(ep, "dist", None)
    if dist is not None:
        try:
            return dist.metadata["Name"] or ep.value
        except Exception:
            pass
    return ep.value


def discover(force: bool = False) -> ExtRegistry:
    """Discover installed ``klink.plugins`` entry points (cached; lazy).

    Never raises for a broken extension: each package's load/register runs
    isolated, and its failure is recorded with the package name.
    """
    global _REGISTRY
    with _LOCK:
        if _REGISTRY is not None and not force:
            return _REGISTRY
        reg = ExtRegistry()
        try:
            from importlib.metadata import entry_points
            eps = list(entry_points(group=ENTRY_POINT_GROUP))
        except Exception as exc:              # metadata backend broken
            reg.fail("<entry-point scan>", repr(exc))
            eps = []
        for ep in eps:
            package = _package_of(ep)
            hook = PluginHook(reg, package)
            try:
                register = ep.load()
                register(hook)
                reg.plugins.append(hook.record)
            except Exception as exc:
                # roll back this package's partial contributions
                for name in hook.record["tools"]:
                    reg.tools.pop(name, None)
                for token in hook.record["domains"]:
                    reg.domains.pop(token, None)
                for item in hook.record["resources"]:
                    kind, _, rname = item.partition(":")
                    reg.resources.get(kind, {}).pop(rname, None)
                reg.fail(package,
                         f"{type(exc).__name__}: {exc} (entry point "
                         f"{ep.name!r} = {ep.value!r})")
        _REGISTRY = reg
        return reg


def reset_for_tests() -> None:
    """Drop the cache so the next discover() re-scans (tests only)."""
    global _REGISTRY
    with _LOCK:
        _REGISTRY = None


# -- lookup API (what examples and tools call) --------------------------------

def get_resource(kind: str, name: str) -> Any:
    reg = discover()
    bucket = reg.resources.get(kind, {})
    if name not in bucket:
        have = sorted(bucket)
        raise KeyError(
            f"no {kind} resource named {name!r} from installed klink "
            f"extensions (installed: {have or 'none'}); install the package "
            "that provides it, or check klink.status for broken extensions")
    return bucket[name]


def list_resources(kind: Optional[str] = None) -> Dict[str, List[str]]:
    reg = discover()
    kinds = [kind] if kind else list(RESOURCE_KINDS)
    return {k: sorted(reg.resources.get(k, {})) for k in kinds}


def failures() -> List[dict]:
    return list(discover().failures)


def status_summary() -> dict:
    """Compact block for klink.status: installed extensions + failures."""
    reg = discover()
    return {
        "installed": [
            {"package": p["package"], "tools": p["tools"],
             "domains": p["domains"], "resources": p["resources"]}
            for p in reg.plugins],
        "failures": list(reg.failures),
    }

# Writing a klink extension package (`klink.plugins`)

klink ships mechanism; your process data and house tools can live in **your
own pip package** and plug in without modifying klink. A lab or PDK vendor
publishes one package; anyone who installs it next to `klayout-klink` gets
the vendor's tools in `klink.find_tools`, and the vendor's named profiles /
device libraries / recipes / stacks resolvable by name.

> 中文见 [plugin-packages.zh-CN.md](plugin-packages.zh-CN.md)

## Just USING someone's extension? Three steps

You do not need anything on this page beyond this section:

1. `pip install acme-pdk-klink` — into the same Python that runs klink
   (the vendor tells you the package name).
2. Restart your agent (MCP servers load at agent startup).
3. That's it. Your agent's `klink.find_tools` now shows the vendor's domain
   (e.g. `acme_pdk`) with its tools — call them like any built-in tool. In
   your own scripts, the vendor's process data resolves by name:

   ```python
   from klink import ext
   profile = ext.get_resource("profile", "acme_2m")   # vendor's ProcessProfile
   ```

If an expected tool is missing, check `klink.status` → `extensions`: it
lists installed extension packages and names any broken one with its error.
Uninstalling the package removes everything again.

Everything below is for the people **writing** such a package.

## The minimal working package

Two files. `pyproject.toml`:

```toml
[project]
name = "acme-pdk-klink"
version = "0.1.0"
dependencies = ["klayout-klink"]

[project.entry-points."klink.plugins"]
acme = "acme_pdk.klink_ext:register"
```

`acme_pdk/klink_ext.py`:

```python
from acme_pdk.process import ACME_2M_PROFILE     # your data, your package

def hello(ctx, arguments):
    # ctx is the MCP bridge: ctx._client reaches the live KLayout session
    return {"greeting": f"hello {arguments.get('who', 'world')}"}

def register(hook):
    hook.add_domain(
        "acme_pdk", title="ACME PDK",
        summary="ACME's process data and helpers",
        usage="Resolve the profile with klink.ext.get_resource('profile', "
              "'acme_2m'); acme_pdk.hello is a smoke check.")
    hook.add_tool(
        "acme_pdk.hello", hello,
        description="smoke-check the ACME extension",
        input_schema={"type": "object",
                      "properties": {"who": {"type": "string"}}},
        domain="acme_pdk")
    hook.add_profile("acme_2m", ACME_2M_PROFILE)
```

`pip install acme-pdk-klink` next to klink, restart the MCP server, and:

- `klink.find_tools` (no args) lists an `acme_pdk` domain with its tool
  count; `domain="acme_pdk"` returns your usage text — your extension gets
  the same progressive disclosure as built-in domains.
- `acme_pdk.hello` is callable like any other tool.
- `klink.ext.get_resource("profile", "acme_2m")` returns your
  `ProcessProfile` from any example or script.

## What you can contribute

| hook call | contributes | notes |
|---|---|---|
| `add_tool(name, handler, description=, input_schema=, domain=)` | an MCP tool | name MUST be namespaced `<token>.<n>`; built-in prefixes (`view.`, `shape.`, `routing.`, …) are reserved and rejected |
| `add_domain(token, title=, summary=, usage=)` | a `find_tools` domain | the `usage` string is your skill-like recipe, disclosed on demand |
| `add_profile(name, obj)` / `add_devices` / `add_recipe` / `add_stack` | named resources | resolved via `klink.ext.get_resource(kind, name)`; how examples and flows pick up your process data |

Handlers follow the local-tool contract: `handler(ctx, arguments)` returning
a JSON-serializable dict; `ctx` is the MCP bridge (its `_client` is the
connected `KLinkClient`). Contributed tools are expected to follow the same
design rules as built-ins — one user intention per call, errors that
instruct, validate before mutating.

## Fault isolation (what happens when a package is broken)

Discovery is lazy (first use) and per-package isolated:

- an extension that fails to import or whose `register()` raises is
  **rolled back completely** (no half-registered tools) and recorded as a
  failure naming the package and the error;
- other extensions and every built-in tool are unaffected — the MCP server
  never crashes because of a broken extension;
- `klink.status` reports the `extensions` block: installed packages with
  their contributions, and the failure list. Check it first when an
  expected tool is missing.
- With **zero** extensions installed, klink's tool list is byte-identical
  to a plain install — the mechanism costs nothing until used.

## Verifying your package

1. `pip install -e .` your extension next to `klayout-klink`.
2. `klink.status` → your package under `extensions.installed`, empty
   `failures`.
3. `klink.find_tools` → your domain appears with the right count; call your
   tool once.
4. `python -c "from klink import ext; print(ext.get_resource('profile',
   '<name>'))"`.
5. Uninstall and re-check: everything disappears cleanly.

Process purity applies to you too: keep the *numbers* (layers, dimensions,
z tables) in your package's data modules, and pass them explicitly into
klink APIs — your extension is exactly the "example that owns its process"
pattern, shipped as a distribution.

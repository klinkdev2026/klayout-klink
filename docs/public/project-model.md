# The user-project model

> 中文见 [project-model.zh-CN.md](project-model.zh-CN.md)

You work in **your own project**, not in klink. Scaffold one with
`klink init <dir>` (the template is bundled in the package).

## Editable surface

```
your-project/
  pdk.py        your process — the only home for process facts
  custom_devices/ build scripts / device generators the agent writes
  specs/        .klink specs
  out/          generated GDS / results   (never commit GDS)
  AGENTS.md     agent rules (CLAUDE.md points to it)
  mcp.example.json
```

You edit `pdk.py`, `custom_devices/`, `specs/`. `klink` and the plugin are installed
packages — never edit them.

## Onboarding: the domain you describe becomes the default

There is **no hard-coded default project**. On a fresh project the agent:

1. **interviews you** about what you build until it can name the domain,
2. **picks the matching recipe** (see [recipes](recipes.md)) and tells you its
   geometry tier — and, if it needs your confidential geometry, asks for it,
3. **scaffolds** `pdk.py` + a first `custom_devices/` script that passes your process
   **explicitly** into klink,
4. **runs and verifies** with structured geometry / LVS queries.

## Verification, not screenshots

A layout is checked with `selection.get`, `shape.query`, `layout.info`, layer
counts, and live LVS — not screenshots. A route/layout is "done" only when live
KLayout LVS returns `match=True`; marker counts and "looks routed" do not count.

## Never commit confidential geometry

Your project must never contain GDS/PDK content — neither a proprietary foundry
PDK nor a transistor layout. The template `.gitignore` blocks `*.gds`/`*.oas`
by default. Point recipe code at such files at run time. (Open PDKs are fine to
depend on, still not committed.)

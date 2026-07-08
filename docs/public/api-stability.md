# API stability

> 中文见 [api-stability.zh-CN.md](api-stability.zh-CN.md)

klink is **0.x software (alpha)**. This page states, precisely, what that
means for compatibility so you can decide how tightly to pin.

## Version semantics

- **Minor version bumps (0.1 -> 0.2) may break the public API.** A 0.x minor
  release is where klink is allowed to reshape a documented surface if the
  design needs it.
- **Patch version bumps (0.1.1 -> 0.1.2) never break documented behavior.**
  A patch is bug fixes, docs, and additive changes only.
- Pin to a minor version (`klayout-klink>=0.1,<0.2`) if you want patch-level
  updates without surprise breaks.

## What counts as public API

These surfaces are covered by the guarantees above:

- The `klink` **CLI commands** (`klink init`, `klink update`, `klink plugin
  install`, `klink plugin status`, `klink-mcp --register`, …).
- `KLinkClient`'s **documented methods** (the RPC wrapper calls described in
  `docs/public/` and the client's own docstrings).
- The **MCP tool names and parameters** advertised by `tools/list` (and
  navigable through `klink.find_tools`).
- `ProcessProfile` / `ConnectivitySpec` / `StackSpec` **constructor fields and
  documented methods** — the mechanism classes your `pdk.py` builds on.
- The **on-disk contracts**: `klink.spec.json` v1, `interaction_context.jsonl`,
  and the SEND journal format.
- The **plugin RPC wire protocol**, versioned by `PROTOCOL_VERSION` and
  checked at handshake time between client and plugin.

## What is NOT public API

No compatibility guarantee applies to:

- Anything prefixed `_` (module- or attribute-level).
- Module layout and import paths that are not documented in `docs/public/`
  — internal reorganizations are not breaking changes.
- The Rust kernel crates' internals (`klink_boxmaze`, `klink_trackmaze`) —
  they are a byte-parity implementation detail behind the documented Python
  APIs, and may be refactored freely as long as outputs stay byte-identical.
- `example_template/` and other starter/example internals — these are meant
  to be copied and adapted, not depended on as a library.

## Deprecation flow

1. A deprecated CLI command, client method, MCP tool, or on-disk field keeps
   working for **one more minor release** after the release that announces
   the deprecation.
2. During that release it emits a warning naming the exact replacement.
3. The following minor release may remove it. Removals are listed under a
   **Breaking** heading in `CHANGELOG.md`.

## Wire protocol

The Python client and the KLayout plugin handshake on `PROTOCOL_VERSION` when
they connect. A `PROTOCOL_VERSION` bump is always a breaking change and is
recorded under a **Breaking** heading in `CHANGELOG.md` — it means an older
plugin cannot serve a newer client, or vice versa, and both sides need
upgrading together.

`klink.doctor` reports the client's protocol version and, once connected,
the plugin's protocol version, so a mismatch is visible before it causes a
confusing failure.

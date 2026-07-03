# Architecture

> 中文见 [architecture.zh-CN.md](architecture.zh-CN.md)

## Three tiers

| Tier | What | Form |
|---|---|---|
| **Mechanism (frozen)** | `klink` (client + algorithms + MCP) and the KLayout plugin | Installed packages (pip + salt). Users never edit them. |
| **Development repo** | tests, recordings, dev examples, design docs | This repo. Not shipped to users. |
| **User project** | your `pdk.py` + `custom_devices/` + specs + output | Your own folder; the agent writes here. |

The user's only editable surface is the **user project**. Mechanism is an
installed package, so the boundary is enforced by packaging, not discipline.

## The control path

```
Agent (Claude Code / Codex)
  → MCP server (python -m klink.mcp)        exposes klink RPCs as MCP tools
  → klink client  (NDJSON over TCP)
  → KLayout plugin (in-process RPC server)  port 8765 (RPC) + 8082 (klive-compat)
```

**Ports are equal-capability sessions.** Each KLayout window binds a port
(8765, 8766, …); every port is a full, independent session. No port has a
"working" or "LVS" role — pass the session you mean explicitly.

## Process purity

`klink` ships only mechanism and holds **zero process data** — no hardcoded
layers, devices, DRC numbers, or PDK instances. Every process fact lives in the
user project's `pdk.py` and is passed **explicitly** into the klink APIs. To
bring up a new process you write a `pdk.py`; you never edit `klink`. A klink
tool called without a process returns an **instructive error** naming the next
step, not a silent default.

## Agent design — three layers

Agent guidance is not just prose in one harness's config file:

1. **Tool-level enforcement (strongest, harness-independent).** Tool errors are
   instructions (they carry a `next_action`), edits validate before mutating,
   and state is persisted on disk. A weak or non-compliant agent is still
   steered correctly because the *tools* refuse bad input with a fix. This is
   the real safety net.
2. **Harness-neutral source of truth.** Lane definitions, recipes, and the tool
   design contract live in neutral files, not a single harness's config.
3. **Per-harness adapters (thin).** `CLAUDE.md` (Claude Code) and `AGENTS.md`
   (Codex) are thin transcriptions of layer 2 for one harness.

So a project's agent rules belong in `AGENTS.md` (neutral), with `CLAUDE.md`
pointing to it — but the durable guarantees come from layer 1.

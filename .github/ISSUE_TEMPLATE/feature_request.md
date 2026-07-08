---
name: Feature request
about: Suggest a new klink capability or tool
title: ""
labels: enhancement
---

**Problem**

What are you trying to do that klink doesn't support today? Describe the
workflow, not just the missing function name.

**Proposed tool shape**

If you have one in mind: what would the RPC / MCP tool / CLI command look
like (inputs, outputs, one call per user intention)?

**Note on process data**

klink ships only mechanism — the RPC/plugin layer, routing and LVS
algorithms, and the MCP bridge. It holds no hardcoded layers, devices, DRC
numbers, or PDK instances. If your request needs process-specific data
(layer numbers, device parameters, a PDK), that belongs in your own
project's `pdk.py` / example, passed explicitly into the klink APIs — not
baked into klink itself. Feature requests that add a generic capability
(a new routing backend, a new RPC, a new query) are in scope; requests to
hardcode a specific process or device into klink are not.

**Alternatives considered**

Any workaround you're using today, or other tools you compared against.

# klink Public Docs (Release)

The **published, release-grade documentation** for klink. Small and curated;
written for users adopting the tool, kept in sync with shipped behavior.

> 中文见 [README.zh-CN.md](README.zh-CN.md)

## Pages

| Page | What |
|------|------|
| [getting-started](getting-started.md) ([中文](getting-started.zh-CN.md)) | Install, configure, and run your first result (no GDS needed) |
| [architecture](architecture.md) ([中文](architecture.zh-CN.md)) | Three tiers, the control path, process purity, the 3-layer agent model |
| [project-model](project-model.md) ([中文](project-model.zh-CN.md)) | The user-project scaffold + the discover-domain onboarding flow |
| [recipes](recipes.md) ([中文](recipes.zh-CN.md)) | Per-domain starting points and their geometry tiers |
| [demos](demos.md) ([中文](demos.zh-CN.md)) | The four demos and exactly what each one requires |
| [control-plane](control-plane.md) ([中文](control-plane.zh-CN.md)) | The typed RPC surface, MCP tool catalogue (`klink.find_tools`), batch authoring, escape hatch |
| [interactive-workflows](interactive-workflows.md) ([中文](interactive-workflows.zh-CN.md)) | SEND selection memory, multi-session transfer, the recorder |

## Release scope (current)

A lightweight first release. **All four gallery demos run with no geometry
from you**: EBL wraparound and Hall bar fully offline; neural-electrode and
fit-device → P&R → LVS against a live KLayout session (the P&R demo fits its
device from **synthetic** exemplars — no device IP involved). The
silicon-photonics feature examples run on the open `gf.gpdk` with
`pip install gdsfactory`. Your own proprietary PDK or device geometry stays
bring-your-own at run time and is never committed.

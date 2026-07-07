# klink

<p align="right">
  <a href="./README.md">English</a> | <a href="./README.zh-CN.md">中文</a>
</p>

klink is an AI-native control plane for [KLayout](https://www.klayout.de/).
It turns a running KLayout GUI into a scriptable layout kernel that can be
controlled from external Python processes, MCP clients, and AI agents.

📖 **Documentation site** (English; 中文 at the site root):
<https://klinkdev2026.github.io/klayout-klink/en/>

**Step-by-step illustrated tutorials** — each one is a real run of a demo,
drawn stage by stage in KLayout, with a screenshot and runnable code per step:

- [Hall bar](https://klinkdev2026.github.io/klayout-klink/en/tutorial-hallbar.html) — geometry-first basics
- [EBL wraparound](https://klinkdev2026.github.io/klayout-klink/en/tutorial-ebl-wraparound.html) — writefield constraints
- [Neural-electrode array](https://klinkdev2026.github.io/klayout-klink/en/tutorial-neural-electrode.html) — parametric arrays + batch RPCs
- [gdsfactory MZI](https://klinkdev2026.github.io/klayout-klink/en/tutorial-gf-mzi.html) — the gdsfactory bridge + drag-to-reroute
- [Custom device → P&R → LVS](https://klinkdev2026.github.io/klayout-klink/en/tutorial-fit-device.html) — fit your own device into a digital place-and-route flow
- [Probe-card padframe](https://klinkdev2026.github.io/klayout-klink/en/tutorial-padframe.html) — pad ring + power net, two card modes
- [Passive-device templates](https://klinkdev2026.github.io/klayout-klink/en/tutorial-passives.html) — IDC / spiral / SAW / BAW

The project is split into three layers:

- `klink`: the external Python client, MCP bridge, routing/domain logic, and
  agent-facing workflows.
- `klink_plugin`: a thin KLayout-side RPC plugin that exposes selected `pya`
  and GUI operations.
- `examples_klink/public`, `tests/public`, and `docs/public`: examples,
  validation, and release documentation.

The core Python package pulls in no third-party runtime libraries — its only
declared dependencies are klink's own two Rust acceleration kernels, shipped as
prebuilt wheels. Virtual environments, caches, build outputs, and local test
artifacts are intentionally not part of a clean release.

## What It Does

- Controls KLayout over local TCP RPC, with batch methods sized for generated
  layouts (thousands of shapes/instances/PCells per call).
- Reads layout, cell, layer, shape, view, selection, and method metadata.
- Creates and edits shapes, text, cells, instances, PCells, Ports, and Anchors.
- Routes: tapered/steiner/damped/channel backends over Port/Anchor markers,
  plus a detailed-router → live LVS flow for custom-device circuits.
- Exposes KLayout operations as MCP tools through `klink-mcp`, navigable with
  `klink.find_tools`; runs controlled `pya` snippets as the escape hatch.
- Remembers what you SEND: selections sent from the KLayout toolbar become
  durable ids an agent can resolve ("this area", "the one I just sent").
- Drives many KLayout sessions from one bridge and moves geometry between
  them with a dry-run-then-commit transfer.
- Records a working session — manual edits and RPC edits — into a replayable
  Python script (plus a standalone `pya` variant).
- Supports gdsfactory-oriented workflows, including Port markers, component
  placement, routing, and klive-compatible `c.show()` display.
- Keeps the KLayout plugin thin while heavier logic runs in external Python.

## Repository Layout

```text
klink/                  Python client, MCP bridge, and core logic
klink_plugin/           KLayout salt plugin
examples_klink/public/  Public, open-box-runnable example gallery
tests/public/           Public test suite (no KLayout required)
docs/public/            Release documentation
rust/                   Rust acceleration crates (klink_boxmaze, klink_trackmaze)
pyproject.toml          Python packaging configuration
README.md               English README
README.zh-CN.md         Chinese README
CLAUDE.md               Claude Code operating rules and project context
LICENSE                 Apache-2.0 license
THIRD_PARTY_NOTICES.md  Third-party notices
```

## Requirements

- **KLayout** — the layout editor klink controls; for everything live this is
  the core prerequisite. Install the standard desktop build from
  <https://www.klayout.de/build.html>. klink is developed and tested against
  KLayout 0.30.x; any recent official desktop build (whose macro environment
  provides the `pya` Qt bindings) should work. Purely offline workflows (the
  public test suite and the offline demos) run without it.
- Python 3.10 or newer (the bundled Rust kernels target CPython 3.10–3.13).
- Optional: Claude Code or another MCP client.
- Optional: gdsfactory, the `klayout` Python package, NumPy/OpenCV, or detector
  dependencies depending on the workflow.

## Install Python Package

For normal use, install the published package from PyPI:

```powershell
python -m pip install klayout-klink
```

`pip install klayout-klink` installs klink **and its two Rust acceleration
kernels** (`klink-boxmaze-rs` + `klink-trackmaze-rs`) — klink's own code, shipped
as pre-built wheels for Linux / macOS / Windows on CPython 3.10–3.13. You get the
fast path automatically; nothing else to do.

> **On a platform with no pre-built kernel wheel** (an unusual OS / arch /
> Python), pip falls back to building the kernel from source, which needs a
> Rust toolchain (rustup). If you don't have one — or want the lightest possible
> install — use `pip install klayout-klink --no-deps` for the **pure-Python core
> only**: everything still works (the kernel has a pure-Python fallback), just
> slower on large place-and-route jobs.

**Third-party** scientific libraries (klayout, gdsfactory, numpy, …) are **not**
bundled — install the ones a feature needs yourself, into the same interpreter:

```powershell
python -m pip install klayout                          # offline DB / LVS extraction
python -m pip install gdsfactory                       # silicon-photonics routing
python -m pip install numpy opencv-python-headless     # nanodevice flake
python -m pip install scipy scikit-learn scikit-image  # flake detectors
```

When a feature needs a library that is missing, klink returns an error naming
the exact `pip install` command — you do not have to know these in advance.

### From source (development only)

Use an editable install (`-e`) only when you are working on klink itself — it
points the package at your working tree so source edits take effect immediately.
Normal users do not need this.

Windows PowerShell:

```powershell
git clone <repo-url> klink
cd klink
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
```

Linux / macOS:

```bash
git clone <repo-url> klink
cd klink
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
```

## Start a project

`pip install` gives you the `klink` CLI. Scaffold a project into a **new empty
folder** with `klink init`:

```powershell
klink init mychip     # creates ./mychip (pdk.py, example_template/, recipes/, agent rules, MCP config)
cd mychip
```

`klink init` refuses a non-empty folder, so point it at a fresh name. Then open
the folder with your agent (Claude Code / Codex) and describe what you are
building — it fills in `pdk.py` + `custom_devices/` from the matching recipe. The
scaffolded `example_template/` holds copy-and-adapt starter demos, grouped into
`nanodevice/`, `photonics/`, and `passives/`; run one with
`python example_template/<category>/<name>.py`.

When you upgrade klink later, refresh those bundled starters **without touching
your own work** (`pdk.py`, `custom_devices/`, `.klink/`, `out/`, `specs/` are
never changed):

```powershell
python -m pip install -U klayout-klink
klink update mychip   # or run `klink update` from inside the project folder
```

## Install KLayout Plugin

Prerequisite: KLayout itself must be installed first — get the desktop build
for your OS from <https://www.klayout.de/build.html>.

`klink_plugin` is a KLayout salt package. Once installed, KLayout autoruns
`pymacros/klink.lym` and starts:

- klink RPC on `127.0.0.1:8765`. If the port is busy, it tries the next ports up
  to `8799`.
- a klive-compatible server on `127.0.0.1:8082` for gdsfactory-style `c.show()`
  workflows.

Windows:

```powershell
cd path\to\klink
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\KLayout\salt" | Out-Null
Copy-Item -Path ".\klink_plugin" -Destination "$env:USERPROFILE\KLayout\salt\" -Recurse -Force
```

Linux / macOS:

```bash
cd /path/to/klink
mkdir -p ~/.klayout/salt
cp -R klink_plugin ~/.klayout/salt/
```

Either way the plugin ends up at `<KLayout salt dir>/klink_plugin/` (the folder
that contains `grain.xml`).

Restart KLayout after copying the plugin. Each KLayout window runs its own
klink session: it binds the first free port in `8765`–`8799` and registers as
session `klayout-<port>`, so with several windows open there is one listener
per window (`8765`, `8766`, …). A successful startup prints:

```text
[klink.server] listening on 127.0.0.1:8765
```

When upgrading an old plugin copy, close KLayout, remove the old
`salt/klink_plugin` directory, copy the new `klink_plugin` folder, then restart
KLayout.

## Smoke Test

Start KLayout with the plugin loaded, then connect directly from Python:

```python
from klink import KLinkClient

with KLinkClient() as c:
    print(c.ping(nonce=42))
    print(c.layout_info(verbosity="summary"))
```

If KLayout is listening on a non-default port:

```python
from klink import KLinkClient

with KLinkClient(port=8766) as c:
    print(c.ping())
```

For a first end-to-end result with no external geometry, run one of the public
demos (see [`docs/public/demos.md`](docs/public/demos.md)):

```bash
python -m examples_klink.public.demos.nanodevice.ebl_wraparound      # fully offline
```

## Claude Code / MCP

After installing the Python package, `klink-mcp` is available as a command-line
entry point.

Install the agent skills and project memory files:

```powershell
cd path\to\klink
klink-mcp --setup .
```

This installs or updates:

```text
.claude/skills/klayout/SKILL.md
.claude/skills/klayout-gdsfactory/SKILL.md
CLAUDE.md
```

Register the MCP server with Claude Code:

```powershell
claude mcp add klayout -- python -m klink.mcp --profile read,write,verify,escape --session-id project-klink
```

Common profiles:

| Profile | Purpose |
| --- | --- |
| `read` | Read-only exploration: layout, cell, layer, shape, view, and selection queries. |
| `write` | Editing tools: create cells, layers, shapes, instances, PCells, and undo records. |
| `verify` | DRC / LVS verification tools. |
| `escape` | Escape-hatch tools such as `exec.python`. |
| `all` | Expose everything. |

Profiles can be combined:

```powershell
python -m klink.mcp --profile read,write,verify,escape
```

Optional libraries must be installed into the same Python environment that
runs `klink.mcp`. For example, if gdsfactory tools report missing dependencies,
install gdsfactory into that same environment:

```powershell
python -m pip install gdsfactory
```

Use the MCP `klink.status` tool to inspect the active interpreter, detected
capabilities, and KLayout connection state.

## Tests

The public test suite is pure-Python and does not require KLayout:

```powershell
python -m pytest -q tests/public
```

Integration tests (routing, LVS, recorder) need a live KLayout with the
`klink_plugin` loaded and are exercised in the development repository.

## Troubleshooting

### Connecting from Python fails

Check that:

- KLayout is running.
- `klink_plugin` was copied into the KLayout salt directory.
- KLayout was restarted after plugin installation.
- Port `8765` is available, or use the actual session port such as `8766`.
- Firewall or security software is not blocking localhost TCP.

### MCP tools exist but return KLayout connection errors

The MCP server is running, but KLayout is not reachable. Start KLayout with the
plugin loaded, then call `klink.reconnect` or restart the MCP client. Use
`klink.status` to inspect the last connection error.

### gdsfactory or detector tools report missing dependencies

Install the missing library in the same environment used by the script or MCP
server (the error names the exact one):

```powershell
python -m pip install gdsfactory
python -m pip install scipy scikit-learn scikit-image
```

### PowerShell blocks `.venv` activation

Allow script execution for the current PowerShell process:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## Further Reading

- [`docs/public/getting-started.md`](docs/public/getting-started.md): install, configure, and first result.
- [`docs/public/architecture.md`](docs/public/architecture.md): the three tiers and the control path.
- [`docs/public/demos.md`](docs/public/demos.md): the demos and what each requires.
- [`docs/public/control-plane.md`](docs/public/control-plane.md): the typed RPC surface, the MCP tool catalogue, batch authoring.
- [`docs/public/interactive-workflows.md`](docs/public/interactive-workflows.md): SEND selection memory, multi-session transfer, the recorder.
- [`CLAUDE.md`](CLAUDE.md): Claude Code operating rules and project context.
- [`klink/mcp/README.md`](klink/mcp/README.md): MCP bridge details.

## Contributing

Contributions are welcome. A few project-specific rules keep klink coherent:

- **Process purity.** `klink/` is pure mechanism and holds zero process data
  (no hardcoded layers, devices, DRC numbers, ports, or PDK instances). Process
  facts live in an example or user `pdk.py` and are passed explicitly into the
  APIs. Do not add process constants to `klink/`.
- **One intention = one call.** New agent-facing tools use one call per user
  intention, errors that instruct (carry a `next_action`), validate-before-mutate,
  and state persisted on disk.
- **Tests must pass.** Run the public suite before sending a change:
  `python -m pytest -q tests/public`. Routing/LVS changes count as done only on
  a live KLayout LVS `match=True`.
- **Byte-frozen router.** `klink/routing/backends/flexdr/` and the crate under
  `rust/` are byte-parity baselines — the Rust kernel is a speed-only port of
  the pure-Python reference. Do not alter them casually.
- **Preflight.** `python -m klink.doctor` checks your interpreter, the plugin
  connection, and the client/plugin version handshake.

Open an issue or a pull request describing the change and how you verified it.
Contributions are accepted under the project's Apache-2.0 license.

Want to contribute a PR, discuss a larger change, or co-develop a feature?
Reach the maintainers at **klinkdev2026@163.com** (or open a GitHub issue).

## Acknowledgements

klink builds on and borrows from excellent open-source work:

- **[KLayout](https://www.klayout.de/)** — the layout editor and `pya`/`db` APIs
  klink drives and embeds into.
- **[OpenROAD](https://github.com/The-OpenROAD-Project/OpenROAD)** (BSD-3-Clause)
  — klink's routing engine contains faithful ports and concept adaptations of
  OpenROAD's detailed router `drt` (FlexDR / FlexPA / FlexGC) and global
  router `grt` (FastRoute).
- **[gdsfactory](https://github.com/gdsfactory/gdsfactory)** — the photonic
  component and `route_bundle` backend behind the silicon-photonics workflows.
- **[klive](https://github.com/gdsfactory/klive)** (MIT) — the display protocol
  that klink's plugin reimplements on port 8082 so gdsfactory-style
  `Component.show()` works unchanged.
- **[KlayoutClaw](https://github.com/caidish/KlayoutClaw)** (MIT) — nanodevice
  flake-detection priors and morphological mask helpers.
- **[Klayout-Router](https://github.com/Legendrexial/Klayout-Router)** (MIT) —
  the EBL auto-patching idea behind the writefield patch generator.

Formal third-party copyright and license texts are in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

## License

Apache-2.0. See `LICENSE`. Third-party components retain their own licenses; see
`THIRD_PARTY_NOTICES.md`.

# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project does not use dated entries (versions only).

## 0.2.2

- PCell fitter upgrade (three tiers). The exemplar fitter now either
  learns geometry exactly or says exactly what it cannot learn — never a
  silently-wrong abstraction:
  - Honesty layer: fit tables record their sampling envelope (`sampled`
    block; additive, v2 readers unaffected); `analyze()` warns on
    single-valued parameters (excluded from the regression, coefficient
    pinned to 0 — previously a singular-matrix crash) and flags
    integer-valued parameters as repetition/count suspects; summaries
    state "interpolation within sampled envelope; extrapolation
    UNVERIFIED".
  - Differential acceptance: the byte-exact harness
    (`verify_differential`) moves to its canonical home
    `klink.domains.structdevice.pcell_diff` (the L-Edit bridge re-exports
    it; public API unchanged); `structdevice.register_pcell` accepts an
    optional `diff_report` recorded into the result as acceptance
    provenance.
  - Repeat-group model (`klink_fitted_device_pcell_v3`): count-varying
    geometry — contact arrays, finger repeats — is fitted as exact
    integer laws: `floor((num_base + Σ coef·param)/den) + plus` counts,
    arithmetic pitch, fixed or centered origins, per-group 2-D grids.
    Every law is exact-verified at every exemplar (rational arithmetic,
    then the same float pipeline the renderers use); a den>1 count law is
    emitted only when exemplars pin it uniquely (sample both sides of a
    count step). Anything outside the model — alternating/parity
    structure, non-grid positions — is REFUSED with an instructive error
    naming the box family and the way out. Count-invariant families fall
    back to plain per-box linear edges. The KLayout plugin renders v3
    with byte-identical arithmetic; v2 tables render exactly as before.
- `tcell_workflows.py` gains verb 5, `fit`: harvest T-Cell exemplars →
  v3 fit → byte-exact differential gate against fresh L-Edit variants at
  held-out `--check` points → optional `--register` = live KLayout PCell
  whose placement is byte-verified too. Refusals exit 2 with the family
  named; layer names map only to GDS numbers already assigned in L-Edit.
  `--paramsets`/`--check` accept a JSON file path as well as inline JSON;
  each parameter set is announced before instancing so a generator error
  (modal dialog in L-Edit) names its culprit.
- New demo `examples_klink/public/demos/digital/fit_repeat_device.py`:
  the KLayout-native geometry-only route — draw an exemplar family, fit
  v3, register, place at never-sampled contact counts, byte-compare.
- Docs: bridge README documents the fit recipe (sampling rules, count
  thresholds, straddling exemplars) and the honest modal-dialog landmine
  (no programmatic recovery; prevent by exploring parameters gradually
  from the `read` defaults).

## 0.2.1

- L-Edit bridge design-targeting safety (macro v0.5.1, from blind-test
  findings): `new_design`/`open_design` now activate the created/opened
  design and FAIL with instructions if it could not become the active
  design — previously a fresh design could be created without a window,
  leaving the user's open design active and silently receiving every
  subsequent write (the shipped demo could draw into it). `open_design`
  pre-checks the path (a failed open pops a modal dialog, which freezes
  the bridge heartbeat) and falls back to `path + ".tdb"`. Every
  file-bound command echoes the design it touched as `result.file`, and
  an optional `expect_file` parameter makes a command refuse any other
  design. `driver.py demo` now creates its own scratch design, verifies
  the switch, and guards every write with `expect_file`; the Python
  client verifies the active design actually switched (defense in depth
  for older macros).
- `tcell_template.cpp` (new in the `ledit_bridge` template): a
  byte-exact-verified copy-and-adapt starting point for T-Cell generator
  code written back through the bridge (need_layer GDS stamping, typed
  parameter getters, integer internal units, degenerate-input guard) —
  hand-written UPI C++ usually fails L-Edit's in-place compile.
- Docs: bridge README gains a Design-targeting section (active-design
  semantics, `result.file`, `expect_file`, when `new_design` is
  appropriate) and names the import path (`pip install klayout-klink`,
  `import klink`, `klink.bridges.ledit.LEditBridgeClient`); clearer
  no-parameters-found message in `tcell_workflows.py`; `driver.py` is
  documented as importable (`from driver import call`).

## 0.2.0

- L-Edit bridge (new): drive a running Tanner L-Edit through a
  file-exchange RPC bridge. One source-loaded UPI macro
  (`example_template/ledit_bridge/ledit_bridge.cpp`, zero compile, no
  sockets, no extra DLLs; written against the old-version API subset —
  tested on v16.x) exposes design files (`new_design`/`open_design`/
  `save_design`), layers (GDS numbers stamped, new layers auto-colored),
  cells, batch drawing, instances/arrays, deep readout (selection and
  whole cells with per-object property trees, ports, labels; wires with
  cap/join; torus/pie with exact params), the design's full DRC rule
  table (`get_drc_rules`), and T-Cells in both directions: read generator
  code, instance programmatically with parameters, and write generator
  code back as a native parametric T-Cell.
- `klink.bridges.ledit` (new Python package): bridge client with
  heartbeat liveness and instructive errors; generic L-Edit→KLayout
  conversion (capability-matched, polygon fallback, layer-name merge
  policy `existing|incoming`); T-Cell toolkit — parameter parsing from
  generator code, a variant factory, and `verify_differential`, the
  byte-exact acceptance harness (a parametric port counts as done only
  when every box matches L-Edit's own generated geometry exactly).
- MCP: new `bridge_ledit` domain with one-call tools `ledit.status`
  (discovery/triage), `ledit.import_selection` (live selection → KLayout,
  circles stay parametric, layer names migrate), `ledit.push_cell`
  (flat KLayout cell → L-Edit).
- Template: `klink init` scaffolds `example_template/ledit_bridge/`
  (macro source + dependency-free driver + `tcell_workflows.py` with
  read/variants/writeback/verify verbs + bilingual README covering the
  known landmines).
- Fixed: smoke example 17 asserted a hard-coded `port.*` RPC count and
  broke when `port.mark_many` was added; it now checks the expected
  names as a subset.

## 0.1.7

- Stable instance identity (#13, from a field report): photonics port
  harvesting no longer derives instance ordinals from instance iteration
  order, which is not stable across save/reload — after reloading a saved
  layout, same-type instances with different rotations could swap
  identities and re-routing failed with wrong port orientations.
  `instance.insert` / `insert_many` / `insert_pcell` / `insert_pcell_many`
  accept an optional `klink_id`, stamped on the instance as a GDS-safe
  integer-keyed user property and echoed back by `instance.query`;
  `photonics.import_gf` stamps every device instance, and both harvesters
  key identity on the stamp (layouts imported by older versions fall back
  to the legacy order with a `RuntimeWarning`; duplicate ids — e.g. a GUI
  copy — raise an instructive error).
- Bounded change-diff cost (#14, from a field report): the change-event
  snapshot+diff pass that runs after every mutation is now bounded — a
  250ms wall-clock budget degrades remaining cells to count/bbox
  comparison (no false events), and an adaptive debounce spaces passes by
  4x the last measured duration, so edit bursts on large layouts no
  longer starve RPC handling into client timeouts. `meta.debug_signals`
  reports `diff_last_ms` / `diff_next_delay_ms` / `diff_degraded_cells`.

## 0.1.6

- Dispatcher enforces schema-declared required params before the handler
  runs: a missing param now returns an instructive `BAD_PARAMS` naming each
  missing param (with its schema description) instead of a handler `KeyError`
  surfacing as `ERR_INTERNAL`.
- Project template (`klink init`) onboarding: AGENTS.md now starts with a
  `klink.find_tools`-first step (domain index / `domain=` / `query=`), adds
  an "Own your tab" working rule (open your own tab via `view.new_tab`;
  never touch tabs you did not create), and defers the starter list to
  `example_template/README.md` as the single live enumeration (README gains
  the `gf_ports` and `layout/fill_region_demo` entries).

## 0.1.5

- Library management RPCs: `library.list` / `library.refresh` /
  `library.register_file` (the `pya.Library` face); `pcell.register_fitted`
  now refreshes its library automatically — no manual GUI refresh step.
  `instance.insert` / `instance.insert_many` accept `library=` to place
  registered-library cells directly.
- `cell.fill_region`: KLayout's Fill Utility as one RPC — fill a region
  given by boxes, polygons, circles/sectors, or hand-drawn scratch-layer
  geometry (`region_layers`), with `exclude_layers`, margins, row/column
  step spacing, and honest `remaining_area_um2` accounting. New starter
  `example_template/layout/fill_region_demo.py` opens the `layout/`
  starter category.
- Transient marker overlays: `view.highlight` / `view.highlight_clear`
  draw boxes, polygons, circles, and rulers on top of the view without
  touching the layout database. `selection.set_box` selects everything
  inside a µm bbox (optionally including instances).
- Layer display control: `layer.display_list` / `layer.set_visible` /
  `layer.set_style`, plus layer-properties file IO (`layer.load_lyp` /
  `layer.save_lyp`).
- Region math as typed RPCs: `geometry.boolean` (and/or/xor/not between
  two layers, optionally written back to a layer), `geometry.cell_xor`
  (layer-by-layer comparison of two cells), and `geometry.density`.
- `layout.import_file`: merge a GDS/OASIS file into the active layout
  with a `layer_map` and cell-name conflict modes.
- Modification RPCs: `shape.transform`, `shape.change_layer`,
  `instance.transform`, `cell.flatten`, `pcell.convert_to_static`.

## 0.1.4

- New photonics starter `gf_ports` (`example_template/photonics/gf_ports.py`):
  the three ways to get a klink Port — hand-drawn triangle conversion
  (including a deliberately-wrong cautionary case), gdsfactory-native
  auto-ports, and blackbox stub harvesting — plus the SEND / route / drag /
  `--reroute` loop.
- Ecosystem extension point: third-party pip packages extend klink through
  the `klink.plugins` entry-point group — contributed MCP tools appear in
  `klink.find_tools` under their own domain, and named resources (profiles,
  device libraries, recipes, stacks) resolve via `klink.ext.get_resource`.
  Discovery is lazy and fault-isolated (a broken package is rolled back and
  reported by `klink.status`, never crashing the server); zero installed
  extensions means a byte-identical tool list. Guide:
  `docs/public/plugin-packages.md`.

- Native 2.5d (3D stack) view: the `view.show_25d` RPC drives KLayout's
  D25View from a display list; `klink.stack25d.stack_displays` derives the
  list from a `StackSpec` plus a caller-owned z table. New example
  `stack_25d_view` (self-contained scene, plus `--demo-add4` for the full
  fit-device block) and a bilingual guide (`docs/public/25d-view.md`).

## 0.1.3

- Profile-derived DRC: `ProcessProfile.drc_script()` and
  `klink.routing.grid.profile_drc.run_drc` generate and run a KLayout DRC
  deck (width/space per routing layer, via-cut enclosure) from the same
  profile that drives routing and LVS — one process declaration feeds all
  three gates. New example `profile_drc_gate` proves it with a positive and
  a negative control.
- Bilingual DRC/LVS guide (`docs/public/drc-lvs.md`) plus a self-contained
  paste-to-agent handout (`docs/public/drc-lvs-agent-handout.md`).
- `klink.doctor --report`: prints a paste-into-issue environment report
  (versions, kernels, connection state, port scan) with the home directory
  redacted.
- `klink update` no longer copies pip-generated bytecode caches from the
  installed template into user projects.
- doctor's `klayout` pip check now verifies the real DB module, so a stray
  empty directory on `sys.path` no longer reads as an install.
- Documented the API stability policy for 0.x releases: see
  `docs/public/api-stability.md`.

## 0.1.2

- KLayout plugin bundled into the wheel, plus `klink plugin install` /
  `klink plugin status` CLI commands.
- abi3 kernel wheels for the Rust accelerators (broader future CPython
  compatibility from a single build per platform).
- The Python client fails fast with an instructive error when the KLayout
  connection drops mid-session, instead of hanging.
- `klink.doctor --scan` to find a live session across a port range, plus new
  informational checks for the Rust kernels and the `klayout` pip package
  version floor.
- Documentation fixes.

## 0.1.1

- Fixed a non-recursive `package-data` glob that dropped all
  `example_template` starters from the wheel; starters are now packaged
  recursively.
- `example_template` starters regrouped into categorized subdirectories
  (nanodevice, photonics, passives, digital), including the digital
  place-and-route family.
- `fit_device` flow decoupled from the KLayout plugin package — the fitted-edge
  math now lives in the pip package, so the starter imports only `klink`
  (running the P&R/LVS stages still needs a live KLayout session).
- Passive-device template usability polish.
- Version-compatibility CI matrix covering supported `gdsfactory` and
  `klayout` pip version lines.
- New `klink update` command to refresh the bundled starter templates
  without touching user files.

## 0.1.0

- Initial public release.
- KLayout RPC control plane: an in-process KLayout plugin server plus a
  typed Python client.
- MCP bridge with profile/domain navigation (`klink.find_tools`) so agents
  can discover tools by intent and area instead of a flat list.
- Routing backends for both digital (place-and-route) and photonic
  (gdsfactory bridge) workflows.
- Digital place-and-route → live LVS flow for custom, fitted devices.
- Nanodevice and photonics starter examples.
- Two Rust acceleration kernels (`klink-boxmaze-rs`, `klink-trackmaze-rs`)
  shipped as prebuilt wheels, with pure-Python fallbacks.
- `klink init` project scaffold and `klink-mcp --register` for one-command
  MCP client registration.

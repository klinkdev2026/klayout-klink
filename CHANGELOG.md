# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project does not use dated entries (versions only).

## 0.4.0

BREAKING (imaging): the four imaging exits no longer ship a look. klink
is the mechanism layer, so a hardcoded appearance constant in it is a
bug regardless of what it means — sun energy and camera lens are the
same category as a layer number or a DRC rule, and this project has
never allowed those inside `klink/`. Each exit now reads its appearance
from a declaration YOU own, and refuses to render without one rather
than falling back to klink's taste. The refusals name the file to copy.

- `imaging.blender` requires `style` (`klink_blender_style_v1`) — lights,
  camera, film, and the material recipes. Also new there, declared
  rather than baked: metals run noise -> colour-ramp mottling, so a flat
  metal stops reading as plastic; dielectrics get transmission, coat and
  a lowered specular level, so you can see plugs and gates through them;
  and every material gets a bevelled shading normal, because layout
  prisms are perfectly sharp and a perfectly sharp edge catches no
  highlight.
- `imaging.sem_top` requires `style` (`klink_sem_style_v1`) — background,
  rim gains, beam blur, grain, scanlines, vignette, false-colour mix.
  `seed` moved into it; `corner_radius_um` survives as an override.
- `imaging.xsection_run` requires `style` (`klink_section_style_v1`)
  ONLY when `render=true`. A section GDS has no look, so asking for
  geometry alone still needs nothing declared.
- `imaging.render3d` requires `style` (`klink_viewer_style_v1`) — the
  GLB's finish and the viewer page's whole palette.
- A `VisualStack` layer must now declare `color`, unless it is
  `kind="lattice"` and drawn as atoms. There is no default colour.

Copy the four style files from `example_template/imaging/` and edit the
numbers; they carry the reasoning for every value.

New in imaging:

- `imaging.sem_top` burns in a SCALE BAR. It never had one, or a way to
  ask for one, while calling itself an SEM-style view — an image you
  cannot measure is a picture, and no reviewer accepts one. The length
  rounds to 1/2/5 x 10^n, the text goes through the CJK-capable font
  path, and a bar that cannot fit says so instead of drawing junk.
- `imaging.sem_top` takes `window_um`, a lateral view window. There was
  no way to magnify: `width_px` only adds pixels over the same field, so
  the scale bar never changed. Now only a window changes it.
- `imaging.xsection_run` exposes `z_window_um` and `axis`, which the
  driver has always supported and the tool never forwarded. Without a
  window the engine's multi-micron substrate fills the frame and the
  device is a hairline at the top.
- `imaging.xsection_run` takes `auto_layer_base`: 300/0 is a klink
  convention like the 999/99 port layer, but a recipe that already
  writes 300/0 has to be able to move it.

Fixed in imaging:

- The die render decided for itself what was metal. A luminance guess
  ("metals in our stacks are light") metallised 11 of 15 materials in a
  plain CMOS stack — substrate, wells, implants, oxides and spacer are
  pale, while tungsten and silicide are dark — and because a metallic
  surface does not read as see-through it also defeated the declared
  alpha, hiding every tungsten plug inside an opaque white ILD. The
  stack decides; the renderer does not guess.
- A Chinese step name rendered as tofu boxes, silently. None of the
  three fonts tried carries a Han glyph, so a `# klink-step:` marker in
  Chinese went into the PNG, the film strip, the GIF and the sidecar
  SHA256 as .notdef squares with nothing failing. Section labels now
  resolve a CJK-capable face by asking the font whether it actually has
  the glyphs, and report any character no installed font can draw.

Found by a blind test before the release, not after:

- `scale_bar.plate`, a declared contrast strip behind the bar and its
  caption, in both the SEM and section exits. A blind agent asked to
  make an image it could "measure straight off" produced one whose bar
  landed white-on-white on a metal rail. The plate is blended, not
  pasted, so the layout stays visible through it; set it to null to
  switch it off.
- Step names that sanitise to nothing no longer leave dangling
  underscores in filenames. Eleven Chinese-named frames came out
  `film_step03__`, `film_step09__`, `film_step10__1_`. Filenames stay
  ASCII on purpose, but empty is more honest than punctuation, and any
  ASCII fragment inside the name is kept. The real name was never lost:
  it is in the sidecar and burnt into the frame.

New: rulers as data.

- `annotation.list / get / insert / update / delete / clear / measure`.
  A ruler is a view object, invisible to `selection.get` and to any
  saved GDS, so these are the only way to read one. `annotation.insert`
  is the agent-to-user channel for a LINE as `view.highlight` is for an
  area; `annotation.measure` is auto-measurement from a seed point.
  New event channels: `annotations_changed`,
  `annotation_selection_changed`.
- `imaging.xsection_run` takes `cut_from_ruler`: section along the
  ruler you drew in KLayout. Since 0.28 a ruler may be multi-segment,
  and its first and last point are then a line you never drew — klink
  refuses to flatten one and names the segments to choose from.

## 0.3.8

- `drc.run` could silently audit the wrong cell. `top_cell` is only a
  substitution for `$topcell` in the script: with a `source()` line the
  script uses it, but WITHOUT one the run is interactive and the engine
  audits whatever cell the view happens to show, ignoring `top_cell`
  entirely. Proven both ways round. A caller who passes `top_cell` and reads
  "0 violations" as "my cell is clean" may be looking at a result for an
  empty cell, and a verification tool that silently checks the wrong subject
  is worse than no tool. That combination is now REFUSED with the fix named,
  and every run reports `audited_cell` and `mode` — without them "0
  violations" is unfalsifiable.
- `drc.run`'s description now says `report()` takes a second argument
  (`report("title", $output_rdb)`). Without it the RDB is never written and
  the summary comes back empty with no error, so a run looks clean because
  nothing was RECORDED rather than because nothing was found.
- `import_cell_tree`'s `layer_of` must be CALLABLE, but the docstring
  described it as a mapping, so passing `build_layer_map()`'s dict — the
  obvious reading — died with `'dict' object is not callable` from inside an
  unrelated frame. It now refuses at the door and names `mapping.get`.
- `geometry.boolean`'s `write_to` failed with `ERR_NOT_FOUND` when the
  target cell did not exist, while the neighbouring transfer tools
  auto-create one. It now creates it and reports `cell_created`, so a typo
  stays visible instead of costing a round trip.
- `klink.status` no longer contradicts itself: `connected` (an object
  exists) and the nested handshake (a live round trip) measure different
  things, so a handshake that could not be refreshed now says `stale` and
  points at `klink.reconnect` rather than reading as a verdict on the
  plugin.
- a second measured counter-example in the geometry docs: `CONTACT` touching
  `POLY` is 0.1824 µm² on a broken two-finger NMOS and 0.1152 µm² on the
  CORRECT one, because a gate contact is supposed to land on a poly pad. A
  rule like "a contact must not touch poly" would condemn a working device.

## 0.3.7

- the L-Edit starter drew a wrong device, and its tests could not see it.
  `draw_device_demo.py` placed the source/drain straps on a fixed pitch
  instead of deriving them from where the gates actually are: for two
  fingers that put one strap across each gate (metal shorting source to
  drain) and a third entirely off the active, with two contacts connected to
  nothing. The gate was never contacted either -- a contact is WIDER than
  the gate it lands on, so the poly now gets a landing pad. Strap positions
  now come from the gate coordinates, and a source/drain region too narrow
  to hold a contact plus its enclosure raises instead of drawing something
  subtly wrong. The tests that passed the broken device now also ask whether
  the straps stay ON the active, whether they clear the gates, and whether
  every gate is contacted, at 1, 2 and 4 fingers.
- the client answers "what do I call, and what comes back". Wrapper names do
  not map onto RPC names by any derivable rule (`view.new_tab` is `new_tab`,
  `shape.query` is `shape_query`), and guessing wrong produced either
  `AttributeError` or "takes 2 positional arguments but 3 were given" --
  neither of which names the fix. Now: any RPC name works as a method
  (`client.view_new_tab(...)`), `client.help("shape.query")` prints the live
  schema including the RESULT keys a signature can never show, and an
  unknown attribute suggests near matches and points at `help()`. This is a
  correctness feature: an agent that cannot work out the API abandons klink
  and hand-rolls the work instead.
- the routing rule now carries the case that proves it. "Do not rebuild
  klink's capability yourself" was read narrowly as being about `draw`
  calls, so reading coordinates with `get_cell` and reasoning about them in
  Python looked exempt. Measured: an agent doing exactly that reported a
  CRITICAL DESIGN ERROR on a correctly drawn NMOS ("n+ covers the gate"),
  which is simply how a self-aligned device is drawn. Geometry questions
  carry PROCESS meaning; a tool that encodes the process does not invent
  rules the way first-principles reasoning does.

## 0.3.6

- non-ASCII paths work. L-Edit's API is ANSI (the system codepage) while
  JSON is UTF-8, and the macro wrote those bytes straight through, so a
  design under a path like a Chinese OneDrive folder produced files that
  were not valid UTF-8 — and the client decoded them strictly, raising
  `UnicodeDecodeError` out of `json.load`, which reads as a klink crash
  rather than an encoding mismatch. It took out anything naming that path:
  `hello` (and so `klink doctor` entirely), `list_designs` (it reports
  EVERY open design, so one such path poisons the call even when the active
  design is ASCII), and `get_cell`. The macro now converts at the boundary
  and emits pure ASCII (`\uXXXX`); the reverse was broken too, so a
  non-ASCII path handed to `open_design` reached `fopen` as the wrong bytes
  and simply was not found. The client reads every bridge file bytes-first
  — UTF-8, then the system codepage, which RECOVERS the characters instead
  of mangling them — so it keeps working against a macro that has not been
  reloaded yet. `doctor` also no longer lets a bridge problem abort the
  whole report.
- `push_cell_tree` no longer mirrors an array. It took `abs()` of the array
  step, but a KLayout array may step in -x/-y while L-Edit's nx/ny only
  grow in +x/+y, so a negative step silently moved every copy to where the
  source never had them. The origin now shifts to the far corner and the
  pitch stays positive, covering the same footprint; a repeat with a zero
  step is reported rather than collapsing onto itself.
- layers are no longer filled solid. You read a layout by seeing THROUGH
  the stack — a contact landing on its metal, an implant enclosing an
  active — and a solid fill hides exactly that. Auto-styled layers get one
  of eight hatches chosen by the same name hash that picks the colour, so
  pattern and colour both separate them; `set_layer_style` gains
  `fill:"hatch"` and `fill_rgb` no longer forces solid.
- the bridge is transport, not the toolbox. Agents were trying to satisfy
  layout requests inside L-Edit because nothing said otherwise. The bridge
  README, the project `AGENTS.md` and the `bridge_ledit` catalog entry now
  state the rule — read the cells out, do the work with klink's real
  capability in KLayout (routing, DRC, LVS, P&R, fill/boolean, gdsfactory,
  imaging, device fitting), push the result back — with a table mapping the
  usual asks onto the tool that does them.
- new `tcell_workflows.py from_pcell`: scaffold T-Cell generator code from
  a live KLayout PCell, so a parametric device can land in L-Edit as a real
  T-Cell instead of static geometry. It emits the proven boilerplate
  (`need_layer()` copied from the byte-exact-verified template, typed
  getters for every parameter UPI can actually read, layer creation, a
  degenerate-input guard) plus the PCell's ACTUAL geometry at the sampled
  parameters as concrete `LBox_New` calls — the port becomes "replace
  constants with formulas". Parameter types UPI cannot read and non-box
  shapes are reported rather than guessed at.

## 0.3.5

- the L-Edit bridge stopped stalling on large write-backs. A request over
  the macro's 64 KiB cap was dropped *without* being executed, answered or
  deleted, so the caller waited out its whole timeout while the file was
  re-read on every poll tick — and the error it finally got blamed a stale
  heartbeat. Measurement says the payload was never the problem: drawing
  costs about 0.02 ms per shape and stays flat as a cell fills, while every
  request costs roughly one poll interval regardless of size. The client now
  refuses an over-cap request outright, `draw()` splits by size and
  pipelines the parts, and the macro answers and consumes an oversized
  request instead of leaving it behind.
- new macro command `batch {ops:[...]}` runs N commands in ONE ordered
  request (stopping at the first failure), and `LEditBridgeClient.pipeline()`
  fires independent requests together — measured ~10x over blocking calls.
  The macro polls at 15 ms while requests are flowing and keeps its old idle
  duty cycle otherwise, so a round trip fell from 0.197 s to 0.026 s.
- hierarchy transfer both ways: `ledit.push_cell_tree` and
  `ledit.import_cell_tree` move a cell and everything below it, cells in
  dependency order, instances rebuilt as instances (arrays and orthogonal
  rotations included), layer identity by NAME, idempotent, one batched
  request. Placements the other side cannot express exactly — magnification,
  non-orthogonal rotation, skewed array — are reported, never approximated.
  `ledit.push_cell` / `import_selection` remain the flat single-cell tools.
- new macro command `import_gds` for bulk transfer, keeping the cell
  hierarchy and idempotent via `overwrite:"all"`. L-Edit's reader expects
  the classic 2048-byte Calma block padding that KLayout does not write;
  unpadded, the import aborts at EOF behind a modal dialog that freezes the
  bridge, and `LFile_ImportGDSII` still returns OK while leaving empty cell
  shells. `import_gds()` pads a sibling copy for you and the macro now reads
  the import log rather than trusting the status code.
- designs are visible and switchable: `list_designs` reports every open
  design, and `activate_design {file}` switches between them BY NAME, so a
  design created and never saved is reachable too. It raises an existing
  window instead of opening a cell, so nothing inside the design is touched.
- `LEditBridgeClient.bind_file()` / `bind_active()` attach the `expect_file`
  design guard to the client, so it rides on every request and on every op
  inside a batch. Passing it per call left the convenience wrappers unable
  to carry it, and one forgotten guard is a write into the user's own
  design.
- `klink doctor` reports the L-Edit bridge (namespace, macro version,
  heartbeat, current design) — it used to be blind to it, so a user whose
  bridge was the broken part got either an all-green report or a KLayout
  error. `klink doctor` also stopped dropping its own flags: `--scan`,
  `--report`, `--json`, `--gdsfactory`, `--host`, `--port` now work.
- new starter `example_template/ledit_bridge/draw_device_demo.py` draws a
  real device (two-finger NMOS over five layers) and demonstrates design
  targeting, idempotent redraw, batching and read-back verification.
  `example_template/README.md` and the project `AGENTS.md` now list the
  L-Edit bridge, which the category list omitted entirely.
- `KLINK_LEDIT_BRIDGE_ROOT` relocates the exchange directory for sandboxes
  that cannot write `%LOCALAPPDATA%`. The client honoured it, but the macro
  and `driver.py` did not, so the escape hatch silently pointed the two ends
  at different directories. All three read it now, and it is documented.
- messages people act on: every "load the macro" instruction carries the
  absolute path of the packaged `.cpp`; a missing heartbeat says outright
  that running L-Edit is not the same as loading the macro; a stale one
  distinguishes "loaded but paused" from "L-Edit restarted, load it again";
  a failed inbox write names the environment variable instead of raising a
  bare `OSError`; a timeout warns that the request may already have executed,
  so a blind retry double-draws. User-facing text is ASCII, because an em
  dash renders as `?` on a Windows console.

## 0.3.4

- imaging figures are now legible by default. `imaging.xsection_run` /
  `render_section_png` accept `z_window_um=(z_bottom, z_top)` to frame a
  section vertically — the engine's substrate is microns deep while the
  films are a few hundred nanometres, so the default bbox framing spent
  most of every frame on bulk. The Blender camera now derives its
  distance by solving the eight bounding-box corners against the lens'
  horizontal and vertical field of view instead of scaling the box
  diagonal; for flat geometry (any die) the old distance put the camera
  inside the slab and rendered a blank sliver. `render_die_glb` gains
  `z_scale` (echoed in the result) for stacks that really are hairlines
  at 1:1.
- `klink update` no longer deletes a starter's `_generated/` output
  directory: its mirror cleanup treated user-produced files as stale
  starters. The imaging starter README now states that
  `example_template/` is package-owned and that your own stack/recipe
  must be copied out before editing.
- the imaging starter is now a real process, not a demo shape. It ships
  a new `demo_layout.py` (a four-transistor CMOS row: NMOS in the
  substrate, PMOS in an n-well, poly gates over active, a contact per
  source/drain, metal-1 straps and power rails) and an eleven-step
  planar CMOS recipe (n-well, LOCOS, gate oxide, poly + silicide, LDD,
  spacer, S/D, ILD, contact etch + W plug + CMP, metal-1 damascene), so
  the film, the 3D model and the SEM view show real device structure.
- `imaging.xsection_run` / `render_section_png` gain `axis=True`: a z
  ruler in µm plus a lateral scale bar.
- recipe variables named with a leading `_` are intermediates and are no
  longer auto-output as materials of their own.
- the section renderer draws geometry supersampled and shades each
  material with a slight gradient (`supersample`, `shade`), so process
  profiles stop looking like staircases; the demo stack makes ILD/IMD
  translucent so the 3D exits show the gates, junctions and plugs
  inside the stack.
- fixed: the guard against multi-line `output(...)` calls was a
  substring check, so a recipe whose COMMENT mentioned `output()` was
  refused. It now looks for a real call.

## 0.3.3

- `klink init` (and `klink update` for existing projects) now scaffolds
  an `imaging/` starter category: `demo_stack.py` and `demo.pyxs` — the
  two process files YOU own and an agent edits interactively — plus
  runnable demos for every exit (cross-section + per-step film, 3D GLB
  + self-contained viewer, SEM top views incl. a per-mask sequence, and
  the optional Blender figure) and a bilingual README with the complete
  optional-dependency command list. The starters run both in place and
  as bare copied scripts. Demo docstrings now front-load the full
  install commands (numpy/scipy/pillow for rendering; mapbox-earcut for
  3D) that 0.3.2 surfaced only through runtime errors. No library code
  changes.

## 0.3.2

- Fix: the 3D exit's instructive install error was incomplete —
  trimesh's polygon extrusion needs a triangulation engine
  (`mapbox-earcut`) that trimesh does not itself depend on, so a clean
  environment following the printed command still failed with trimesh's
  raw "No available triangulation engine!". `mesh3d` now probes for the
  engine and its install guidance is `pip install trimesh shapely
  mapbox-earcut`. Caught by clean-environment wheel verification.

## 0.3.1

- Fix: the 0.3.0 wheel shipped WITHOUT the vendored model-viewer bundle
  (the package-data entry existed only in the development pyproject, not
  the release one), so `imaging.render3d` could not build its
  self-contained viewer page from a pip install. The release pyproject
  now carries the asset glob, and `tools/check_versions.py` gained a
  package-data parity guard so a dev-only data glob can never ship a
  broken wheel again. No code changes.

## 0.3.0

- New `imaging` domain: process-realistic imaging exits driven by ONE
  declaration (`klink_visual_stack_v1`: per layer z0_um/z1_um, colors,
  material kind solid|lattice|dielectric, SEM response, and the .pyxs
  recipe variable it corresponds to; optional `recipe_styles` for
  engine-only materials). klink ships the mechanism; stacks and recipes
  are example/project-owned. Four MCP tools, all runnable offline from
  a gds path or against the live session, all with deterministic output
  contracts (`output_dir`+`basename`, `overwrite=false` by default,
  `klink_imaging_result_v1` sidecars with stable key order):
  - `imaging.xsection_run` — headless process cross-sections from a
    `.pyxs` recipe along an explicit `cut_um` line (engine:
    `klayout-pyxs==0.1.13`, optional dependency, instructive install
    error; GDS written without timestamps so identical inputs are
    byte-identical). `steps=true` + `# klink-step: <name>` recipe
    markers produce one section per process step; `render=true` adds
    material-colored PNGs and assembles a per-step film strip + GIF.
    `.pyxs` recipes are trusted Python executed in-process.
  - `imaging.render3d` — GLB + a SELF-CONTAINED interactive viewer
    page (vendored model-viewer inlined, model embedded; opens offline
    by double-click; per-layer color/metallic/roughness panel, tone
    mapping incl. agx, PNG export). `mode=fast` extrudes the declared
    stack; `mode=process` sweeps the cross-section engine so LOCOS,
    conformal layers and CMP topography are real; `fraction<1` gives a
    cutaway exposing a true section face.
  - `imaging.sem_top` — SEM-style top views (greyscale + false color):
    per-layer emission levels and topography edge glow, litho corner
    rounding, seeded grain/scanlines/vignette (same seed = identical
    image); `layers=[...]` renders the masks printed at a given step.
  - `imaging.blender` — paper-grade renders via headless Blender
    (`pip install bpy`, optional; executed in a subprocess). `mode=die`
    restages a render3d GLB on transparent film with a shadow catcher;
    `mode=figure` builds a device figure at 1:1 layout coordinates
    where `kind='lattice'` layers render as the material's atomic
    structure (graphene / MoS2 motif library with literature lattice
    constants documented). Saves a hand-editable `.blend` next to the
    PNG.
- Third-party: the @google/model-viewer 3.5.0 bundle (BSD-3/MIT) is
  vendored for the self-contained viewer pages; notices preserved in
  the file header and THIRD_PARTY_NOTICES.md.

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

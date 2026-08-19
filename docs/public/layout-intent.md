# Regions & Executable Layout Intent

Circle an area in KLayout, and let the agent generate into it — safely,
with a preview, and regenerable after you change your mind.

> 中文见 [layout-intent.zh-CN.md](layout-intent.zh-CN.md)

```text
drag rulers around a spot          (KLayout's own ruler tool: box/ellipse)
  -> region.claim                  rulers become ONE Region marker PCell
  -> intent.prepare                analyze + plan + validate (writes nothing)
  -> intent.apply                  one transaction, one Ctrl+Z
  -> intent.regenerate             change numbering/pitch, swap the output atomically
```

## Regions

A **Region** is a claimed area: a `klink_Region` PCell on the reserved
marker layer (default `999/10`, configurable via `region.set_layer`). It
lives **in your layout**, so it travels with the GDS, survives restarts,
and can be clicked + SENT to the agent like any other object. Rulers are
only the input gesture — a successful claim consumes them (undo brings
both the rulers and the pre-claim state back).

Claims compose several rulers with three roles:

| role | meaning | example |
|---|---|---|
| `include` | union (default) | two boxes → L-shape |
| `clip` | intersect | ellipse ∩ box → half disc |
| `exclude` | subtract (holes are never writable) | circle − circle → annulus |

Ellipses are discretized safely: include/clip inscribed, exclude
circumscribed — the writable area only ever shrinks, never crosses what
you drew. The result must be one connected component; disconnected
islands are rejected with their bboxes so you can claim them separately.

Useful on its own, before any generation:

- `region.get` returns the polygon — feed `hull_um` to `cell.fill_region`
- `region.occupancy` reports what is inside: per-layer obstacles, named
  obstacle cells, free area
- `view.zoom_box` on `bbox_um` navigates there

## Executable intent: numbered arrays

`intent.prepare` plans a pitch grid of **any existing cell** inside a
Region, with a **unique physical number label per copy** (real polygon
text, previewed exactly as applied). Nothing is written until you confirm
with `intent.apply`; the output lands in a dedicated `KLINK_I_*` container
cell with a stable identity, so `intent.regenerate` can atomically replace
it later — and refuses if you hand-edited the output (diverged is
detected, never silently overwritten).

Everything is explicit — klink ships **no process defaults**:

- **Obstacles are whatever you declare**: `obstacle_layers` (your design
  layers), `obstacle_cells` (named device/blackbox cells — every instance
  occurrence counts by bbox), `extra_obstacles_um` (free-form polygons),
  plus `clearance_um`. Declaring none requires an explicit
  `allow_empty_obstacles: true`.
- **Instances, not polygons**: the arrayed thing is an instance of your
  cell, with `rotation_deg` (0/90/180/270) and `mirror` supported.
- **Labels** have two modes:
  - *offset*: `{layer, height_um, offset_um}` relative to each footprint
    center;
  - *slot* (recommended): circle a small region **inside the unit cell**
    that says "the number goes here", and pass
    `{layer, slot_region: "R00X"}`. Every copy gets its own number
    auto-fitted into its own slot; the slot moves and rotates with the
    instance. Drag the slot and regenerate — every number follows.
- **Numbering** has two modes:
  - *prefix*: `{prefix: "S", width: 3, start: 1, order: "top_down"}`
    → S001, S002, …
  - *pattern*: any grid notation your project uses —
    `"{row}+{col}"` → 1+2, `"{row}-{col}"` → 1-3, `"R{row}C{col}"`,
    `"{row:A}{col}"` → A1 (letters), with per-axis
    `{start, step, order}`. Rejected sites never leave numbering gaps.

Every plan is validated before you see it: footprint and label
containment, obstacle clearance, overlap, duplicate numbers. A plan with
problems cannot be applied; a layout that changed between prepare and
apply is refused (re-prepare).

## Delivery: clean export

Region/Port/Anchor markers are working aids on reserved layers — they must
never reach a mask. `layout.export_clean` is the fail-closed exit:

```text
layout.export_clean {path: "out.gds", allowlist_layers: ["10/0", "20/0"],
                     cells: ["TOP"]}
```

It removes every klink marker (by PCell type, not by guessing layer
numbers), writes ONLY your explicit layer allowlist, strips PCell context,
verifies the output file by re-reading it, and only then promotes it into
place. The live layout is never touched. `layout.save_file` remains the
full working archive.

Pass `cells` to scope the export to those top cells and their hierarchy —
without it, **every** top cell in the layout goes into the file, including
unrelated ones from a shared session. Note that generated `KLINK_I_*`
container cells are real design content, not markers: they are kept (and
verified) in the export.

## Site engine (fabrication domain)

The deterministic grid/numbering core is shared with the fabrication
domain and usable directly:

- `generate_grid_sites` / `generate_circular_die_sites` — site grids over
  a bbox or a circular die
- `number_sites` — rowcol / sequential / prefix / serpentine schemes with
  `order` and `start`
- `pattern_site_ids` — the grid-notation engine behind numbering patterns

Runnable demos (against a live KLayout):

```bash
python -m examples_klink.public.features.region_claim_fill
python -m examples_klink.public.features.region_array_labeled
python -m examples_klink.public.features.fabrication_sites
```

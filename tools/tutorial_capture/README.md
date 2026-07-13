# tutorial_capture (dev-only)

These scripts replay a starter demo's build against a **live KLayout**
session in stages, screenshotting after each stage, to produce the
step-by-step figures used by the public website's tutorial pages
(the `tutorial-*.html` pages of the docs website source tree). They
ship with the release so tutorials stay regenerable — `release/manifest.json` uses a
whitelist (`wholesale_dirs`), and `tools/` is not on that list, so nothing
here ships. They are also outside `tests/`, so `pytest tests/unit -q` never
collects them.

Per the ruling that motivated this directory: **a tutorial is a
step-by-step expansion of its demo, not a separate artifact.** Whenever a
starter demo under `example_template/` (and its `examples_klink/public/`
source) changes, the matching tutorial page can drift out of sync with
the actual RPC calls/output. The fix is mechanical, not manual editing of
prose:

1. Run `python examples_klink/public/sync_to_template.py` so
   `example_template/` matches `examples_klink/public/demos/`
   (`tests/unit/test_template_starters_synced.py` guards this but does not
   auto-fix drift).
2. Re-run the matching script(s) below against a live, disposable KLayout
   tab and diff the new screenshots / `build_report.json` numbers against
   what the tutorial page currently shows. Update the tutorial HTML's
   prose/numbers/screenshots to match reality — never hand-author them.

## Layout

One subdirectory per demo. The first 4 match the pip-installable starters
(`example_template/{name}.py`); `fit_device/` covers the repo-only
`examples_klink/public/demos/fit_device_pnr_lvs.py` demo (batch-2 tutorials):

```text
hallbar/draw_hallbar_tutorial.py                 -> tutorial-hallbar.html
ebl_wraparound/draw_ebl_tutorial.py               -> tutorial-ebl-wraparound.html
  ebl_wraparound/annotate_detail.py               (adds the highlight+arrow overlay)
neural_electrode/draw_neural_tutorial.py          -> tutorial-neural-electrode.html
  neural_electrode/annotate_detail.py
gf_mzi_module/draw_gf_mzi_tutorial.py             -> tutorial-gf-mzi.html
  gf_mzi_module/annotate_detail.py
fit_device/draw_fit_device_tutorial.py            -> tutorial-fit-device.html
  fit_device/annotate_detail.py                   (fit-model arrows on the exemplar crop)
gf_ports/draw_gf_ports_tutorial.py                -> tutorial-gf-ports.html (see its own README.md --
                                                      writes straight into the website's asset dir by
                                                      default, and its Turn-5 SEND figure is a full-window
                                                      PowerShell screenshot, not a view.screenshot clip)
multiwindow/draw_multiwindow_tutorial.py          -> tutorial-multiwindow.html + the workflows.html
                                                      SEND/GFTGT/transfer figures (see its own README.md --
                                                      drives TWO live sessions at once, all six figures are
                                                      full-window PowerShell screenshots, CN + EN sets)
```

`hallbar/` has no `annotate_detail.py` — its one annotated figure
(`step-07-detail-annotated.png`) was produced ad hoc in the session that
authored it and the script was never saved. If you need to regenerate
that figure, write one following the same pattern as the other three
`annotate_detail.py` scripts (Pillow rectangle + arrow over a known
um-bbox, see any of them for the `um_to_px` convention).

## Running

Each `draw_*.py` owns its own disposable KLayout tab lifecycle end to end:
it opens a fresh tab via the typed `view.new_tab` RPC, draws/screenshots
into it, then closes that tab and restores whatever tab was current
beforehand (`view.activate_tab`, skipped when `view.new_tab`'s
`previous_current_index` is -1, i.e. there was no tab open at all). Just
point `KLinkClient` at a live KLayout with the klink plugin loaded — no
manual tab setup is required. Any tab that already existed when the script
started is the user's own session and is never touched; each script
verifies the current tab is the one it just created, every time, right
before every screenshot.

```bash
# from the repo venv, against a live KLayout with the klink plugin loaded
# (each draw_*.py opens and cleans up its own disposable tab)
python tools/tutorial_capture/hallbar/draw_hallbar_tutorial.py \
    --out-dir test_outputs/tutorial_capture/hallbar

python tools/tutorial_capture/ebl_wraparound/draw_ebl_tutorial.py \
    --out-dir test_outputs/tutorial_capture/ebl_wraparound
python tools/tutorial_capture/ebl_wraparound/annotate_detail.py \
    --out-dir test_outputs/tutorial_capture/ebl_wraparound

python tools/tutorial_capture/neural_electrode/draw_neural_tutorial.py \
    --out-dir test_outputs/tutorial_capture/neural_electrode
python tools/tutorial_capture/neural_electrode/annotate_detail.py \
    --out-dir test_outputs/tutorial_capture/neural_electrode

python tools/tutorial_capture/gf_mzi_module/draw_gf_mzi_tutorial.py \
    --out-dir test_outputs/tutorial_capture/gf_mzi_module
python tools/tutorial_capture/gf_mzi_module/annotate_detail.py \
    --out-dir test_outputs/tutorial_capture/gf_mzi_module

# fit_device runs the full fit -> P&R -> LVS flow, so it needs a live
# session it may build in; --klink-port picks the session (default 8766)
python tools/tutorial_capture/fit_device/draw_fit_device_tutorial.py \
    --out-dir test_outputs/tutorial_capture/fit_device --klink-port 8766
python tools/tutorial_capture/fit_device/annotate_detail.py \
    --out-dir test_outputs/tutorial_capture/fit_device
```

`--out-dir` defaults to `test_outputs/tutorial_capture/<name>/` under the
repo root if omitted (each script derives the repo root from its own
`__file__`, not a hardcoded path — do not reintroduce a hardcoded
absolute `sys.path.insert(...)` or similar). Each `annotate_detail.py` reads
`step-NN-detail.png` from the **same** `--out-dir` its `draw_*.py`
sibling wrote to and adds the `-annotated.png` variant next to it.

## Screenshot iron rules (unchanged, do not relax)

Every script here follows the same non-negotiable rules as any other
KLayout screenshot code in this repo (see `CLAUDE.md`'s selection-first /
destructive-RPC-safety sections for the general versions):

- Operate on a **one-off, disposable tab** the script itself opens via
  `view.new_tab`, for this purpose only.
- **Verify the current tab is ours** (title/index check) immediately
  before every screenshot — never assume the active tab is still the one
  we created.
- Never touch a pre-existing tab; any tab that existed before the script
  started is the user's own session. Close only the tab the script
  created, then restore whatever was current beforehand.

## `view.new_tab` / `view.hier_levels` RPCs

All four `draw_*.py` scripts call the typed `view.new_tab` RPC (via the
`KLinkClient.new_tab()` wrapper) to open and later close their own
disposable tab, and use `view.zoom_box(bbox_um=...)` /
`view.screenshot(bbox_um=...)` for framing — plain microns throughout,
never microns passed through the `bbox_dbu=` keyword.
`gf_mzi_module`'s script calls `KLinkClient.hier_levels(max=...)` to raise
the displayed hierarchy depth (see its docstring for why). The one
remaining `exec.python` escape hatch in that same script is for
repositioning an already-placed instance's transform (simulating a drag)
-- no typed RPC mutates an existing instance in place.

## Manually-captured figures (not regenerated by these scripts)

One tutorial figure is a screenshot of a KLayout Qt dialog, which
`view.screenshot` cannot capture (it renders only the layout canvas, not
child windows):

- `assets/tutorials/fit-device/step-07-lvs-verify.png` — the Netlist
  Database Browser after `lvs_check(mode="lvsdb")`, showing the matched
  cross-reference. Capture it by hand from the KLayout GUI after running
  the fit-device demo. It is referenced by `tutorial-fit-device.html`;
  do NOT treat it as a stray/unused asset and delete it.

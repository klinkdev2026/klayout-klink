# gf_ports tutorial capture

Regenerates every screenshot used by the docs website's
`tutorial-gf-ports.html` / `en/tutorial-gf-ports.html` ("three ways to get a
klink Port" -- hand-drawn, gdsfactory-native, blackbox-harvested -- plus the
SEND / route / drag / reroute loop). Per the ruling in
`tools/tutorial_capture/README.md`: **a tutorial is a step-by-step expansion
of its demo, not a separate artifact** -- these screenshots must be
regenerable from a script, not hand-captured once and never touched again.

Source demo: `examples_klink/public/demos/photonics/gf_ports.py` (also
ships as the pip-installable starter `example_template/photonics/gf_ports.py`
-- keep them in sync with `python examples_klink/public/sync_to_template.py`
per the usual rule). Its five `scenario_*` functions are reused directly
where they already return the state we want to screenshot (scenarios 3 and
4); scenarios 1, 2, and 5 build + convert/route in ONE call with no
"before" state exposed, so `draw_gf_ports_tutorial.py` replicates their
build steps verbatim (same literal coordinates, same constants imported
FROM the demo module) and takes a screenshot in between -- see the script's
module docstring for the full reuse/replicate breakdown.

## Running

```bash
# from the repo venv (needs BOTH klink and gdsfactory -- see gf_ports.py's
# own "Requirements" docstring section), against a live KLayout with the
# klink plugin loaded
python tools/tutorial_capture/gf_ports/draw_gf_ports_tutorial.py \
    --port 8765 \
    --out-dir "D:\klink_website\assets\tutorials\gf-ports"
```

`--out-dir` defaults to `D:\klink_website\assets\tutorials\gf-ports` --
**unlike the other `tools/tutorial_capture/*/draw_*.py` scripts** (which
default to `test_outputs/tutorial_capture/<name>/` and leave publishing to
a manual copy step), this one writes PNGs directly into the docs website
checkout's asset directory, because that is what this capture job's own
brief specified and `view.screenshot(mode="path", ...)` already writes
straight to an absolute path on disk (same machine as the KLayout process
in this dev setup). Pass a different `--out-dir` if your website checkout
lives elsewhere. **This script never writes or touches any HTML** -- only
PNGs under the target directory.

The script owns its own disposable KLayout tab end to end (opens one via
`view.new_tab`, builds every scenario as a sibling cell inside it, closes
it, restores whatever tab was current before) -- see
`tools/tutorial_capture/README.md`'s "Screenshot iron rules". Any
pre-existing tab (e.g. your own working session on the same port) is never
touched.

## What gets written, and which tutorial Turn each one illustrates

| File | Turn | State |
|---|---|---|
| `gf-1-handdrawn.png` | Turn 1 | Scenario 1 built, **before** `recognize_handdrawn_ports` -- two yellow hand-drawn triangle markers |
| `gf-2-converted.png` | Turn 1 (cont'd) | Scenario 1 **after** conversion -- markers replaced by red `klink_port.P0` / `P1` |
| `gf-3-err-before.png` | Turn 2 | The cautionary WRONG-recipe scenario, **before** conversion -- oversized triangle |
| `gf-3-err-after.png` | Turn 2 (cont'd) | **After** conversion -- port width/orientation visibly wrong, not edge-attached |
| `gf-4-auto.png` | Turn 3 | gdsfactory's own `mmi1x2` ports, auto-marked, no hand-drawing |
| `gf-5-blackbox.png` | Turn 4 | Synthetic blackbox `SYNTH_BB`, ports harvested by stub convention |
| `gf-7-send.png` / `gf-7-send-en.png` | Turn 5 | Full KLayout window, SEND button + cell tree annotated (CN / EN) |
| `gf-6-beforeroute.png` | Turn 6 | Loop scenario built, **before** `route_gdsfactory_ports` |
| `gf-8-routed.png` | Turn 6 (cont'd) | **After** a straight 40um route (device A's P0 <-> MMI.o1) |
| `gf-9-moved.png` | Turn 7 | Device A dragged +20um in y -- old route now stale, one end dangling |
| `gf-10-rerouted.png` | Turn 8 | **After** re-routing from device A's new position -- 60um step/S-bend |

`gf-8-routed-detail.png` and `gf-10-rerouted-detail.png` are pre-existing
files in the website's asset directory that no current page references
(confirmed by grepping `tutorial-gf-ports.html` / its `en/` twin for every
`gf-*.png` filename). Leave them alone -- this script does not write them,
and per the task brief they are safe to ignore, not stray files to clean up.

## Prerequisites

- A **live KLayout** process with the klink plugin loaded, reachable on
  `--port` (default 8765).
- The interpreter running this script needs **both klink and gdsfactory**
  (scenario 3's `mmi1x2` and scenario 5's route are built with
  gdsfactory-backed helpers) -- see `gf_ports.py`'s own docstring for the
  two supported ways to get one interpreter with both installed.
- **Windows-only, and only for the `gf-7-send*.png` pair**: capturing the
  full KLayout window (toolbar + cell tree, not just the layout canvas that
  `view.screenshot` renders) needs a real desktop screenshot. This needs:
    - the target KLayout window **visible** (not minimized, not fully
      covered) on the Windows session running this script;
    - that window **maximized to exactly 1550x838 px**. The SEND-button
      highlight box / arrow / CN-EN callout chip drawn by `gfsend.ps1` are
      **pixel coordinates** calibrated against that one window size -- any
      other size will misplace the annotation. `capture_send_window.ps1`
      checks this and **refuses to write an image** (exits nonzero, no
      output file) rather than emit a silently-misaligned one, if the size
      doesn't match or no window is found.
    - Window **selection** is done by OS process id, not by title text or
      size alone -- this dev machine routinely has several KLayout windows
      open at once (multiple klink sessions/ports), often at the exact same
      maximized size. `draw_gf_ports_tutorial.py` asks the live server (on
      `--port`) for its own process id via
      `client.exec_python("import os\nos.getpid()")` and passes it to
      `capture_send_window.ps1 -TargetPid`, so the right window is picked
      even with several KLayout windows open side by side.
  Pass `--skip-send-capture` to skip this pair deliberately (e.g. running
  headless, or on a non-Windows box) -- every other PNG is unaffected.

## The `gf-7-send*.png` pipeline (Windows/PowerShell helpers)

`capture_send_window.ps1` is the orchestrator; it chains three small,
already-verified helper scripts shipped alongside it in this directory
(kept byte-for-byte as originally verified -- do not "clean up" or
"simplify" their internals without re-verifying the annotation coordinates
against a real 1550x838 window):

- `cap4.ps1` -- brings the target window to the foreground (does **not**
  resize it) and grabs a raw screenshot via `CopyFromScreen`. `PrintWindow`
  was tried first and rejected: it returns a stale cached frame for an
  occluded Qt OpenGL canvas, which is exactly KLayout's layout view.
- `gfsend.ps1` -- draws the red SEND-button highlight box, an arrow, and a
  CN or EN callout chip (text read from `send-cn.json` / `send-en.json`) on
  top of the raw capture.
- `crop.ps1` -- crops the annotated full-window image down to the top
  285px band (toolbar + cell-tree sidebar + callout), which is what the
  tutorial page actually embeds.

`draw_gf_ports_tutorial.py` drives the RPC side first (zoom to the two
`link0` ports, `selection.set_box` to select them, `selection.send_context`
to fire a real SEND event -- exactly what a user clicking the toolbar SEND
button does), then shells out to `capture_send_window.ps1` for the window
screenshot. This split exists because `view.screenshot` only ever renders
the layout canvas -- it cannot capture the surrounding Qt chrome (toolbar,
cell tree) that the SEND callout needs to point at, so a real desktop
screenshot is the only option for this one figure.

## exec.python escape hatches (both justified, both narrow)

Two uses in `draw_gf_ports_tutorial.py`, same justification pattern as
`gf_mzi_module/draw_gf_mzi_tutorial.py`'s drag simulation (see its
docstring + CLAUDE.md's "Prefer typed RPCs over raw exec.python"):

1. **Simulating the GUI drag** (`gf-9-moved.png`): moves device A's two
   waveguide boxes and its recognized Port instance P0 by `(0, +20um)`. No
   typed RPC repositions an ALREADY-PLACED shape or instance --
   `port.transform` (`klink_plugin/.../methods/port_m.py`) can edit a
   Port's orientation/width/net/etc but its `params_schema` has no x/y
   field at all, so it cannot move one.
2. **Reading the target KLayout process id** (for `-TargetPid` window
   selection, see above): `os.getpid()` inside the live server's own
   process, via `client.exec_python("import os\nos.getpid()")`. This is a
   read-only diagnostic call, not a layout mutation.

## Verification

```bash
python -m pytest tests/unit -q
```

This directory is outside `tests/`, so nothing here is collected by the
unit suite -- the command above is a "didn't break anything else" check,
not a test of this script. Actual verification of this script is: run it
against a live KLayout + gdsfactory interpreter, then look at the PNGs it
wrote (see the Turn table above for what each one should show).

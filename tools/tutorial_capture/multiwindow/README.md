# multiwindow tutorial capture

Regenerates the 6 CN + 6 EN screenshots used by the docs website's
`tutorial-multiwindow.html` / `en/tutorial-multiwindow.html` (cross-window
collaboration: K876x port badges, SEND to durable agent memory, GFTGT klive
target, flat-selection transfer) — the same PNGs are also embedded inline by
`workflows.html` / `en/workflows.html`. Per the ruling in
`tools/tutorial_capture/README.md`: **a tutorial is a step-by-step expansion
of its demo, not a separate artifact** — these screenshots must be
regenerable from a script, not hand-captured once and never touched again.

Unlike the other capture jobs here, there is no starter demo behind this
tutorial — the "demo" IS the cross-session workflow itself:
`draw_multiwindow_tutorial.py` drives TWO live klink sessions at once and
performs a REAL `selection.send_context` and a REAL two-phase
`build_flat_selection_package` → dry-run → commit transfer (the same
functions the `klink.transfer_prepare` / `klink.transfer_commit` MCP tools
call). Every screenshot is a real window state; the returned numbers
(SEND count, dry-run inserted 0, commit inserted 5) are asserted, not
assumed.

## Running

```bash
# from the repo venv, against TWO live KLayout processes with the klink
# plugin loaded -- see Preconditions below
python tools/tutorial_capture/multiwindow/draw_multiwindow_tutorial.py \
    --src-port 8765 --dst-port 8767 \
    --out-dir "D:\klink_website\assets\tutorials\multiwindow"
```

CN PNGs go to `--out-dir`, EN twins to `<out-dir>/en`. The script never
writes or touches any HTML. `--keep-raw` keeps the temporary raw full-window
captures for annotation-coordinate debugging.

## What gets written

| File (CN + en/ twin) | Raw source | Shows |
|---|---|---|
| `step-1-toolbar.png` | src-clean | klink toolbar badges (K8765 / SEND / GFTGT / REC) called out |
| `step-2-two-windows.png` | src-clean + dst-empty | side-by-side montage, both port badges circled |
| `step-3-send.png` | src-send | selection highlighted + real `selection.send_context` |
| `step-4-gftgt.png` | dst-empty | GFTGT: destination marked as the gdsfactory/klive target |
| `step-5-transfer-before.png` | dst-empty | destination before transfer (empty landing frame) |
| `step-6-transfer-after.png` | dst-after | after the flat-selection commit (5 shapes landed) |

## Preconditions

- TWO live KLayout processes with the klink plugin loaded, one per port
  (`--src-port` / `--dst-port`). The tutorial narrates 8765 → 8767; the
  plugin binds the first free port in 8765..8799, so to make a
  freshly-launched second instance land on 8767, hold 8766 with a dummy
  TCP listener while it starts.
- Both windows **visible** (not minimized) and sized to **exactly
  1550x838 px** (outer window rect). The annotation overlay
  (`annotate3.ps1`) is pixel coordinates calibrated against that one size;
  `capture_window.ps1` refuses to capture (nonzero exit, no output file) at
  any other size. Resizing is safe BEFORE the run — the script sets its own
  zoom via `view.zoom_box` after building — but never resize mid-run
  (a resize resets KLayout's zoom).
- Window selection is by **OS process id**, not title/size (this dev
  machine routinely has several identically-sized KLayout windows open).
  Each session's PID is read with
  `client.exec_python("import os\nos.getpid()")` — a read-only diagnostic
  call, the one narrow exec.python escape hatch this script uses.

## Capture methods: cap4 primary, cap_pw fallback

`capture_window.ps1` tries `cap4.ps1` (bring to foreground +
`CopyFromScreen`) first — on an ATTACHED desktop that is the only reliable
method, because `PrintWindow` returns a stale cached frame for an occluded
Qt GL canvas. In a **disconnected RDP session** there is no screen surface
and `CopyFromScreen` throws "The handle is invalid"; capture_window then
falls back to `cap_pw.ps1` (`RedrawWindow` + `PrintWindow`
`PW_RENDERFULLCONTENT`), which IS fresh in that state (verified live: draw
via RPC → capture → the new geometry is in the PNG). In fallback mode,
eyeball the final PNGs for staleness — the expected content of each figure
is the table above.

Disconnected-session quirk seen in practice: a freshly-launched KLayout
window can lose its native `WS_VISIBLE` bit while Qt still reports
`isVisible()==true`, making it invisible to `EnumWindows` (capture fails
with "no visible KLayout window belongs to process id ..."). Native
`ShowWindow` won't fix it because Qt thinks nothing is wrong; cycle it from
inside Qt instead, via the live RPC:
`client.exec_python("import pya\nmw = pya.Application.instance().main_window()\nmw.hide()\nmw.show()")`,
then re-check the size precondition.

## Annotation calibration

`annotate3.ps1`'s toolbar callouts are tied to the fixed 1550x838 window
chrome and survive reruns unchanged. Its two TEAL geometry boxes (step-3
selection, step-6 landed device) additionally depend on the LAYOUT framing,
which is fully determined by the zoom boxes in
`draw_multiwindow_tutorial.py` (`SRC_ZOOM_UM` / `DST_ZOOM_UM`) — if you
change those zooms or the drawn geometry, recalibrate the two `Box`/`Chip`
pixel rects in `annotate3.ps1` (run with `--keep-raw`, measure on the raw
PNGs, re-run `annotate3.ps1` alone over the kept raw dir — no need to
re-drive KLayout). Label text lives in `labels-cn.json` / `labels-en.json`
(UTF-8; keep text out of the .ps1 — PowerShell 5.1 parses scripts as ANSI
and mangles non-ASCII literals). Don't bake run-varying numbers (e.g.
`send_seq`, which increments monotonically per SEND journal) into label
text.

## Side effects on shared state (deliberate, minimal)

- `session.mark_klive_target` is called on the destination session — that
  updates the PERSISTENT klive-target entry in the shared klink registry
  (that is the point of the GFTGT figure). If your fleet had a different
  klive target, re-mark it afterwards.
- The real SEND appends one entry to the source session's send journal
  (`send_seq` advances by one). Harmless — the journal is append-only by
  design.
- Both disposable tabs are closed and the previously-current tab restored
  on each session independently, per the iron rules in
  `tools/tutorial_capture/README.md`; pre-existing tabs are never touched.

## Verification

Run it, then look at the 12 PNGs (table above) and check the printed
summary asserts: `selection.send_context count: 5`, `transfer commit
inserted: 5`, `by_layer: {'10/0': 3, '20/0': 2}`. This directory is outside
`tests/`, so the unit suite never collects it.

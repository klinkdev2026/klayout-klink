# Multilayer P&R engine (`pnr_multilayer`)

klink carries **two fully-decoupled detailed-routing engines**. This package
is the **multilayer engine**.

| | frozen single-stack engine | multilayer engine (this pkg) |
|---|---|---|
| folder | `klink/routing/backends/flexdr/` | `klink/routing/backends/pnr_multilayer/` |
| files | `flexdr.py`, `flexta.py` | `pnr_flexdr.py`, `pnr_flexta.py` |
| seed | greedy net-by-net | track-assignment seed (optional) |
| stack | a few shared routing layers | clean signal layers above the device terminals |
| status | byte-stable reference — do not touch | active |
| scale | small designs, fastest | the path toward large scale |

The layer stack itself is always an example-owned `ProcessProfile` — this
package holds no layer numbers.

## Why two copies

The single-stack engine is locked byte-stable (a frozen oracle guards its
routes). To guarantee that multilayer optimization can **never** break it,
this package is a **complete copy** of the routing engine (`pnr_flexdr.py` =
route + DRC check + pin access + maze; `pnr_flexta.py` = track assignment).

## The boundary (do not cross)

- This package **must not** import from `backends/flexdr/`. It is
  self-contained.
- Both engines share only the frozen **FOUNDATION** (not "the engine"):
  `klink/routing/grid/capacity_grid.py` (grid datastructure),
  `klink/routing/grid/pathfinder.py` (low-level helpers), and the
  `klink_boxmaze_rs` Rust kernel. Treat these as read-only.
- The shared Rust kernel mirrors the *current* Python DRC-check/route-box for
  byte-parity. If this engine diverges from them, disable the Rust path here
  (the Python fallback in `pnr_flexdr.py` is exact); never edit the shared
  kernel for this engine. The engine's own per-net maze kernel
  (`klink_trackmaze_rs`) is optional — a pure-Python fallback produces
  identical results.

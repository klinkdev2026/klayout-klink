# klink ↔ L-Edit bridge (file-exchange RPC)

Connect an AI agent (or any script) to a running Tanner L-Edit: manage
design files and layers, draw geometry, read everything (selection, whole
cells, properties, ports, labels, DRC rules), and work with T-Cells in
both directions — over a plain JSON-files-in-a-folder transport. No
sockets, no firewall prompts, no extra DLLs, nothing to compile.

本目录是 klink 的 L-Edit 桥：通过「JSON 文件交换」让 agent/脚本驱动正在
运行的 L-Edit——管理设计文件与图层、画图、读取一切（选区/整 cell/属性/
端口/标签/DRC 规则表）、双向操作 T-Cell。无套接字、无防火墙弹窗、无 DLL、
无需编译。

```
agent / klink                     L-Edit (ledit_bridge.cpp, load as SOURCE)
    │  write inbox\req_*.json          │  SetTimer polls inbox (200 ms)
    │ ────────────────────────────────▶│  executes UPI calls on UI thread
    │  read outbox\resp_*.json         │  writes response atomically
    │ ◀──────────────────────────────── │
Exchange dir: %LOCALAPPDATA%\klink\ledit_bridge\default\
```

## Files / 文件

- `ledit_bridge.cpp` — the ONE-file UPI macro. Load the SOURCE directly:
  **Tools → Macro → Load Macro…** (L-Edit compiles it itself). Written
  against the old-version UPI subset — older versions compatible by
  design; tested environment is v16.x. We ship zero Siemens/Tanner
  files — the SDK header comes from YOUR L-Edit installation.
- `driver.py` — standalone smoke-test driver (stdlib only):
  `python driver.py ping | demo | selection | layers | cell <name>`;
  importable as `from driver import call`.
- `tcell_workflows.py` — the T-Cell loop (needs `pip install
  klayout-klink`; the package imports as `klink` — the client is
  `from klink.bridges.ledit import LEditBridgeClient`):
  `read` / `variants` / `writeback` / `verify` (byte-exact differential) /
  `fit` (geometry-only: harvest → v3 repeat-group fit → byte-exact gate →
  KLayout PCell).
- `tcell_template.cpp` — byte-exact-verified COPY-AND-ADAPT template for
  generator code you write back. Do not hand-write UPI C++ from scratch.

## Quickstart / 快速上手

1. L-Edit: **Tools → Macro → Load Macro…** → pick `ledit_bridge.cpp`.
   The bridge starts immediately (Tools menu gains *klink: Bridge
   Start/Stop/Status*).
2. `python driver.py ping` → shows macro version, current file/cell and
   the capability list. `"design_ready": false` means no design is open —
   that is fine: call `new_design` (below) and keep going.
3. With klink's MCP configured, agents get `ledit.status`,
   `ledit.import_selection`, `ledit.push_cell` as one-call tools
   (domain `bridge_ledit` in `klink.find_tools`).

## Core flows / 核心流程

**Design targeting / 设计归属**: every command runs against the ACTIVE
design — normal workflow is to draw in the design the user already has
open, NOT to create new ones. Every file-bound command echoes the design
it touched as `result.file`; pass `expect_file` in params to make a
write refuse any other design. Reach for `new_design` only to bootstrap
(no design open) or for an isolated scratch experiment; `new_design` /
`open_design` activate the new file and FAIL if it could not become the
active design (macro ≥ 0.5.1), so a returned `ok` means later writes go
where you intended. 常规=在用户当前设计里画；`result.file` 回显归属，
`expect_file` 拒写别的设计；`new_design` 只用于零设计自举或隔离实验。

**Bootstrap with no design open / 零设计自举**: `ping` reports
`design_ready:false`; call `new_design {name, setup_from_visible?}` to
create a .tdb (optionally inheriting the open design's technology), then
proceed. `open_design {path}` / `save_design {path?}` round out file
management. 没开 tdb 也能开工：`new_design` 直接建库。

**Draw / 画图**: `ensure_layer` (GDS numbers stamped; NEW layers are
auto-colored — solid fill, no outline, deterministic by name) →
`create_cell` → `draw` (box/polygon/wire/circle, batch) →
`place_instance` (single or nx×ny array). Draw is APPEND-ONLY: to
regenerate, `clear_cell {cell}` (explicit cell name required) or use a
fresh cell.

**Read everything / 全量读取**: `list_cells` (T-Cell + hidden-variant
flags) → `get_cell {cell}` returns shapes (with per-object property
trees), instances (transform/array/properties), ports, labels; wires
carry cap/join; torus/pie carry exact params. `get_selection` reads the
user's live selection the same way. `get_layers` includes GDS numbers,
fill color, special-layer flags. `get_drc_rules` exports the design's
whole DRC rule table (machine-readable process knowledge).

**Exchange with KLayout / 与 KLayout 互导**: MCP tools
`ledit.import_selection` (selection → KLayout cell; circles stay
parametric as CIRCLE PCells; layer NAMES migrate — conflicts append
`existing|incoming`, never overwrite) and `ledit.push_cell` (flat
KLayout cell → L-Edit; sub-instances are counted, flatten first).

**T-Cells / 参数化单元**:

```bat
python tcell_workflows.py read NFET_Generator          # params + defaults
python tcell_workflows.py variants NFET_Generator --paramsets "[...]" --out ex.json
python tcell_workflows.py writeback MyGen --code gen.cpp --params "[...]"
python tcell_workflows.py verify MyGen --reference ref.py:boxes --paramsets "[...]"
python tcell_workflows.py fit MyGen --paramsets "[...]" --check "[...]" ^
    --out fit.json --register MY_DEVICE
```

`read` parses the generator source (stored in the cell's
`System.TCell Code` property; parameters are discovered from the typed
`LCell_GetParameterAs*` getter calls). `variants` makes L-Edit itself
generate exemplars (its code runs — geometry is authoritative).
`writeback` turns your generated C++ into a NATIVE parametric T-Cell —
**start from `tcell_template.cpp`**: the template's pattern (need_layer
GDS stamping, typed getters, integer internal units, degenerate-input
guard) is byte-exact-verified; hand-written UPI C++ usually fails
L-Edit's in-place compile, and the compile-error dialog pauses the
bridge until closed. `verify` is the acceptance bar: a
ported/parameterized cell counts as done ONLY on an ALL-BYTE-EXACT
report. 写回请以 `tcell_template.cpp` 为底稿改；验收标准=逐字节一致，
别的都不算数。

**Fit a T-Cell into a KLayout PCell (geometry-only) / 拟合成 KLayout
PCell**: `fit` is the one-call route when you want the T-Cell usable in
KLayout WITHOUT porting its code: it harvests exemplars at your
`--paramsets`, fits the v3 repeat-group model (counts/pitch/positions as
exact integer laws), byte-verifies against FRESH L-Edit variants at the
held-out `--check` points, and with `--register NAME` registers the
KLayout PCell and byte-verifies a live placement too. Read the output —
it is the workflow:

- `REFUSED: <family> ...` — the geometry has structure the model cannot
  express exactly and uniquely. The message names the box family and the
  options. Alternating/parity structure is refused BY DESIGN — and it is
  NORMAL in real devices: MOSFET-style finger arrays almost always route
  odd/even fingers differently (source vs drain), so expect the
  finger-count axis of such a T-Cell to refuse. That is not a dead end:
  **pin the refusing parameter to one value and re-fit** — the fit then
  learns every other axis and the table is honest about the envelope
  (you get the single-value DECIDE warning). For the full axis, use
  `writeback` + `verify` (code handles anything). 叉指器件的奇偶交替是
  常态：把引发 REFUSE 的参数钉在单值重拟合（其余轴照学，包络如实记录），
  要完整轴就走代码移植路线。
- `DECIDE: param X has a single sampled value` — that axis was not
  learned; the fit is only valid at that value. Add exemplars if you
  need the axis.
- `MISMATCH - iterate` at a `--check` point — usually the point crossed
  a count threshold your exemplars never crossed. Find the threshold
  with `variants` (the printed box COUNT changes at the step; bisect the
  parameter until you have it to your finest unit), then add TWO
  exemplars straddling the step (last value before, first value after) —
  that pins the count law uniquely — and re-run.

Sampling rules of thumb: vary EVERY parameter you want learned (>= 2
values); for count-like axes include exemplars on BOTH sides of at least
one count step, ideally straddling it. A fit that only interpolates is
still honest — the table records its sampled envelope and the check
gate refuses extrapolation it cannot prove. 采样规则：要学的参数至少两个
取值；数量型参数必须跨档位，最好骑缝取样（档位前最后一个值+档位后第一
个值），数量律才能唯一钉死。`REFUSED`/`MISMATCH` 输出就是下一步操作指南。

## Command reference (schema 1)

`ping` · `get_layers` · `ensure_layer` · `set_layer_style` ·
`create_cell` · `clear_cell` · `list_cells` · `draw` · `place_instance` ·
`get_selection` · `get_cell` · `get_tcell_params` · `get_drc_rules` ·
`instance_tcell` · `set_tcell_code` · `new_design` · `open_design` ·
`save_design` — all coordinates in microns (doubles); errors carry
`next_action` (read it; it names the fix). Full parameter shapes are in
the header comment of `ledit_bridge.cpp`.

## Landmines / 已知地雷（都踩过，按此绕行）

- **Heartbeat stalls (`hello.json` stale)**: a MODAL DIALOG is open in
  L-Edit (a T-Cell compile error, or a GENERATOR error from instancing a
  T-Cell at invalid parameter values). A human must close it — there is
  NO programmatic recovery, by design (it is a GUI EDA tool). Agents /
  headless runs: treat a stale heartbeat as "stop and tell the user to
  close the dialog in L-Edit", and prevent it up front — keep parameter
  values physically plausible (start from the defaults `read` prints and
  move gradually; e.g. don't instance a MOSFET at W below its contact
  size). 心跳停摆=有人必须去 L-Edit 关弹窗，没有程序化恢复；预防=参数
  取值从 `read` 给的默认值出发渐进探索，别喂物理上不成立的值。
- **Stale T-Cell variants**: after changing a T-Cell's code, identical
  parameter values return the CACHED old variant. Use fresh values or
  Tools → Regenerate T-Cells.
- **`EXCLUDE_LEDIT_LEGACY_UPI`**: don't define it in generator code that
  stamps GDS numbers — the Ex830 layer-parameter calls live in that
  header section.
- **One namespace, one L-Edit**: two L-Edit instances with the macro
  loaded would fight over the same inbox. Keep one.
- **GDS numbers are tape-out critical**: bridge-created layers stamp
  them; generator code written back must stamp them too (`need_layer`
  pattern in `tcell_template.cpp`). A layer showing `-1` will not
  export correctly.

## Scope honesty / 边界说明

Parametric intelligence lives on the klink/KLayout side (fitting,
transpilation, verification); L-Edit receives native results (static
cells or real T-Cells). Not yet: TTL/idempotency journal reload across
restarts, response chunking, multi-instance namespaces, DRC-marker
readback.

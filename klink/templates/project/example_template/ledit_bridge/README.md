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
- `draw_device_demo.py` — **the copy-and-adapt starter**: a static
  two-finger NMOS over five layers (`python draw_device_demo.py --new
  my_lib`). Shows the four things that are easy to get wrong: design
  targeting with `expect_file`, idempotent redraw (`draw` only appends),
  one batched request instead of a dozen round trips, and read-back
  verification. 改尺寸/画 PMOS/做 inverter 都从这个文件改起。
- `tcell_workflows.py` — the T-Cell loop (needs `pip install
  klayout-klink`; the package imports as `klink` — the client is
  `from klink.bridges.ledit import LEditBridgeClient`):
  `read` / `variants` / `writeback` / `verify` (byte-exact differential) /
  `fit` (geometry-only: harvest → v3 repeat-group fit → byte-exact gate →
  KLayout PCell) / **`to_pcell`** (the code-true route `fit` REFUSE points
  to: verify a ported Python reference byte-exact, then register it as a
  live KLayout PCell — see the T-Cell → PCell section) / **`from_pcell`**
  (the other direction: scaffold T-Cell generator code from a live
  KLayout PCell — see the PCell → T-Cell section).
- `tcell_template.cpp` — byte-exact-verified COPY-AND-ADAPT template for
  generator code you write back. Do not hand-write UPI C++ from scratch.

## Quickstart / 快速上手

1. L-Edit: **Tools → Macro → Load Macro…** → pick `ledit_bridge.cpp`.
   Loading it IS the start — the poll timer begins immediately and the
   Tools menu gains *klink: Bridge Start/Stop/Status*; those are for
   pausing and resuming an already-loaded macro, not for the first run.
   **Running L-Edit is not enough — the macro must be loaded, and it is
   not restored after L-Edit restarts.** `python -m klink.doctor` prints
   the macro's absolute path, so you can paste it into the dialog.
   L-Edit can also preload it at launch: `ledit64.exe -u <that path>`
   (`-U` loads and runs it) — put that in your L-Edit shortcut and it is
   loaded every time. There is no way to inject it into an L-Edit that is
   ALREADY running. 加载即启动；重启 L-Edit 后不会自动恢复，快捷方式加
   `-u <宏路径>` 可以一劳永逸，但已经开着的 L-Edit 只能手动加载。
   Changing the .cpp needs a RELOAD to take effect — run *klink: Bridge
   Stop* first, or two timers poll the same inbox.
2. `python -m klink.doctor` → reports the bridge (namespace, macro
   version, heartbeat, current design) next to the KLayout checks, or
   says it is not configured. `python driver.py ping` is the
   no-klink-needed version.
3. `python draw_device_demo.py --new my_lib` → draws a real device
   (two-finger NMOS, five layers). **Start here when adapting.**
4. With klink's MCP configured, agents get `ledit.status`,
   `ledit.import_selection`, `ledit.push_cell` as one-call tools
   (domain `bridge_ledit` in `klink.find_tools`). In plain Python the
   same health check is `LEditBridgeClient().status()`.

**Exchange directory / 交换目录**: defaults to
`%LOCALAPPDATA%\klink\ledit_bridge\`. Set **`KLINK_LEDIT_BRIDGE_ROOT`**
to relocate it when that path is not writable (sandboxed agent,
redirected profile) — the client, `driver.py` and the macro all read it,
so set it BEFORE starting L-Edit and in the calling process, then reload
the macro. 沙箱写不了 `%LOCALAPPDATA%` 时用这个变量改交换目录，三端都认，
但要在启动 L-Edit 前设好并重载宏。

## Core flows / 核心流程

**Bind the guard once / 一次绑定全程守卫**: in Python, prefer
`LEditBridgeClient().bind_active()` (or `.bind_file("my_lib")`). The guard
then rides on every request AND on every op inside a `batch`, so the
convenience wrappers stay usable — `ensure_layer(...)` / `create_cell(...)`
have no room for a per-call `expect_file`, and passing it per call is what
pushed callers back to raw `call()` with hand-built dicts. One forgotten
guard is a write into the user's own design. Verified live: after the
active design changes, a bound write is REFUSED ("active design is X but
expect_file is Y") and nothing leaks into the other design.

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
auto-styled with a HATCHED fill, never solid — you read a layout by
seeing THROUGH the stack, and a solid fill hides the very overlap that
shows a contact landing on its metal; colour and pattern are both
deterministic by layer name, so layers stay separable. `set_layer_style`
takes `fill:"hatch"` (default) `|"solid"|"none"`. 自动配色改为**图案填充**，
实心会把叠层糊死) →
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
python tcell_workflows.py to_pcell MyGen --reference ref.py:boxes ^
    --params-spec "[...]" --paramsets "[...]" --check "[...]" ^
    --register MY_DEVICE
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
  `to_pcell` (code handles anything — see the section below). 叉指器件的
  奇偶交替是常态：把引发 REFUSE 的参数钉在单值重拟合（其余轴照学，包络
  如实记录），要完整轴就走 `to_pcell` 代码移植路线。
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

## Where the work happens / 活该在哪儿干 (read this first)

**L-Edit is where the user's design lives. KLayout is where klink's
capability lives. The bridge exists to move geometry between them — not
to make L-Edit do the computing.**

The bridge's own command set is deliberately small: draw, read, place,
manage designs/layers/T-Cells. That is the transport, not the toolbox. So
when a user asks for something *in L-Edit*, the first question is not
"which bridge command does this?" but:

> **Does klink already do this on the KLayout side?**
> If yes: `ledit.import_cell_tree` (or `import_selection`) → do the work
> with the real klink API in KLayout → `ledit.push_cell_tree` (or a GDS
> via `import_gds`) to put the result back.

用户在 L-Edit 里提的需求，先问「klink 有没有这个能力」。有 → **读出来 →
在 KLayout 侧做 → 写回去**。L-Edit 负责「设计在这里」，不负责计算。

| The user asks, in L-Edit | Route it through |
|---|---|
| route these ports / nets, avoid obstacles | `routing.*` backends, `photonics.connect` |
| check the layout / run DRC / does it match the netlist | `drc_run`, `structdevice.lvs_check` |
| place a netlist, do P&R | `structdevice.build_from_netlist` |
| fill a region, boolean ops, density, XOR two cells | `cell.fill_region`, `geometry.*` |
| **is A inside B? do these layers overlap? is every contact covered?** | `geometry.boolean`, `drc_run` — *not* rectangle math you write yourself |
| generate a photonic circuit / gdsfactory component | `photonics.import_gf`, `routing.gdsfactory_ports` |
| a cross-section, a 3D view, an SEM-style figure | `imaging.*` |
| turn measured geometry into a parametric device | `pcell.register_fitted`, `tcell_workflows.py fit` |
| draw a device / edit shapes / manage designs | the bridge directly — this is its job |

### The failure mode, concretely

Reimplementing klink's capability yourself is what this section exists to
prevent — and it does NOT only mean rebuilding a router out of `draw`
calls. The commoner temptation is quieter: **read the coordinates out with
`get_cell` and reason about them in Python.** That looks harmless for a
question as simple as "is this box inside that one", which is exactly why
it slips through.

It was measured. A capable agent was asked to check a two-finger NMOS and
chose "Python + coordinate math over KLayout tools, because the check is
purely geometric". Its rectangle arithmetic was correct. Its conclusion was
not: it reported a **CRITICAL DESIGN ERROR — "NPLUS covers the gate, the
transistor can never turn off"**, which is simply how a self-aligned device
is drawn. The n+ SELECT mask is meant to enclose the whole device; the poly
blocks the implant, so no channel doping results. Every standard PDK draws
it that way. Hand-rolled geometry gave the user a confident, alarming,
wrong answer.

The lesson is not that the arithmetic was hard. It is that geometry
questions carry PROCESS meaning, and a tool that encodes the process
(a DRC deck, `geometry.boolean` against the real layers) does not invent
rules the way an agent reasoning from first principles will. 手搓矩形运算
算得对不代表结论对——几何问题带着工艺含义，凭常识发明规则就会像这样自信
地报出一个假的「致命错误」。

### Doing it, concretely

klink already answers these; the reason agents hand-roll instead is not
missing capability, it is not knowing the incantation. Two tools:

```python
# layer identity is spelled DIFFERENTLY per tool -- shape.query takes a
# layer_index, geometry.boolean takes "L/D" or {layer, datatype}
LD = {e["name"]: "%d/%d" % (e["layer"], e["datatype"])
      for e in k.call("layer.list")["layers"] if e.get("name")}

def area(cell, op, a, b):
    return k.call("geometry.boolean", {
        "op": op,                       # and / or / xor / not  (not = a minus b)
        "a": {"cell": cell, "layer": LD[a]},
        "b": {"cell": cell, "layer": LD[b]}})["area_um2"]

area(cell, "not", "CONTACT", "MET1")    # >0 : a contact with no metal over it
area(cell, "and", "MET1",    "POLY")    # >0 : metal touching a gate
```

`drc.run` is the other half: it takes DRC DSL source INLINE and, with no
`source()` line, runs against the layout already loaded — no deck file to
write, no path to configure.

Measured on the two-finger NMOS in this starter, comparing a broken draft
against the fixed cell (µm², 0 = clean):

| check | broken | fixed |
|---|---|---|
| `CONTACT` on neither `ACTIVE` nor `POLY` | **0.1152** | 0.0000 |
| `CONTACT` not covered by `MET1` | 0.0000 | 0.0000 |
| `POLY` not enclosed by `NPLUS` | 0.0000 | 0.0000 |
| `MET1` overlapping `POLY` | 0.4000 | **0.3872** |
| `CONTACT` touching `POLY` | 0.1824 | **0.1152** |

0.1152 is exactly two 0.24 µm contacts sitting on nothing — the defect,
located without a line of rectangle arithmetic.

**And read the last two rows before you trust any of this.** Both are
NONZERO on the CORRECT cell. `MET1` overlaps `POLY` because the gate bus is
*supposed* to cross the poly pads, and `CONTACT` touches `POLY` because the
gate contact is *supposed* to land on one — that is how a gate gets
connected at all. A rule like "a contact must not touch poly" would flag a
correct device, and an agent that reasoned its way to that rule once
reported a broken cell for the right verdict on the wrong evidence. A
truthful number is not a verdict: whether an overlap is a short or a connection is DESIGN
INTENT, which no layer-relation rule knows. State the intent you are
checking, and when you cannot, say the check is inconclusive rather than
calling it a finding. 工具给的数字是诚实的,但"重叠"是短路还是连接取决于
设计意图——说不清意图就说结论不确定,别当成缺陷报出去。

So: if the question is about geometry, route it. If klink has no such
capability either, say so plainly instead of approximating it with
primitives — and if you do fall back to your own analysis, label the answer
as unverified rather than as a finding.

Two things do NOT survive the round trip automatically, so decide before
you start: KLayout **PCells become static geometry** in L-Edit unless you
port them (see the next section), and a GDS trip maps layers by NUMBER
rather than by name.

## PCell -> T-Cell: keep it parametric / 参数化怎么带过去

`push_cell_tree` moves a PCell's *shapes*; the parameters stay behind. To
land a real, editable T-Cell in L-Edit, generate its UPI generator and
write it back. Do not hand-write that C++: a compile error pops a modal
dialog that freezes the bridge until a human closes it. Scaffold it.

```bash
# 1. scaffold from the live KLayout PCell (KLayout must be running)
python tcell_workflows.py from_pcell MY_PCELL --library MyLib \
    --params '{"w": 0.5, "n": 4}' --out gen.cpp
# 2. edit gen.cpp: the geometry is emitted as the PCell's ACTUAL boxes at
#    those parameters, in integer internal units. Replace the constants
#    with formulas over the parameters -- that edit IS the port.
# 3. write it back as a native T-Cell
python tcell_workflows.py writeback KLINK_MY_PCELL --code gen.cpp \
    --params gen.params.json
# 4. acceptance: ALL-BYTE-EXACT, nothing less counts
python tcell_workflows.py verify KLINK_MY_PCELL \
    --reference ref.py:boxes --paramsets "[...]"
```

What the scaffold hands you, already correct: the `need_layer()` helper
copied from the byte-exact-verified `tcell_template.cpp` (GDS stamping
included — a layer left at -1 exports wrong), typed getters for every
parameter UPI can actually read, layer creation for every layer the PCell
drew, a degenerate-input guard, and the `UPI_Entry_Point`. It reports what
it could NOT scaffold rather than guessing: parameter types with no UPI
getter (strings, shapes, layer objects — UPI has only Layer/Coord/Double/
Boolean/Int), and non-box shapes, which you add with `LPolygon_New` /
`LWire_New`.

Rules of thumb: sample the PCell at parameters that make the structure
obvious (a count of 3 or 4, not 1 — you cannot see a repeat law in one
copy), and keep every coordinate in integer internal units, because that
is what makes the byte-exact gate possible at all. 采样时数量参数取 3~4,
一个副本看不出重复律;坐标全程整数内部单位,逐字节验收才成立。

For the opposite direction (an existing T-Cell you want usable in
KLayout), `tcell_workflows.py fit` already does it geometry-only, with the
same byte-exact gate; when `fit` REFUSES, `to_pcell` (next section) is the
full-code fallback.

## T-Cell -> PCell: the transpile route / T-Cell 反向带参数过来

`fit` REFUSES geometry its locked v3 model cannot express exactly and
uniquely — alternating/parity finger structure, M*L bilinear extents, that
kind of thing (normal in real devices, see the `fit` section above). The
refusal is not a dead end; it names the exit: port the generator's LOGIC
(not its fitted geometry envelope) to Python and register THAT function as
a live KLayout PCell — full parameter fidelity, any code, no model class
to fit into. `fit` 的 REFUSE 不是死路,是指路牌:把生成器的**逻辑**(不是
拟合出来的几何包络)搬成 Python,把这个函数本身注册成活的 KLayout PCell —
参数完整保真,任意代码,没有模型类限制。

```bash
# 1. read the T-Cell's parameters + generator code
python tcell_workflows.py read NFET_Generator
# 2. port the geometry logic into a Python reference function --
#    def nfet_boxes(params: dict) -> {layer_name: [[x0,y0,x1,y1] int-nm,
#    ...], ...} (harvest-native: same dict shape `verify` already uses).
#    That edit IS the port.
# 3. to_pcell verifies BYTE-EXACT against L-Edit first (nothing is
#    registered on a mismatch), THEN registers the SAME function as a
#    live KLayout PCell and byte-checks a placed instance against a
#    FRESH L-Edit variant
python tcell_workflows.py to_pcell NFET_Generator \
    --reference nfet_ref.py:nfet_boxes \
    --params-spec "[{\"name\":\"L\",\"type\":\"int\",\"default\":2}, ...]" \
    --paramsets "[{\"L\":2,\"W\":5,\"M\":1}, ...]" \
    --check "[{\"L\":3,\"W\":8,\"M\":2}, ...]" \
    --register NFET_DEVICE
```

Acceptance is ALL-BYTE-EXACT at every stage, not "close enough": the
reference must reproduce every `--paramsets` point before anything is
registered, and the registered PCell's live KLayout placement must match a
FRESH L-Edit variant at every `--check` point. 验收全程逐字节:先过
`--paramsets` 逐字节验证才准注册,注册后再核对活的 KLayout 摆放跟新鲜
L-Edit 变体逐字节一致 — 缺一步都不算数。

Unlike `fit`, there is no envelope limit here: any code the reference
function can express registers, at the price of porting it yourself
instead of letting the fitter infer it.

## Bulk transfer: what is actually slow / 批量写回的真实瓶颈

Measured on v16.3, not guessed: **drawing is nearly free (~0.02 ms per
shape, flat as the cell fills), and the cost is one poll interval PER
REQUEST.** So the lever is fewer requests, never smaller payloads.
实测：画图几乎免费，成本按**请求数**计——要快就减少请求数，不是减小批量。

| Need | Use | Why |
|---|---|---|
| An ORDERED sequence (create_cell → draw → place_instance) | `batch {ops:[...]}` | N commands, one request, stops at the first failure |
| Many INDEPENDENT requests | `LEditBridgeClient.pipeline([...])` | one tick drains the whole inbox — measured ~10x over blocking calls |
| A cell SUBTREE, keeping layer names | `ledit.push_cell_tree` / `ledit.import_cell_tree` | cells in dependency order, instances stay instances |
| A whole DESIGN, or thousands of shapes | `import_gds` | keeps the hierarchy, no size cap, `overwrite:"all"` is idempotent |
| Incremental edits, T-Cells | either RPC path | GDS flattens parametric content to static geometry |

## Hierarchy / 层级传输

Both lanes keep the hierarchy; they differ in what else they keep.

- **RPC** — `ledit.push_cell_tree` (KLayout → L-Edit) and
  `ledit.import_cell_tree` (L-Edit → KLayout), or in Python
  `from klink.bridges.ledit import push_cell_tree, import_cell_tree`.
  Cells are created children-first, instances are rebuilt as real
  instances (arrays and orthogonal rotations included), layer identity
  travels **by NAME**, each cell is cleared before redraw so re-running
  is idempotent, and the whole tree goes over as ONE batch. Anything
  L-Edit placement cannot express exactly — a magnification, a
  non-orthogonal rotation, a skewed array — is listed in
  `unsupported_instances` with a fix, never approximated.
- **GDS** — `import_gds` for a whole design. Faster for thousands of
  shapes, but it maps layers by NUMBER and turns parametric content
  static.

Measured on a 3-level tree (leaf shapes on two layers, a 2×2 array, a 90°
placement): RPC push = 15 ops in one 0.18 s request, round-trips back into
KLayout with the array still an array. 两条路都保层级：RPC 路保图层名和
参数化、可只推一个子树、幂等；GDS 路适合整库搬家。

`ledit.push_cell` / `ledit.import_selection` remain the FLAT single-cell
tools — they report sub-instances rather than silently flattening them.

## Command reference (schema 1)

`ping` · `batch` · `get_layers` · `ensure_layer` · `set_layer_style` ·
`create_cell` · `clear_cell` · `list_cells` · `draw` · `place_instance` ·
`get_selection` · `get_cell` · `get_tcell_params` · `get_drc_rules` ·
`instance_tcell` · `set_tcell_code` · `new_design` · `open_design` ·
`save_design` · `list_designs` · `activate_design` · `import_gds` — all
coordinates in microns (doubles); errors carry `next_action` (read it; it
names the fix). Full parameter shapes are in the header comment of
`ledit_bridge.cpp`.

**Designs / 设计切换** (macro >= 0.5.2): every command targets the VISIBLE
design, so start from `list_designs` (name, path, visible, changed, cell
count) and switch with `activate_design {file}` — BY NAME, no disk path,
so a design created by `new_design` and never saved is reachable too. It
raises an existing window rather than opening a cell, so nothing inside
the design is touched. `open_design {path}` still loads from disk.

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
- **GDS needs the 2048-byte block padding**: L-Edit's reader expects the
  classic Calma padding; KLayout does not write it. Unpadded, the import
  aborts AT EOF ("Unexpected element", file position == file size),
  `LFile_ImportGDSII` still returns OK, empty cell shells are left
  behind, and a MODAL dialog freezes the bridge until a human closes it
  (measured: 40 s frozen vs 0.11 s once padded). The klink client's
  `import_gds()` pads a sibling `*.ledit.gds` copy for you and never
  edits your file; macro >= 0.5.2 also reads the import log and refuses
  to report success on an aborted import. 用 KLayout 写的 GDS 必须补齐到
  2048 字节整数倍，否则 L-Edit 在文件末尾报错并弹模态框冻住桥。
- **A request over 64 KiB**: the cap is on the REQUEST, not the batch
  count — 250 flattened polygons are 104 KiB while 250 boxes are not.
  Macro < 0.5.2 dropped such a request without answering OR deleting it
  (a full-timeout stall plus a file re-read on every tick); 0.5.2 answers
  `ERR_REQUEST_TOO_LARGE` and consumes it, and the client refuses to send
  one at all. `draw()` chunks itself, so pass items to it rather than
  hand-building a `call("draw", ...)`.
- **A timeout does NOT mean it did not run**: the request may already have
  been delivered and executed. `draw` is append-only and every retry gets
  a fresh id, so blindly re-sending DOUBLE-draws. Check with `get_cell`
  first, or `clear_cell` and redo.
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

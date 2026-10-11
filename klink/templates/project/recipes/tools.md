# Recipe: a toolbar button that runs a deterministic tool

## What a tool is

A **tool** is a folder `<id>/` with `tool.json` (the interface) + `run.py` (the
body). `tool install` turns it into a KLayout **toolbar button** (presets become
its dropdown entries). A click runs it inside KLayout -- no agent, no external
Python -- as **one undo step** (an exception rolls the edit back), and every run
is **journaled** (`tool log`). klink ships **no installed tools**; three working
examples are in `example_template/tools/` (`bool_region_macro` = a macro dropped
in unchanged, `bool_region` = layers recognised from the selection + one entry
per operation, `bool_selection` = a boolean OF the selected shapes).

## The pipeline

Run every command from the project root. Do the steps **in order**; do not go
on while a **Gate** fails. `python -m klink.cli` is the CLI (a `klink` on PATH
may be an older install). The pipeline is `tool new -> check -> install -> run -> log`.

**0. Clarify (one question, skip what the user already said).** What does the
button do? What must be selected (nothing / N shapes / a marker box over the
area)? Which layers are inputs, which is the output? Variants (= presets)?

**1. Scaffold.**

```bash
python -m klink.cli tool new <id> --min-selected N --title "What it does" --label ABC
python -m klink.cli tool new <id> --from example_template/tools/bool_region   # variant of an example
python -m klink.cli tool new <id> --macro                                     # the user hands over a working macro
```

`<id>` = `^[a-z][a-z0-9_]{0,39}$` (also the folder name). The folder is
`.klink/tools/<id>/` (`--dir` changes the parent). `--label` = toolbar text, 6
characters shown (default: the id's first 6, upper case). `--min-selected N`
writes `inputs.selection.min`. With `--macro`, replace the scaffolded `run.py`
by the user's macro **unchanged** (it must not define a top-level `run`).
**Gate:** you see `created .klink/tools/<id>  (...)  [run.py form: ...]` and a
`next:` list of four commands. Else: `already exists and is not empty` -> pick
another id; `must match ^[a-z]...` -> fix the id.

**2. Edit `tool.json`.** Declare every setting the tool needs as a param.

| Key | Meaning |
|---|---|
| `id` | required; equals the folder name |
| `title` | required; dialog title + undo entry `klink tool: <title>` |
| `label` | toolbar text, 6 characters shown |
| `description` | free text |
| `inputs.selection.min` | at least N selected objects (shapes or instances), else klink refuses before any edit |
| `inputs.selection.min_shapes` | at least N selected **shapes** (instances do not count) |
| `params` | name -> `{"type", "default"?, "title"?, "doc"?, "unit"?, "min"?, "max"?, "choices"?}` |
| `presets` | name -> `{"title"?, "params": {...}}`; each preset = one dropdown entry |
| `prompt` | `"missing"` (default: a click asks every param the entry does not fix), `"always"`, `"never"` (ask nothing) |

Param `type`: `int`, `number`, `string`, `bool`, `enum` (needs `choices`),
`layer` (accepts `1/0`, `1`, `1,0`, `1 0`; `params` always holds `"L/D"`).
Extra keys of a `layer` param:

| Key | Meaning |
|---|---|
| `from` | `"region"` (layers under the selection), `"selection"` (layers of the selected shapes/instances), `"layout"` (all layers): the form shows a drop-down instead of a text field |
| `exclude` | list of other layer param names whose value is left out (`layer_b` excludes `layer_a`) |
| `allow_new` | `true` adds `new layer...` (asks an L/D, the `default` proposed) -- for outputs |
| `allow_same` | `true` accepts the value `"same"` (meaning is up to `run.py`) |

Replace the scaffold's placeholder `description` with what the tool does.
Rule: **every param the click must not ask for needs a `default` or a value in
the clicked preset.** A param without `default` is required. Input layers:
use `from` instead of a hard-coded default. Copy the exact shape from
`example_template/tools/bool_region/tool.json`.
Icon (optional, only when the user asks for one): put a 24x24 or 32x32 PNG
(or SVG) in the tool folder and set `"icon": "icon.png"`; an agent may
generate it (e.g. with Pillow if installed, or a hand-written SVG). The button
then shows the icon with the label under it; without `icon` it is text only.
**Gate:** none yet -- step 4 checks it.

**3. Write `run.py`** (skip for an unchanged `--macro`). The scaffold's
docstring is the contract; summary:

| Topic | Contract |
|---|---|
| entry | `def run(ctx, params):` called once per click, inside ONE undo transaction |
| `ctx` | `ctx.view`, `ctx.cellview`, `ctx.layout`, `ctx.cell` (insert here), `ctx.dbu`, `ctx.selection` (selected SHAPES), `ctx.selected` (every selected object), `ctx.tool_dir`, `ctx.log`, `ctx.layers(scope)`, `ctx.pya` |
| params | already validated + coerced: int / float / bool / str / enum; a layer is `"1/0"` |
| units | database units (integers): `int(round(um / ctx.dbu))` |
| layer index | `ln, dt = (int(v) for v in params["out"].split("/"))`; `li = ctx.layout.layer(pya.LayerInfo(ln, dt))` |
| insert | `ctx.cell.shapes(li).insert(pya.Box(x1, y1, x2, y2))` (or Polygon / Path / Region) |
| selected shape | `obj.shape`, `obj.layer` (layer index), `obj.trans()` (into `ctx.cell`); test `shape.is_box()` / `is_polygon()` / `is_path()` / `is_text()` first |
| booleans | `pya.Region(shape.polygon.transformed(obj.trans()))`; `a & b`, `a - b`, `a + b`, `a ^ b`, `r.sized(d)`, `r.merged()` |
| order | read + validate EVERYTHING first, then edit |
| user error | `raise ValueError("<what is wrong>; <what to do>")` -- shown verbatim, nothing changed |
| transaction | none of your own (klink already opened one) |
| dialogs | none (klink asks the params in one form) |
| output | `print` ONE summary line (goes to the status bar) |
| result | `return` a small JSON-serialisable dict (journaled) |

pya cheat-sheet -- the mistakes that break tools:

1. Coordinates in um passed to `pya.Box` (wants dbu integers) -> convert with `ctx.dbu`.
2. Layer NUMBER used as layer INDEX (`ctx.cell.shapes(5)`) -> `ctx.layout.layer(pya.LayerInfo(5, 0))`.
3. `shape.polygon` on a text or box without checking the type -> test `is_text()` / `is_box()` first.
4. Shapes inside instances used without `obj.trans()` -> always `.transformed(obj.trans())`.
5. `view.transaction()` / `commit()` -> remove; one click is already one undo step.
6. `pya.InputDialog` / `MessageBox` / Qt dialogs -> declare a param; raise `ValueError`.
7. `sys.exit(1)` -> reported as a failed run; raise `ValueError` instead.
8. `run` returns nothing or a non-dict -> the run fails and is rolled back; return `{...}`.
9. Editing, then validating -> validate first (a raise after an edit still rolls back, but the message comes late).
10. Assuming something is selected -> set `--min-selected` / check `ctx.selection` and raise.
11. Hard-coded layer numbers (`1/0`) -> a `layer` param with `from` (and a default for outputs).
12. `print` of polygons / big lists -> one line; put counts in the returned dict.

**4. Check (no KLayout needed).**

```bash
python -m klink.cli tool check .klink/tools/<id>
```

**Gate:** the last line is `ok: 0 error(s), N warning(s)` and the `params:`
table shows what you meant (`(required)` = no default; `asked` column). Else:
`FAILED: ...` -> fix every `ERROR:` line and check again. Read every
`warning:` line too (own transaction, dialogs, non-zero exit, `def run` with
the wrong arguments, label longer than 6, a required param with
`"prompt": "never"`); each names the line and the fix.

**5. Install.**

```bash
python -m klink.cli tool install .klink/tools/<id>
```

**Gate:** `installed <id> -> <registry>/tools/<id>`, `button <registry>/buttons/<id>.json`
(plus `(entries: ...)` with presets) and `toolbar: installed=['<id>'] ...` (or
`updated=['<id>']` on a reinstall); the button is in KLayout's toolbar. Else:
`KLayout not reachable ...` -> KLayout is not running, the button appears at
its next start (go on once it runs); an error message -> back to step 4.

**6. Run it headless before anyone clicks.** Ask the user to select the input
(or set it via MCP `selection.set_box`), then:

```bash
python -m klink.cli tool run <id> [--preset p] [--param name=value ...]
python -m klink.cli tool run <id> --interactive    # opens the form in KLayout instead
```

A non-interactive run asks nothing: a layer param with several candidates ends
in `[ERR_TOOL_PARAMS] ...` whose `next:` line lists the candidates -- pass one
with `--param layer_a=1/0` and run again.
**Gate:** the first line is `ok: <id> preset=... selection=N ... ms`, then a
`stdout:` block (what `run.py` printed) and the result dict. Verify the
geometry (MCP `shape.query` / `layer.list`) and that ONE `edit.undo` reverts
the whole run (a layer the run created disappears too, so `shape.query` on it
then says `layer ... not present`). Undo also clears the selection: select
again before running again. Else: `[ERR_TOOL_INPUT]` -> wrong / too small selection;
`[ERR_TOOL_RUN]` -> read the message + `traceback (tail):`, fix `run.py`, go
to step 4.

**7. The user clicks once; read the journal.**

```bash
python -m klink.cli tool log <id> -n 3
```

**Gate:** the newest record (printed last; headless runs are journaled too,
a click's header ends in `interactive`) says `OK` with the params and result. `CANCELLED` = the user closed the form. `ERROR` -> read `error: [CODE]
message` and `hint:`, fix `run.py` / `tool.json`, go back to step 4 (step 5
replaces the installed copy). `no runs recorded` -> the click never reached
klink (see Troubleshooting).

**8. Report to the user:** the button label, its dropdown entries (presets),
what the form asks (the `asked` column of step 4), where the source lives
(`.klink/tools/<id>/`, versioned) and how to share it
(`python -m klink.cli tool export <id> <id>.zip`; the receiver runs
`tool install <id>.zip`).

## Where files live

- **Source, in this project:** `.klink/tools/<id>/` (`tool.json` + `run.py`
  [+ README.md]). Versioned: the project `.gitignore` ignores the rest of
  `.klink/` but keeps `.klink/tools/`. klink never runs it from here -- you
  **install FROM it**.
- **Installed, user level:** `<registry root>/tools/<id>/` + the button
  manifest `buttons/<id>.json` (`%LOCALAPPDATA%\klink\registry` on Windows,
  `~/Library/Application Support/klink/registry` on macOS,
  `~/.local/state/klink/registry` on Linux; `KLINK_REGISTRY_ROOT` overrides).
  `python -m klink.cli tool list` prints the folder and each tool's state.
- **Examples:** `example_template/tools/` are sources only (nothing is
  installed by `klink init`); copy one with `tool new <id> --from ...`.
  Released klink ships no tools.
- **Update:** edit the source, then steps 4-5 again. **Remove:**
  `python -m klink.cli tool uninstall <id>` (button + folder).

## Agent procedure

Follow the pipeline above, step by step, gates included. MCP side
(`klink.find_tools domain=tools`): `tool.list`, `tool.get`, `tool.run`
(`interactive=false` by default: pass every param without a value; the
`ERR_TOOL_PARAMS` hint lists candidates), `buttons.list`, `buttons.reload`
(after hand-editing an installed `tool.json`). `new`, `check`, `install`,
`log`, `export`, `uninstall` are CLI only (Python: `klink.tools`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| button missing after install | KLayout was not running / klink not loaded: start it, or call `buttons.reload`; `tool list` shows `UNUSABLE: <reason>` for an invalid tool |
| `check` warning about a transaction / dialog / `sys.exit` | remove it (see the cheat-sheet); warnings do not block install but the run misbehaves |
| `check` warning "no default and prompt never" | a click can never ask that param: give it a default, pin it in every preset, or use `"prompt": "missing"` |
| `[ERR_TOOL_PARAMS] ... pick ...` from `tool run` | several candidate layers; the `next:` line lists them: pass `--param name=L/D`, or use `--interactive` |
| "needs at least N selected object(s)" | select more, or lower `inputs.selection.min` |
| "layer X does not exist" | a default does not match this layout's layers: fix it, or use a `from` param, and reinstall |
| the form shows an unexpected layer first | `from: "region"` puts the selected shapes' layers first when they lie on 2+ layers (operands: preselected as A, B); a single selected shape is a marker, its layer is listed LAST and the layers under it first; `[selected]` tags selected layers; counts stop at `200+` |
| the form will not close on OK | a field fails validation (reason in red); fix it, or give the param a `default` and `"prompt": "never"` |
| one click undoes more/less than expected | `run.py` opens its own transaction -- remove it |
| `invalid choice: 'tool'` | the `klink` that `python` imports is older than this project: `python -m pip install -U klayout-klink` in that interpreter |
| `tool log` says `no runs recorded` | the click never reached klink: klink plugin not loaded in KLayout, or the CLI uses another registry root (`KLINK_REGISTRY_ROOT`); `tool list` shows which folder the CLI reads |

---

## 中文版：流水线

在项目根目录执行全部命令，**按顺序**做；任何一步的 **闸门** 没过就不要往下走。
CLI 一律用 `python -m klink.cli`（PATH 上的 `klink` 可能是旧版本）。流水线是
`tool new -> check -> install -> run -> log`。

**0. 澄清（一个问题，用户已说过的跳过）。** 按钮做什么？需要选中什么（不选 / N 个图形 /
一个框住区域的标记框）？哪些图层是输入、哪个是输出？有没有变体（= presets）？

**1. 生成骨架。**

```bash
python -m klink.cli tool new <id> --min-selected N --title "做什么" --label ABC
python -m klink.cli tool new <id> --from example_template/tools/bool_region   # 示例的变体
python -m klink.cli tool new <id> --macro                                     # 用户给了能跑的宏
```

`<id>` 须符合 `^[a-z][a-z0-9_]{0,39}$`（也是文件夹名），生成在 `.klink/tools/<id>/`
（`--dir` 改父目录）。`--label` 是工具栏文字（显示前 6 个字符），`--min-selected N`
写入 `inputs.selection.min`。用 `--macro` 时，把生成的 `run.py` 换成用户的宏，
**一字不改**（宏里不能有顶层 `run`）。
**闸门：** 看到 `created .klink/tools/<id>  (...)  [run.py form: ...]` 和含四条命令的
`next:` 列表。否则：`already exists and is not empty` -> 换个 id；`must match ^[a-z]...`
-> 改 id。

**2. 编辑 `tool.json`。** 工具需要的每个设置都声明成 param（键表见上文英文部分）。
param 类型：`int`、`number`、`string`、`bool`、`enum`（要 `choices`）、`layer`
（`params` 里永远是 `"L/D"`）。图层 param 额外键：`from`（`region` / `selection` /
`layout`，表单里变下拉框）、`exclude`（排除其他图层 param 已选的值）、`allow_new`
（加 `new layer...`，用于输出层）、`allow_same`（接受 `"same"`）。
把骨架里占位的 `description` 改成工具实际做什么。
规则：**点击时不应询问的每个 param 都必须有 `default` 或在所点的 preset 里有值。**
输入层用 `from`，不要写死默认值。照抄 `example_template/tools/bool_region/tool.json` 的结构。
图标（可选，仅当用户要求时）：把一个 24x24 或 32x32 的 PNG（或 SVG）放进工具文件夹，
并设 `"icon": "icon.png"`；可由 agent 生成（例如已安装 Pillow 时用它，或手写 SVG）。
按钮随后显示图标、标签在图标下方；不设 `icon` 就只有文字。
**闸门：** 本步暂无，第 4 步检查。

**3. 写 `run.py`**（原样宏可跳过）。骨架 docstring 就是契约，要点见上文英文契约表：
`def run(ctx, params)` 每次点击调用一次、已在一个撤销事务里；坐标用数据库单位整数
（`int(round(um / ctx.dbu))`）；图层号要先换成图层索引
（`ctx.layout.layer(pya.LayerInfo(ln, dt))`）；结果插入 `ctx.cell`；选中图形先判类型、
再 `.transformed(obj.trans())`；先全部校验再编辑；用户错误抛
`ValueError("哪里错; 怎么办")`；不开自己的事务、不弹自己的对话框、不 `sys.exit(1)`；
只 `print` 一行摘要；返回一个小 dict。易错清单见上文英文 cheat-sheet 第 1-12 条。

**4. 检查（不需要 KLayout）。**

```bash
python -m klink.cli tool check .klink/tools/<id>
```

**闸门：** 最后一行是 `ok: 0 error(s), N warning(s)`，且 `params:` 表与你的意图一致
（`(required)` = 无默认值；`asked` 列 = 何时询问）。否则：`FAILED: ...` -> 修完每一条
`ERROR:` 再检查。每条 `warning:` 也要读（自开事务、对话框、非零退出、`def run` 参数不对、
label 超过 6 个字符、`"prompt": "never"` 下的必填 param），每条都写明行号和改法。

**5. 安装。**

```bash
python -m klink.cli tool install .klink/tools/<id>
```

**闸门：** 看到 `installed <id> -> <registry>/tools/<id>`、`button <registry>/buttons/<id>.json`（有 preset 时带
`(entries: ...)`）和 `toolbar: installed=['<id>'] ...`（重装时是 `updated=['<id>']`），
KLayout 工具栏里出现按钮。否则：`KLayout not reachable ...` -> KLayout 没运行，下次启动时
按钮出现（等它运行后继续）；报错 -> 回第 4 步。

**6. 先无头运行，再让人点。** 请用户选中输入（或经 MCP `selection.set_box` 设置），然后：

```bash
python -m klink.cli tool run <id> [--preset p] [--param name=value ...]
python -m klink.cli tool run <id> --interactive    # 改为在 KLayout 里弹表单
```

非交互运行什么都不问：图层 param 有多个候选时返回 `[ERR_TOOL_PARAMS] ...`，`next:` 行
列出候选——用 `--param layer_a=1/0` 传一个再跑。
**闸门：** 第一行是 `ok: <id> preset=... selection=N ... ms`，随后是 `stdout:` 块（`run.py`
打印的内容）和结果 dict。用 MCP `shape.query` / `layer.list` 核对几何，并确认**一次**
`edit.undo` 撤销整个运行（本次新建的图层也会消失，此时对它 `shape.query` 会报
`layer ... not present`）。撤销也会清空选区：再跑之前重新选中。否则：`[ERR_TOOL_INPUT]` -> 选区不对/不够；`[ERR_TOOL_RUN]` -> 读消息和
`traceback (tail):`，改 `run.py`，回第 4 步。

**7. 用户点一次；读日志。**

```bash
python -m klink.cli tool log <id> -n 3
```

**闸门：** 最新一条（最后打印；无头运行也会记录，点击的记录头部带 `interactive`）是 `OK`，
带 params 和 result。`CANCELLED` = 用户关了表单。
`ERROR` -> 读 `error: [CODE] message` 和 `hint:`，改 `run.py` / `tool.json`，回第 4 步
（第 5 步会替换已安装副本）。`no runs recorded` -> 点击没到达 klink（见故障排查表）。

**8. 向用户汇报：** 按钮标签、下拉项（presets）、表单会问什么（第 4 步 `asked` 列）、
源码位置（`.klink/tools/<id>/`，纳入版本管理）、如何分享
（`python -m klink.cli tool export <id> <id>.zip`，对方 `tool install <id>.zip`）。

文件位置：源码在本项目 `.klink/tools/<id>/`（从这里 install）；安装后在用户级
`<registry root>/tools/<id>/`；`example_template/tools/` 只是示例源码，用
`tool new <id> --from ...` 复制；发布的 klink 不预装任何工具。

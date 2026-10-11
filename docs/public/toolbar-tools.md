# Toolbar tools

Turn a small script into a KLayout toolbar button — one click, one undo step,
no agent in the loop when you click it.

> 中文见 [toolbar-tools.zh-CN.md](toolbar-tools.zh-CN.md)

```text
tool.json + run.py           a folder: the interface + the body
  -> klink tool install      becomes a toolbar button (presets = dropdown entries)
  -> click                   one form, OK, the edit runs inside KLayout
  -> Ctrl+Z                  undoes the whole run in one step
```

## What you get

A **tool** is a folder `<id>/` with `tool.json` (what it is called, what must be
selected, which settings it takes) and `run.py` (what it does).
`tool install` turns it into a KLayout **toolbar button**; each preset becomes
one entry of the button's dropdown. A click runs the tool inside KLayout — no
external Python process, no agent:

- the whole run is **one undo step** (`klink tool: <title>` in the undo list);
- if `run.py` raises, the partial edit is **rolled back** and the message is
  shown; nothing is left half-done;
- every run, clicked or scripted, is **journaled** (`tool log`);
- all settings are asked in **one form**, like KLayout's own Boolean dialog.

klink ships **no installed tools** — your agent writes them for you. Three
working examples come with `klink init`, under `example_template/tools/`:

| Example | Button | What it does |
|---|---|---|
| `bool_region` | `BOOL2` | boolean of two layers inside the selection (AND / OR / NOT / XOR entries) |
| `bool_region_macro` | `MACRO` | the same, as a KLayout macro dropped in unchanged |
| `bool_selection` | `BOOL` | boolean **of** the selected shapes (Union / Intersection / Subtract / XOR) |

**Plain buttons** are the lower level: `python -m klink.cli button add <id>
--label ABC` adds a toolbar button whose click only journals a record and emits
a `button_triggered` event — whatever listens (an agent, a script) decides what
it means. A tool is a button whose click klink runs for you.

## Try it in 2 minutes

In a `klink init` project, with KLayout running and klink loaded:

```bash
python -m klink.cli tool install example_template/tools/bool_region
```

A `BOOL2` button appears in the toolbar. Select two overlapping shapes on
different layers, then click `BOOL2` (or one of its entries). One window opens:

- **layer A** / **layer B**: drop-downs of the layers under the selection, with
  the two selected shapes' layers preselected (A = first, B = second);
- **operation**: the entry's, changeable;
- **output layer**: the layout's layers plus `new layer…` with an L/D field;
- **keep what is already on the output layer**.

Press OK: the result lands on the output layer and the status bar shows one
confirmation line. **Ctrl+Z** undoes it all, including a layer the run created.

A single marker box drawn over an area works too: the layers under it are
listed first and the marker's own layer last.

## Ask your agent for a button

Say what the button should do and what you will select ("AND layers 1/0 and
2/0 inside the box I draw, output on a new layer"). The agent follows
`recipes/tools.md` in your project — a fixed pipeline where every step has a
gate it must pass before going on:

```bash
python -m klink.cli tool new <id> --min-selected 1 --title "What it does" --label ABC   # gate: "created .klink/tools/<id>"
python -m klink.cli tool check .klink/tools/<id>      # gate: "ok: 0 error(s)", params table as intended (no KLayout needed)
python -m klink.cli tool install .klink/tools/<id>    # gate: "installed <id>", button in the toolbar
python -m klink.cli tool run <id> --preset p          # gate: "ok: <id> ...", geometry verified, one undo reverts it
python -m klink.cli tool log <id> -n 3                # gate: your click is journaled as OK
```

`tool new --from example_template/tools/bool_region` starts from an example;
`tool new --macro` wraps a working macro you already have, unchanged.

**Where files live.** The **source** is in your project, `.klink/tools/<id>/`
(`tool.json` + `run.py` [+ `README.md`]), and is versioned with it — the
project `.gitignore` keeps `.klink/tools/`. klink never runs it from there; it
**installs from it** into the user-level registry:

| OS | Installed copy |
|---|---|
| Windows | `%LOCALAPPDATA%\klink\registry\tools\<id>` |
| macOS | `~/Library/Application Support/klink/registry/tools/<id>` |
| Linux | `~/.local/state/klink/registry/tools/<id>` |

`KLINK_REGISTRY_ROOT` overrides the registry root; `python -m klink.cli tool
list` prints the folder and each tool's state. To update: edit the source, then
`check` + `install` again. To remove: `python -m klink.cli tool uninstall <id>`.

## tool.json at a glance

| Key | Meaning |
|---|---|
| `id` | required; `^[a-z][a-z0-9_]{0,39}$`, equals the folder name |
| `title` | required; form title + undo entry `klink tool: <title>` |
| `label` | toolbar text, 6 characters shown |
| `description` | free text |
| `icon` | optional `.png` / `.svg` file next to `run.py`; without it the button is text-only |
| `inputs.selection.min` | at least N selected objects (shapes or instances), else refused before any edit |
| `inputs.selection.min_shapes` | at least N selected **shapes** (instances do not count) |
| `params` | name -> `{"type", "default"?, "title"?, "doc"?, "unit"?, "min"?, "max"?, "choices"?}`; types `int`, `number`, `string`, `bool`, `enum` (needs `choices`), `layer` (always `"L/D"` in `run.py`) |
| layer `from` | `"region"` (layers under the selection), `"selection"` (layers of the selected objects), `"layout"` (all layers): a drop-down instead of a text field |
| layer `exclude` | other layer params whose value is left out (`layer_b` excludes `layer_a`) |
| layer `allow_new` | adds `new layer…` (asks an L/D, the `default` proposed) — for outputs |
| layer `allow_same` | accepts the value `"same"` (meaning is up to `run.py`) |
| `presets` | name -> `{"title"?, "params": {...}}`; each preset = one dropdown entry |
| `prompt` | `"missing"` (default: ask what the entry does not fix), `"always"`, `"never"` |

A param without `default` is required. For input layers use `from` instead of a
hard-coded layer number. `example_template/tools/bool_region/tool.json` is the
reference shape.

## run.py in ten lines

```python
def run(ctx, params):      # called once per click, already inside ONE undo step
    # ctx.view, ctx.layout, ctx.cell (insert here), ctx.dbu, ctx.selection (selected shapes),
    # ctx.selected (every selected object), ctx.layers(scope), ctx.tool_dir, ctx.log, ctx.pya
    # params are validated + coerced (int / float / bool / str / enum; a layer is "1/0")
    # coordinates are database units: int(round(um / ctx.dbu))
    # read + validate everything first, then edit
    # user error: raise ValueError("what is wrong; what to do")  -> shown verbatim, nothing changed
    # no transactions and no dialogs of your own: klink provides both
    print("one summary line")  # goes to the status bar
    return {"count": 0}        # a small JSON-serialisable dict, journaled
```

## From scripts and agents

```bash
python -m klink.cli tool run <id> --preset p --param name=value
python -m klink.cli tool run <id> --interactive    # opens the form in KLayout instead
```

A non-interactive run asks nothing: a layer param with exactly one candidate is
filled in; with several, the run stops with `[ERR_TOOL_PARAMS]` and its `next:`
line lists the candidates to pass (`--param layer_a=1/0`).

Over MCP the `tools` domain has `tool.list`, `tool.get`, `tool.run`
(non-interactive by default), `buttons.list` and `buttons.reload` (after
hand-editing an installed `tool.json`); `klink.find_tools domain=tools` shows
them with usage. `new`, `check`, `install`, `log`, `export` and `uninstall` are
CLI only.

## Sharing

```bash
python -m klink.cli tool export <id> <id>.zip     # zip an installed tool
python -m klink.cli tool install <id>.zip         # the receiver installs it
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| button missing after install | KLayout was not running or klink not loaded: start it, or call `buttons.reload`; `tool list` shows `UNUSABLE: <reason>` for an invalid tool |
| `check` warning about a transaction / dialog / `sys.exit` | remove it; warnings do not block install but the run misbehaves |
| `[ERR_TOOL_PARAMS] ... pick ...` from `tool run` | several candidate layers: pass `--param name=L/D` from the `next:` line, or use `--interactive` |
| "needs at least N selected object(s)" | select more, or lower `inputs.selection.min` |
| "layer X does not exist" | a default does not match this layout's layers: fix it or use a `from` param, then reinstall |
| the form will not close on OK | a field fails validation (reason in red): fix it |
| one click undoes more/less than expected | `run.py` opens its own transaction — remove it |
| `invalid choice: 'tool'` | the `klink` that `python` imports is too old: `python -m pip install -U klayout-klink` |
| `tool log` says `no runs recorded` | the click never reached klink: plugin not loaded, or the CLI reads another registry root (`KLINK_REGISTRY_ROOT`; `tool list` shows which) |

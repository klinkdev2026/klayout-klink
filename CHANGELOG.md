# Changelog

## 0.7.0

- Custom toolbar buttons. A button is a JSON manifest under `<registry root>/buttons/` managed with `klink button add / remove / list`; the KLayout toolbar picks changes up through the `buttons.reload` RPC without a restart. A click is journaled and emitted as a `button_triggered` event; a button may carry dropdown entries. Optional `icon` (PNG or SVG) next to the label; without it the button is text only.
- Deterministic tools: a folder with `tool.json` + `run.py` becomes a toolbar button that runs inside KLayout as one undo step. `run.py` is either an unmodified KLayout pya macro or `def run(ctx, params)`. Presets become dropdown entries. If the run raises, the partial edit is rolled back and the message is shown; every run is journaled; the last printed line goes to the status bar. klink ships no tools; three example tools come with `klink init` under `example_template/tools/` as sources.
- Parameters are asked in one form, laid out like KLayout's Boolean Operations dialog: every param in one window, layer params as drop-downs of the layers actually present (`"from": "region" | "selection" | "layout"`, with `exclude`, `allow_new` -> `new layer…`, `allow_same`), the layers of the selected shapes first and preselected, a single-layer marker's own layer last. Non-interactive runs fill a single candidate and otherwise return `ERR_TOOL_PARAMS` with the candidates. Layers are accepted as `1/0`, `1`, `1,0` or `1 0`.
- A command-driven pipeline for writing tools: `klink tool new <id>` scaffolds `.klink/tools/<id>/` with a `run.py` that documents the whole `ctx`/`params` contract and runs as is; `klink tool check <dir>` validates `tool.json` and `run.py` without KLayout and reports every error and warning at once; `klink tool install / uninstall / list / export / run`; `klink tool log <id>` prints the run journal (status, params, result, printed output, error + hint). `klink init` projects get `recipes/tools.md`: the numbered procedure with a gate per step, the `tool.json` key tables, the `run.py` contract and a pya cheat-sheet; `AGENTS.md` / `CLAUDE.md` and the klayout skill point to it. Project tool sources live in `.klink/tools/` (kept by the project `.gitignore`).
- MCP: new `tools` domain — `tool.list`, `tool.get`, `tool.run`, `buttons.list`, `buttons.reload` (`klink.find_tools domain=tools`); the layout-build lane may run tools.
- Fixes: the KLayout plugin's session heartbeat no longer leaves `.tmp` files behind when the atomic replace fails on Windows (retry, clean up, sweep stale files); the layer-panel refresh after a write RPC that created a layer stays inside the undo transaction; a rollback no longer matches a user's own undo entry of the same title.

## 0.6.5

- In viewer mode, a write RPC that needed a layout tab no longer leaves an empty tab behind: the editor-mode check now runs before the default tab is created, with the same `ERR_VIEWER_MODE` instruction.
- `KLinkClient.connect()` is idempotent. The documented `with KLinkClient().connect() as c:` form used to connect twice and start two reader threads.
- Write RPCs are atomic on failure. When an RPC body fails after it already changed the layout, the partial edit is rolled back before the error is returned, and the error's `data.transaction.partial_edit` says what happened (`none`, `undone`, `left_as_undo_entry`, `unknown`). The rollback only ever undoes the entry the RPC itself created, never a user's own edit; the reverted edit stays available as redo until the next edit. A write for which no undo transaction could be opened is refused with `ERR_TXN_STATE` instead of being applied without undo, and a failed commit is reported (`applied: true`) instead of being swallowed. Previously a failing RPC could leave a half-applied edit in the layout while returning an error, so retrying a tool was not safe.

## 0.6.4

- Viewer mode is refused up front instead of failing mid-edit. When KLayout runs without `-e`, every write RPC returns `ERR_VIEWER_MODE` with the restart instruction before touching the layout; read-only RPCs keep working. Previously the first `shape.insert_*` failed with KLayout's "No undo/redo support on non-editable shape lists" (reported in public PR #19). Writes are never applied without an undo transaction, so an agent cannot change a layout the user is unable to undo.
- `hello`, the client handshake, `klink.status` (`editor_mode`) and `python -m klink.doctor` report whether KLayout is in editor mode and name the fix when it is not.
- Documentation states that KLayout must be started in editor mode (`klayout -e`).

## 0.6.3

- Create task run folders at the `klink init` project root as `runs/<run>/`, beside `custom_devices/`, instead of nesting generated scripts and artifacts under `custom_devices/runs/`.
- Generate run drivers with the corrected project-root lookup and keep root-level `runs/` untouched during `klink update`.

## 0.6.2

- Update `klink init` project guidance for Vestigraph checkpoint grouping, pending manual-edit capture, 30-item default history, and explicit additive restore.
- Add a worked `vestigraph.restore` flow to generated Agent and Claude instructions while preserving the requirement that the user selects the checkpoint.

## 0.6.1

- Align the MCP/plugin version reported by the live KLayout server with the 0.6.1 release.
- Document Codex, Pi, and Kimi Code skill installation and MCP registration.


## 0.6.0

- Registered local companion services can start with KLayout and expose their toolbar actions.
- Optional Vestigraph integration provides the HIST entry for local layout history.
- Installed MCP extensions provide discovery instructions through `klink.status` and `klink.find_tools`.
- Joint installation and upgrade instructions keep compatible Python packages, the KLayout plugin and companion registration aligned.
- KLink remains usable without Vestigraph. User histories, evidence and private skills are not included in this distribution.
- Client and editor plugin identify as 0.6.0. Rust accelerators identify as 0.6.0 and stay within the compatible 0.6.x dependency range.

See the README for supported workflows and installation instructions.

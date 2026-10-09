# Changelog

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

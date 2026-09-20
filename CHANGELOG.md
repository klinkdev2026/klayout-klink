# Changelog

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

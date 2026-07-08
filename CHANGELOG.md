# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project does not use dated entries (versions only).

## Unreleased

- KLayout plugin bundled into the wheel, plus `klink plugin install` /
  `klink plugin status` CLI commands.
- abi3 kernel wheels for the Rust accelerators (broader future CPython
  compatibility from a single build per platform).
- The Python client fails fast with an instructive error when the KLayout
  connection drops mid-session, instead of hanging.
- `klink.doctor --scan` to find a live session across a port range, plus new
  informational checks for the Rust kernels and the `klayout` pip package
  version floor.
- Documentation fixes.

## 0.1.1

- Fixed a non-recursive `package-data` glob that dropped all
  `example_template` starters from the wheel; starters are now packaged
  recursively.
- `example_template` starters regrouped into categorized subdirectories
  (nanodevice, photonics, passives, digital), including the digital
  place-and-route family.
- `fit_device` flow decoupled from the KLayout plugin package — the fitted-edge
  math now lives in the pip package, so the starter imports only `klink`
  (running the P&R/LVS stages still needs a live KLayout session).
- Passive-device template usability polish.
- Version-compatibility CI matrix covering supported `gdsfactory` and
  `klayout` pip version lines.
- New `klink update` command to refresh the bundled starter templates
  without touching user files.

## 0.1.0

- Initial public release.
- KLayout RPC control plane: an in-process KLayout plugin server plus a
  typed Python client.
- MCP bridge with profile/domain navigation (`klink.find_tools`) so agents
  can discover tools by intent and area instead of a flat list.
- Routing backends for both digital (place-and-route) and photonic
  (gdsfactory bridge) workflows.
- Digital place-and-route → live LVS flow for custom, fitted devices.
- Nanodevice and photonics starter examples.
- Two Rust acceleration kernels (`klink-boxmaze-rs`, `klink-trackmaze-rs`)
  shipped as prebuilt wheels, with pure-Python fallbacks.
- `klink init` project scaffold and `klink-mcp --register` for one-command
  MCP client registration.

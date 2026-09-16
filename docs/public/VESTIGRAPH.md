<p align="right">
  <a href="VESTIGRAPH.md">English</a> | <a href="VESTIGRAPH.zh-CN.md">中文</a>
</p>

# Vestigraph local history

The full Klink + Vestigraph feature set requires installing Vestigraph in the same Python environment as `klink-mcp`. It provides local file and saved-layout history, HIST, and agent history tools. Use Python 3.10+, KLayout desktop 0.30.x, and Klink 0.6.0 or a compatible later 0.6.x release.

## Install and synchronize

```console
python -m pip install "vestigraph>=0.2,<0.3"
klink plugin install
# restart the MCP client that runs klink-mcp
# restart KLayout, open a saved GDS/OASIS layout, then click HIST
python -m vestigraph doctor --integration
```

`pip install vestigraph` installs compatible `klayout-klink` and `vestigraph-scan-core` dependencies. The Rust scanner is selected automatically when its native module is available. `klink plugin install` installs or upgrades the KLayout plugin. Restarting MCP lets the existing Klink extension registry discover Vestigraph and register its companion for that Python environment. No extra MCP server is needed. Installing packages does not configure a chat client.

For upgrades, stop the old Vestigraph service if one is running, upgrade packages, rerun `klink plugin install`, then restart MCP and KLayout. Keep Vestigraph, Klink MCP, and companion registration in the same Python environment.

## MCP discovery

```json
{"tool":"klink.find_tools","arguments":{"domain":"vestigraph"}}
```

Then call `vestigraph.guide` with `{}` and follow `next_action`. Existing requests use `vestigraph.skill` to read, then `vestigraph.submit` to save a draft and check document structure. New requests use `vestigraph.history`, `vestigraph.refine`, then `vestigraph.submit`. When the user asks for an export, `vestigraph.export` writes a local file.

## Enable local refinement

Experimental and disabled by default. Stop the old service first.

PowerShell:

```powershell
$env:VESTIGRAPH_EXPERIMENTAL_SKILLS = "1"
python -m vestigraph serve --control-file --open-browser
```

POSIX shell:

```sh
VESTIGRAPH_EXPERIMENTAL_SKILLS=1 python -m vestigraph serve --control-file --open-browser
```

For companion startup, KLayout must inherit this environment. For a custom service state, configure `VESTIGRAPH_CONTROL_FILE` in the MCP environment to point at its local control file. Never paste the secret into an agent prompt.

## Local data and verification

History, frozen evidence, drafts, revisions and exports stay in user storage. Private skills are not shipped in either product. The adapter uses authenticated loopback HTTP and does not upload, execute or install skills. The user's agent client determines how returned information is handled.

Submission checks document structure only. Author statements are separate from platform checks. Saved-file differences do not prove GUI action order, replay, DRC/LVS or process intent. On a revision conflict, read the latest skill before changing it.

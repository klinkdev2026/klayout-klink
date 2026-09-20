# Vestigraph - local history and skill refinement

`klink init` ships this guide; it does not install or start Vestigraph.
Vestigraph stores saved file versions and layout history locally, with a timeline,
previews, and old-version import and recovery.

## Setup

Use the Python interpreter that runs your existing klink MCP server:

```sh
python -m pip install "vestigraph>=0.2,<0.3"
klink plugin install
python -m vestigraph doctor --integration
```

Restart MCP and KLayout after installing or upgrading. MCP discovers the extension
and registers the local companion. No extra MCP server is needed. Open a saved
GDS/OASIS layout and click **HIST**. Package installation does not configure your
agent client's MCP connection; this project's `mcp.example.json` describes the
existing klink server.

## Example: find layout history

Ask: "Find the saved history of my layout in Vestigraph." First make these two
separate MCP calls:

```json
{"tool":"klink.find_tools","arguments":{"domain":"vestigraph"}}
{"tool":"vestigraph.guide","arguments":{}}
```

Inspect the live schemas and follow `next_action`. Use returned project/document
ids with `vestigraph.history` to query versions. Clarify which document the user
means if there are multiple candidates. Old-version import and recovery are
available in HIST. Use the explicit `vestigraph.restore` tool only after the
user selects a checkpoint.

## AI editing and checkpoint summaries

Save an unsaved editor document into this project before editing. The local
history store retains snapshots; it does not write your working source file.
With Klink 0.6.x and Vestigraph 0.2.2 or newer, Klink MCP waits for pending
manual edits to be checkpointed before sending an AI mutation. Continuous AI
edits are grouped after the configured idle window, rather than stored as a
separate checkpoint for every inserted shape.
Provide a short `_reason` for editor write tools when their live schema includes
it; the checkpoint records that explanation. Do not include private source
content or credentials in the reason.

`vestigraph.history` normally returns the 30 most recent summaries, including
manual checkpoints. Each summary includes its ID, title, source, save time and
recorded explanation. A modification time is reported only when observed; it
is not inferred from the save timestamp. Set `all: true` only when the user
explicitly requests the complete checkpoint list. Use the local web panel for
full event details and comparisons.

## Example: restore a selected checkpoint

Ask: "Restore checkpoint CHECKPOINT_ID and keep the rest of my history."

1. Resolve the active saved document and KLayout session with `vestigraph.guide` and `vestigraph.history`.
2. If the user has not named a checkpoint, show the recent summaries and ask them to select one. Do not choose on their behalf.
3. Call `vestigraph.restore` with the selected document, checkpoint, active session, and a short user-approved reason.
4. Read history again and report the new restore checkpoint. The selected content becomes current, while every earlier and later checkpoint remains available.

Restore saves pending manual editor changes first, replaces the saved working file atomically, reloads the same path in KLayout, and appends a checkpoint carrying `restore_of`. It requires a saved live document; an unsaved layout must be saved into the project first.

## Example: draft a skill from an existing request

In the history page, select two versions as the range start and end. Enter the
name, goal, applicability, parameters and acceptance checks, then save a request
for an agent. Vestigraph freezes the evidence window.

Ask: "Read my pending Vestigraph request and draft a reusable instruction."

1. Call `vestigraph.guide` to locate the request.
2. Call `vestigraph.skill` with the returned request id and arguments required
   by its live schema. Read frozen evidence and the current revision.
3. Draft instructions separating observed facts, user intent and inference.
4. Call `vestigraph.submit` using the live schema and `expected_revision` from
   the revision just read. Relay `problems` and follow `next_action`.
5. Review revisions in the local skill library. When the user requests a file,
   use `vestigraph.export` for the selected revision.

On conflict, read again and compare before resubmitting.

## Example: create a request through the agent

Ask: "Turn the changes between these two saved versions into a reusable skill."
Use `guide -> history` to resolve document and version ids, then `vestigraph.refine`
to create a request and freeze evidence for the explicitly selected range. Follow
its returned instructions to read and submit the draft. Do not guess the range,
fabricate ids or invent tool arguments.

## Enable experimental refinement

History does not require experimental skill refinement. For refinement, stop the
old service and enable the switch in the environment starting the new service.

PowerShell:

```powershell
$env:VESTIGRAPH_EXPERIMENTAL_SKILLS = "1"
python -m vestigraph serve --control-file --open-browser
```

macOS / Linux:

```sh
VESTIGRAPH_EXPERIMENTAL_SKILLS=1 python -m vestigraph serve --control-file --open-browser
```

If KLayout starts the companion, KLayout must inherit this switch. Restarting only
the browser does not change a running service's environment. For custom service
state, configure `VESTIGRAPH_CONTROL_FILE` in the MCP environment to point at the
local control file. Do not paste secrets or login links into prompts.

## Verification and local data

Submission validates document structure. It does not execute attachments or prove
layout replay, DRC, LVS or manufacturability. File differences are not GUI action
logs; they do not establish edit order or a universal process rule. Publishing is
local catalog state and does not upload, install or execute skills. Keep private
history, evidence and exports in user storage outside public version control.

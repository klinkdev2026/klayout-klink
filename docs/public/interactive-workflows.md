# Interactive workflows

> 中文见 [interactive-workflows.zh-CN.md](interactive-workflows.zh-CN.md)

Three capabilities make the human + agent + live-KLayout loop workable in
practice: SEND selection memory (what "this area" means), the multi-session
registry with cross-session transfer, and the recorder. All three are ordinary
tools — nothing here needs special setup beyond the plugin.

## "This area" — SEND selection memory

When you select geometry in KLayout and click the plugin's **SEND** toolbar
action (or an agent calls `selection.send_context`), the selection is recorded
as a stable id such as `sel_0006` in session-scoped memory
(`.klink/sessions/<session-id>/interaction_context.jsonl`). From then on,
phrases like "just sent", "this area", or "that one" resolve to exact
geometry, not to a screenshot.

The agent-facing tools:

```text
interaction.selection.recent   -> latest stored selections (default latest 5)
interaction.selection.latest   -> latest stored selection
interaction.selection.get      -> exact stored id
interaction.selection.label    -> attach a name/description to an important id
interaction.context            -> current live selection plus recent memory
```

`selection.get` is the *live* current selection; `interaction.*` is the
durable memory of what was explicitly sent. Memory is resolved by order and
count, not age — layout work can take minutes between a SEND and the message
that refers to it.

SENDs are durable: the plugin journals every SEND with a monotonic sequence
number *before* broadcasting it, so a SEND made while no agent is listening is
not lost — the bridge catches up from the journal and deduplicates when it
next connects.

## Many KLayouts, one bridge — sessions and transfer

Every running KLayout window binds a port (8765, 8766, …) and registers as a
session. Sessions are **equal peers** — no port has a privileged role; you
always say which session you mean.

```text
klink.session_list      -> enumerate running sessions
klink.session_label     -> attach a human label / aliases to one
klink.session_resolve   -> turn a label, alias, or active cell into a session id
klink.session_use       -> repoint the bridge's primary RPC target
```

Moving geometry between sessions is two-phase and confirmation-safe:
`klink.transfer_prepare` builds a package (`flat_selection` for merged shapes,
`shallow_instance` for instances), **dry-runs it against the target session**,
and persists it as pending; `klink.transfer_commit` then writes it. Nothing
lands in the target until the commit, so a wrong-target mistake is caught at
the dry-run stage.

## Recorder — edits become a script

The recorder turns a working session — manual GUI edits and agent RPC edits
alike — into a replayable script:

```text
recorder.start    -> begin recording (optionally naming the output path)
recorder.status   -> is it recording, how many events/actions so far
recorder.stop     -> write the script, return stats + wrote=true/false
```

Stopping writes **two artifacts**: a `KLinkClient` replay script (`<name>.py`,
annotated with `# user command:` lines naming the menu actions that produced
each step) and a standalone `pya` companion (`<name>_pya.py`) that runs inside
KLayout with no klink installed.

The recorder is a **replay-script generator, not a literal call logger**: it
records whatever actions rebuild the final layout state, so a bulk RPC or an
`exec.python` snippet may appear as expanded per-object actions. Check
`recorder.status` before starting a recording of your own so you never clobber
one already in progress.

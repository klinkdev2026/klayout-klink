# bool_region_macro: a KLayout macro dropped in unchanged

This is **step 1** of the "macro -> button" guide (`recipes/tools.md` in a klink project; `docs/TOOL_AUTHORING.md` in the klink repo):
`run.py` is a pya macro exactly as written for KLayout's Macro Development IDE
(it finds the current view itself, has its layers in a config block and ends
with `if __name__ == "__main__": main()`). Nothing in it was changed for klink.
`tool.json` is one line: an id, a title, a button label, and "at least one
object selected" (so an empty selection gets klink's friendly info box
instead of the macro's own `ValueError`).

Install it (adds a `MACRO` toolbar button):

```bash
python -m klink.cli tool install <path to this folder>   # e.g. example_template/tools/bool_region_macro in a klink project
```

Variant of this: `python -m klink.cli tool new <new_id> --from <path to this folder>` (then follow `recipes/tools.md` of a klink project from step 2).

Select the region (shapes and/or instances), click `MACRO`: the AND of layers
1/0 and 100/0 inside the selection lands on 3/0. No dialog. klink wraps the
macro so that the run is one undo step (Ctrl+Z), an exception rolls the edit
back and shows its message, the macro's `print` lines are journaled and the
last one appears in KLayout's status bar.

To use other layers, edit the config block in `run.py` and reinstall. The same
macro after step 2 (config block -> layer params the tool recognises from what
is under the region, one preset per operation -> dropdown entries) is
[`bool_region`](../bool_region/).

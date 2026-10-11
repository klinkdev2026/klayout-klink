# bool_selection: Boolean on selection

An example klink deterministic tool (`tool.json` + `run.py`). It merges,
intersects, subtracts or xors the selected shapes into one result, as one undo
step.

Install it (adds a `BOOL` toolbar button with Union / Intersection / Subtract /
XOR entries):

```bash
python -m klink.cli tool install <path to this folder>   # e.g. example_template/tools/bool_selection in a klink project
```

Variant of this: `python -m klink.cli tool new <new_id> --from <path to this folder>` (then follow `recipes/tools.md` of a klink project from step 2).

Select at least two shapes, then click an entry. The entry fixes the
operation; KLayout asks for the target layer from a list (`same layer as the
first selected shape` first, then the layout's layers, then `new layer…`) and
whether to keep the source shapes. From a script or agent:

```bash
python -m klink.cli tool run bool_selection --preset union
python -m klink.cli tool run bool_selection --preset intersect --param target=2/0 --param keep=yes
```

Remove it with `python -m klink.cli tool uninstall bool_selection`. Format and contract:
`recipes/tools.md` in a klink project (`docs/TOOLS.md` in the klink repo).

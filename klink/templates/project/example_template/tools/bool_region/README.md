# bool_region: Boolean of two layers inside the selection

An example klink deterministic tool (`tool.json` + `run.py`). The selection does
not take part in the boolean: it only **defines a region**. The tool takes layer
A and layer B of the current cell (with its whole hierarchy), clips both to that
region, combines them (`A and B`, `A or B`, `A not B`, `A xor B`) and writes the
result into an output layer, as one undo step.

- a selected shape adds its outline to the region (boxes, polygons, paths; texts
  add nothing);
- a selected instance adds the layer A and layer B geometry inside it.

Install it (adds a `BOOL2` toolbar button with A AND B / A OR B / A NOT B /
A XOR B entries):

```bash
python -m klink.cli tool install <path to this folder>   # e.g. example_template/tools/bool_region in a klink project
```

Variant of this: `python -m klink.cli tool new <new_id> --from <path to this folder>` (then follow `recipes/tools.md` of a klink project from step 2).

**No layer numbers anywhere.** Select the two operand shapes (or a marker
shape / instances over them; at least one object), then click an entry. One form opens (like KLayout's own
Boolean Operations dialog), headed by what is selected
(`Selected: 1 shape (50/0 x1)`). The tool looks at what is under the region
itself:

- **layer A** and **layer B**: drop-downs of ALL the layers that have
  geometry under the region (with the layer panel's name when the layer has
  one; ` [selected]` marks layers of selected shapes). Operands selected (on
  two layers): their layers come first, in selection order, so A starts on the
  first operand and B on the second. A marker selected (one layer): the layers
  under it come first and the marker's own layer last, so A and B start on
  the two layers under it. A and B must differ (checked on OK).
- **output layer**: the layout's layers plus `new layer…` with an L/D field
  next to it; `new layer…` with `3/0` is preselected when 3/0 does not exist
  yet, else 3/0 itself.
- **operation** (the entry's, changeable) and **keep what is already on the
  output layer** (checked).

With the two operands (or two layers under a marker) the proposals are right: just OK. KLayout's
status bar shows a one-line confirmation; Ctrl+Z undoes the whole run in one
step.

This tool is the drop-in macro `bool_region_macro` after step 2 of
the "macro -> button" guide, `recipes/tools.md` in a klink project (config block -> params recognised from the layout,
one preset per operation -> dropdown entries).

From a script or agent (no form; a layer param with exactly one candidate is
filled in, otherwise the error lists the candidates to pass):

```bash
python -m klink.cli tool run bool_region --preset and --param layer_a=1/0
python -m klink.cli tool run bool_region --preset not --param layer_a=1/0 --param layer_b=2/0 --param output=3/0 --param keep_output=no
```

Remove it with `python -m klink.cli tool uninstall bool_region`. Format and contract:
`recipes/tools.md` in a klink project (`docs/TOOLS.md` in the klink repo).

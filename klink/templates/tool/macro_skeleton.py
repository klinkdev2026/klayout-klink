"""__TITLE__ -- a klink tool (plain-macro form).

Scaffolded by ``klink tool new --macro``. A plain KLayout macro, exactly as you would write
it in KLayout's macro IDE: it fetches the view itself and runs top to bottom. As written it
counts the selected shapes per layer and changes nothing.

THE CONTRACT (docs/TOOL_AUTHORING.md has the full text)

* Do NOT define a top-level ``run`` function: that switches klink to the
  ``run(ctx, params)`` form.
* klink executes this file as ``__main__`` inside ONE undo transaction (an exception rolls
  every edit back), so do NOT open your own ``view.transaction()``.
* klink also injects the globals ``ctx`` and ``params`` (the same objects the
  ``run(ctx, params)`` form gets); they do not exist when the macro runs from KLayout's
  macro IDE, so this skeleton does not use them and fetches the view itself.
* Params declared in tool.json are asked in ONE KLayout form by klink: do not pop your own
  InputDialog / MessageBox. A layer param arrives as a "L/D" string such as "1/0".
* Geometry is in database units (integers): ``int(round(um / layout.dbu))``; layer index:
  ``layout.layer(pya.LayerInfo(ln, dt))``; insert into ``cell.shapes(li)``; booleans with
  ``pya.Region``.
* Validate everything before the first edit; for a user error raise
  ``ValueError("<what is wrong>; <what to do>")``. ``sys.exit(0)`` ends early; a non-zero
  exit is reported as a failure.
* ``print`` ONE summary line (the last printed line goes to KLayout's status bar).
"""

import pya

TITLE = __TITLE_REPR__


def main():
    view = pya.Application.instance().main_window().current_view()
    if view is None:
        raise ValueError("no layout is open; open one and run the tool again")
    cv = view.active_cellview()
    layout = cv.layout()

    counts = {}
    for obj in view.each_object_selected():
        if obj.is_cell_inst():
            continue
        info = layout.get_info(obj.layer)
        key = f"{info.layer}/{info.datatype}"
        counts[key] = counts.get(key, 0) + 1

    total = sum(counts.values())
    if total:
        per_layer = ", ".join(f"{k}: {n}" for k, n in sorted(counts.items()))
        print(f"{TITLE}: {total} selected shape(s) ({per_layer})")
    else:
        print(f"{TITLE}: nothing selected")


if __name__ == "__main__":
    main()

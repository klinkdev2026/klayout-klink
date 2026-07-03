"""
10_exec_demo.py - M4 demo: exec.python escape hatch.

Shows five progressively richer scenarios:

  1. Trivial one-liner expression (Jupyter-style return).
  2. Multi-line script that mutates layout via raw pya (bypassing
     the typed RPC surface), then checks the result with typed RPCs.
  3. State persistence: set a variable in call A, read it in call B.
  4. User exception is captured, not fatal. RPC itself still succeeds.
  5. Syntax error is a structural bad request (RPC fails), nothing runs.

You need a layout open in KLayout. The script leaves behind a cell
named EXEC_DEMO with a couple of boxes drawn through raw pya.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from klink import KLinkClient


CELL = "EXEC_DEMO"


def banner(title: str) -> None:
    print()
    print("=" * 64)
    print("  " + title)
    print("=" * 64)


def main() -> None:
    c = KLinkClient()
    c.connect()

    # Clean slate so the demo is repeatable.
    # IMPORTANT: if the previous run left KLayout viewing EXEC_DEMO,
    # we MUST switch the view away before deleting it, otherwise pya
    # is left with a dangling pointer to the shown cell and KLayout
    # will segfault. Pick any other top cell (or the first root we
    # find that isn't us).
    info = c.layout_info()
    others = [t for t in info.get("top_cells", []) if t != CELL]
    if others:
        try:
            c.show_cell(others[0])
        except Exception:
            pass
    try:
        c.cell_delete(CELL, recursive=True)
    except Exception:
        pass
    c.cell_create(CELL)

    # Pre-register layer 201/0 via the typed RPC so it shows up in
    # the LayerView panel. Raw pya (`ly.layer(...)`) only creates the
    # layer in the Layout data model; the view needs an explicit
    # entry to know how to draw it. Once registered here, the raw
    # pya inside exec.python will hit the SAME layer_index and the
    # boxes will actually render.
    c.layer_ensure(201, 0)

    # -- Scenario 1: Jupyter-style expression return value --
    banner("1. one-line expression -> return_value")
    res = c.exec_python("1 + 2 * 3")
    print(f"  had_result={res['had_result']}")
    print(f"  return_value={res['return_value']}  (type={res['return_value_type']})")
    print(f"  stdout={res['stdout']!r}  wall_ms={res['wall_ms']:.2f}")

    val = c.pyeval("[i*i for i in range(5)]")
    print(f"  pyeval squares -> {val}")

    # -- Scenario 2: multi-line raw pya mutation --
    banner("2. multi-line raw pya: draw two boxes + set up layer")
    code = f"""
cv = view.active_cellview()
ly = cv.layout()
demo = ly.cell({CELL!r})
li = ly.layer(pya.LayerInfo(201, 0))
demo.shapes(li).insert(pya.Box(0, 0, 5000, 3000))
demo.shapes(li).insert(pya.Box(7000, 0, 12000, 3000))
print("inserted 2 boxes on layer 201/0")
print("cell bbox now:", demo.bbox().to_s())
demo.shapes(li).size()
"""
    res = c.exec_python(code)
    print("  stdout:")
    for line in res["stdout"].splitlines():
        print("    |", line)
    print(f"  return_value={res['return_value']}  (shapes on 201/0 after insert)")
    print(f"  exception={res['exception']}  wall_ms={res['wall_ms']:.2f}")

    # Cross-check through the typed RPC: the shapes we drew with raw
    # pya should be visible via shape.query (which uses `layers` and
    # returns `returned`, not `count`).
    q = c.shape_query(CELL, layers=["201/0"])
    print(f"  shape.query sees {q.get('returned', 0)} shape(s) on 201/0 "
          f"(expected 2)")

    # -- Scenario 3: state persistence across calls --
    banner("3. namespace persists across calls on the same connection")
    c.exec_python("my_secret = {'answer': 42, 'tags': ['klink', 'm4']}")
    got = c.pyeval("my_secret['answer'] + len(my_secret['tags'])")
    print(f"  remote computed: {got}  (expected 44)")
    print(f"  namespace_size after the two calls: "
          f"{c.exec_python('None')['namespace_size']} "
          f"(includes pya/view/mw/layout + my_secret)")

    # And show reset works
    c.exec_reset()
    after_reset = c.exec_python("'my_secret' in globals()")
    print(f"  after exec_reset, 'my_secret' in globals() -> "
          f"{after_reset['return_value']}  (expected False)")

    # -- Scenario 4: user code raises -> captured, not fatal --
    banner("4. user exception is captured, RPC still succeeds")
    res = c.exec_python(
        "print('about to blow up')\n"
        "raise ValueError('pretend something bad happened')"
    )
    print(f"  stdout={res['stdout']!r}")
    print(f"  exception.type={res['exception']['type']}")
    print(f"  exception.message={res['exception']['message']}")
    print(f"  traceback tail:")
    tb_tail = res['exception']['traceback'].splitlines()[-3:]
    for line in tb_tail:
        print("    ", line)

    # pyeval wrapper turns that into a real python exception locally:
    try:
        c.pyeval("1/0")
    except RuntimeError as e:
        head = str(e).splitlines()[0]
        print(f"  pyeval translated to local RuntimeError: {head!r}")

    # -- Scenario 5: syntax error = structural bad request --
    banner("5. syntax error is structural (RPC fails before running)")
    try:
        c.exec_python("def broken(:\n    pass")
    except Exception as e:
        print(f"  caught: {type(e).__name__}: {e}")

    # -- final: make sure the typed RPCs we trust still work --
    banner("regression smoke: typed RPCs untouched")
    info = c.layout_info()
    print(f"  layout_info top_cells={info.get('top_cells')}")
    edit = c.edit_status()
    print(f"  edit.status has_undo={edit.get('has_undo')} "
          f"undo_label={edit.get('undo_label', '')!r}")

    c.show_cell(CELL)
    print()
    print(f"done. viewing '{CELL}'. two raw-pya boxes on 201/0.")

    c.close()


if __name__ == "__main__":
    main()

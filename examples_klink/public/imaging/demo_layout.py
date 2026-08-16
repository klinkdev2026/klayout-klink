"""EXAMPLE-owned demo layout for the klink imaging starters.

A four-transistor CMOS row: two NMOS in the p-substrate on the left,
two PMOS in an n-well on the right, poly gates crossing the active
strip, contacts on every source/drain, metal-1 straps up and power
rails top and bottom. It is deliberately a REAL (if small) cell row
rather than a couple of rectangles: the section cut crosses gates,
spacers, plugs and the well edge, so every step of ``demo.pyxs`` shows
up in the film.

Layers (yours to change — they must match ``demo.pyxs`` and
``demo_stack.py``):

    1/0  n-well      2/0  active      3/0  poly
    4/0  contact     6/0  metal-1

The section cut runs along ``CUT_UM`` — a horizontal line straight
through the middle of the active row.
"""
import klayout.db as kdb

# geometry in µm, converted to dbu on the way out
GATES_X = (1.6, 3.8, 6.4, 8.6)          # poly gate centers
CONTACTS_X = (0.9, 2.7, 5.1, 7.5, 9.6)  # source/drain contact centers
ROW_Y = (0.9, 2.9)                      # active strip (bottom, top)
CUT_Y = 1.9                             # cut through the middle of it
CUT_UM = [[-0.8, CUT_Y], [11.2, CUT_Y]]


def write_demo_device(path, cell_name="CMOS_ROW"):
    """Draw the cell row and write it to ``path``. Returns the layout."""
    ly = kdb.Layout(); ly.dbu = 0.001
    top = ly.create_cell(cell_name)
    um = 1000

    def box(layer, x0, y0, x1, y1):
        top.shapes(ly.layer(layer, 0)).insert(kdb.Box(
            int(x0 * um), int(y0 * um), int(x1 * um), int(y1 * um)))

    # n-well under the right half — the cut crosses its edge, so the
    # film shows the well appear on one side only
    box(1, 4.3, -0.7, 10.8, 4.7)
    # active strip (everything outside it becomes field oxide)
    box(2, 0.4, ROW_Y[0], 10.0, ROW_Y[1])
    # poly gates, overhanging the active on both sides
    for x in GATES_X:
        box(3, x - 0.18, 0.3, x + 0.18, 3.5)
    # source/drain contacts, centered on the cut line
    for x in CONTACTS_X:
        box(4, x - 0.2, CUT_Y - 0.2, x + 0.2, CUT_Y + 0.2)
    # metal-1: a strap over each contact column, plus power rails
    for x in CONTACTS_X:
        box(6, x - 0.3, 0.55, x + 0.3, 3.25)
    box(6, 0.2, 3.9, 10.2, 4.5)        # top rail
    box(6, 0.2, -0.5, 10.2, 0.1)       # bottom rail
    ly.write(str(path))
    return ly


if __name__ == "__main__":       # quick check: python demo_layout.py
    ly = write_demo_device("demo_device.gds")
    print("wrote demo_device.gds:",
          ly.top_cell().dbbox(), "cut", CUT_UM)

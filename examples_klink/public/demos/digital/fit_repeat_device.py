"""PUBLIC demo: fit COUNT-VARYING device geometry drawn in KLayout into a
parametric PCell (v3 repeat-group model) — no generator code needed.

This is the geometry-only fitter route, KLayout-native end to end:

  1. draw an exemplar family in KLayout (here: synthetic, IP-free — a
     plate with a contact column whose CONTACT COUNT grows with the
     device height; swap in YOUR drawn cells),
  2. harvest each exemplar cell's boxes back as exact integer dbu,
  3. analyze_boxes -> the honesty report (refused families / warnings),
  4. fit_table_v3 -> register as a live KLayout PCell,
  5. place the PCell at parameters NEVER SAMPLED and byte-compare the
     drawn geometry against the same reference that drew the exemplars.

The v2 fitter (fit_device_pnr_lvs.py) models one box per named role and
cannot express a count that changes with a parameter; the v3 model fits
counts as exact floor-linear laws — or REFUSES, naming the box family,
when the structure is outside the model. Byte-exact is the only bar.

Exemplar sampling rule demonstrated below: the contact count must CROSS
a threshold inside the exemplar set, with two exemplars STRADDLING one
step (last value before + first value after) so the count law is pinned
uniquely. Params are in nm so every law is clean integer arithmetic.

Run: python -m examples_klink.public.demos.digital.fit_repeat_device [--port 8765]
"""
import json
import sys
import time

from klink import KLinkClient
from klink.domains.structdevice.pcell_diff import verify_differential
from klink.domains.structdevice.pcell_repeat import (
    analyze_boxes, fit_table_v3, render_table)
from pathlib import Path

_OUT = Path(__file__).parent / "_generated"; _OUT.mkdir(exist_ok=True)
FIT = str(_OUT / "repeat_fit.json")
PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8765

# --- synthetic device reference (EXAMPLE-owned; swap for your real cells).
# plate on 61/0 (corner origin, so every edge is a plain linear law — a
# centered plate with -h//2 has a parity kink at odd h, which the fitter
# correctly REFUSES); contact column on 62/0 up the left side:
#   count = floor((h_nm - 300) / 470) + 1     (contact 220 sq, pitch 470)
def device_boxes(params):
    w, h = int(params["w_nm"]), int(params["h_nm"])
    n = (h - 300) // 470 + 1
    contacts = sorted([200, 150 + k * 470, 420, 150 + k * 470 + 220]
                      for k in range(n))
    return {"61/0": [[0, 0, w, h]], "62/0": contacts}


# exemplars: w varies; h crosses the 5->6 contact step at h=2650 and
# STRADDLES it (2649 | 2650) so the floor law is pinned uniquely
EXEMPLARS = [{"w_nm": 2000, "h_nm": 2400}, {"w_nm": 3000, "h_nm": 2400},
             {"w_nm": 2000, "h_nm": 2649}, {"w_nm": 2000, "h_nm": 2650},
             {"w_nm": 3000, "h_nm": 3200}]
# held-out gate: interior point AND count values never sampled
CHECK = [{"w_nm": 2600, "h_nm": 2500}, {"w_nm": 2400, "h_nm": 4100},
         {"w_nm": 2000, "h_nm": 5000}]

HARVEST = """
import pya, json
_ly = pya.Application.instance().main_window().current_view() \
    .active_cellview().layout()
_cell = _ly.cell(%r)
_out = {}
for _li in _ly.layer_indexes():
    _info = _ly.get_info(_li)
    _boxes = []
    _it = _cell.begin_shapes_rec(_li)
    while not _it.at_end():
        _sh = _it.shape()
        if _sh.is_box():
            _b = _sh.box.transformed(_it.trans())
            _boxes.append([_b.left, _b.bottom, _b.right, _b.top])
        _it.next()
    if _boxes:
        _out["%%d/%%d" %% (_info.layer, _info.datatype)] = sorted(_boxes)
json.dumps(_out)
"""


def harvest(client, cell):
    res = client.exec_python(HARVEST % cell)
    if res["exception"] is not None:
        raise SystemExit(f"harvest failed: {res['exception']}")
    return json.loads(res["return_value"])


def main():
    stamp = int(time.time())
    pcell = f"REPEAT_FIT_{stamp}"
    with KLinkClient(port=PORT).connect() as c:
        # 1. draw the exemplar family (this stands in for YOUR drawn cells)
        exemplar_cells = []
        for i, ps in enumerate(EXEMPLARS):
            cell = f"{pcell}_EX{i}"
            c.cell_create(cell)
            for layer_key, boxes in device_boxes(ps).items():
                l, d = (int(v) for v in layer_key.split("/"))
                li = c.layer_ensure(l, d)["layer_index"]
                c.shape_insert_boxes(
                    cell, layer_index=li,
                    boxes_um=[[v / 1000.0 for v in b] for b in boxes])
            exemplar_cells.append((cell, ps))

        # 2. harvest them back as exact integer dbu (the real-world input)
        exemplars = [{"params": ps, "boxes": harvest(c, cell)}
                     for cell, ps in exemplar_cells]

        # 3. screen: the honesty report is part of the workflow
        report = analyze_boxes(exemplars, ["w_nm", "h_nm"])
        print(report.summary())
        if report.refusals:
            raise SystemExit(2)

        # 4. fit table -> live PCell
        table = fit_table_v3(report, sample_order=[ps for _, ps in
                                                   exemplar_cells])
        Path(FIT).write_text(json.dumps(table, indent=1), encoding="utf-8")
        diff = verify_differential(lambda p: render_table(table, p),
                                   device_boxes, CHECK)
        print(diff.summary())
        if not diff.all_ok:
            raise SystemExit(1)
        reg = c.call("pcell.register_fitted", {"name": pcell,
                                               "fit_table": FIT})
        print(f"registered {reg['pcell']} (library {reg['library']})")

        # 5. live placement at a NEVER-SAMPLED count, byte-compared
        probe = pcell + "_PROBE"
        c.cell_create(probe)
        c.instance_insert_pcell(
            probe, pcell, library="klink_structdevice",
            params={**CHECK[-1], "style": "default"}, position_um=[0, 0])
        got = harvest(c, probe)
        truth = device_boxes(CHECK[-1])
        n = sum(len(v) for v in truth.values())
        if got != truth:
            raise SystemExit(f"live placement MISMATCH at {CHECK[-1]}")
        print(f"live placement at {CHECK[-1]}: BYTE-EXACT ({n} boxes)")

        # tidy the drawing board (the registered PCell stays usable)
        for cell, _ in exemplar_cells + [(probe, None)]:
            c.cell_delete(cell, recursive=True)
    print("PASS: drawn exemplars -> v3 fit -> live PCell, byte-exact")


if __name__ == "__main__":
    main()

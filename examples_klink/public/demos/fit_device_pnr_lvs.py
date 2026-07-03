"""PUBLIC demo: fit a device from exemplars -> parametric PCell -> digital P&R
-> live LVS. Self-contained and IP-free.

The exemplar geometry here is SYNTHETIC (clean, obviously-not-real numbers) so
the demo ships and runs with zero confidential data. Swap in YOUR harvested
exemplar boxes (from your drawn device cells) to fit your real device -- the
flow is identical. The fitter (klink.domains.structdevice.pcell_fitter) is the
general mechanism; everything device/process-specific lives in this example.

Run: python -m examples_klink.public.demos.fit_device_pnr_lvs [--port 8766] [--draw-only]
"""
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

from klink import KLinkClient
from klink.domains.structdevice import layout_engine as eng
from klink.domains.structdevice import pcell_fitter as fitter
from klink.domains.structdevice.orchestrators import lvs_check
from klink.domains.structdevice.recipes import geom_terminal_provider
from klink.routing.grid.floorplan import derive_grid, derive_row_pitch
from klink.routing.grid.process_profile import ProcessProfile

# --- PUBLIC process config (layers / spacing / vias) -- EXAMPLE-owned, IP-free.
# klink ships ZERO process constants; this demo owns its process the same way it
# owns the synthetic device below. A compact 3-layer back-gate stack: gate on 101,
# source/drain on 104, one upper routing layer 106; the PDN reuses the same stack
# (rail=104 followpins, strap=106). Copy + edit the numbers for YOUR process.
PUBLIC_PROCESS = ProcessProfile(
    routing_layers=("101/0", "104/0", "106/0"),
    gate_layer="101/0",
    sd_layer="104/0",
    channel_layer="103/0",
    vias=(("101/0", "102/0", "104/0"), ("104/0", "105/0", "106/0")),
    layer_directions={"101/0": "V", "104/0": "H", "106/0": "V"},
    wire_width_um=5.0,
    wire_clear_um=2.0,
    prl_spacing_um=10.0,
    prl_length_um=15.0,
    via_pad_um=5.0,
    litho_tol_um=1.0,
    y_step_um=30.0,
    col_pitch_um=100.0,
    margin_um=60.0,
)

_ROOT = Path(__file__).resolve().parents[3]   # repo root (to reach klink_plugin/python)
_OUT = Path(__file__).parent / "_generated"; _OUT.mkdir(exist_ok=True)
FIT = str(_OUT / "public_fit.json")     # generated fit table (this demo's output)
GEOM = str(_OUT / "public_geom.json")   # generated synthetic device geometry
PCELL = "demobg"
PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8766
DRAW_ONLY = "--draw-only" in sys.argv

# --- SYNTHETIC exemplar geometry (um), clean + IP-free. A back-gate-style cell:
#     channel (103) narrow; source/drain (104) protrude past the channel in x;
#     back-gate plate (101) under the channel + extends left for the contact. ---
SX, OUTER, CY, PL, PR = 2.5, 11.0, 2.0, 10.0, 8.0   # OUTER = FIXED S/D outer edge (pad height not parametric, per the confirmed model)


def _device_boxes(W, L):
    return {
        "channel":    ("103/0", [-W / 2, -(CY + L / 2), W / 2, CY + L / 2]),
        "source":     ("104/0", [-(W / 2 + SX), -OUTER, W / 2 + SX, -L / 2]),
        "drain":      ("104/0", [-(W / 2 + SX), L / 2, W / 2 + SX, OUTER]),
        "gate_plate": ("101/0", [-(W / 2 + PL), -(CY + L / 2), W / 2 + PR, CY + L / 2]),
    }


EXEMPLAR_SIZES = [(10, 4), (50, 4), (10, 8), (50, 8)]   # span W and L
BUILD_DEVICES = {"dev10_8": (10, 8), "dev20_8": (20, 8), "dev50_3": (50, 3)}
_EDGES = ("x1", "y1", "x2", "y2")


def fit_parametric_table(verbose=True):
    exemplars = [{"params": {"w_um": W, "l_um": L},
                  "roles": {r: {"layer": ly, "box_um": bx}
                            for r, (ly, bx) in _device_boxes(W, L).items()}}
                 for W, L in EXEMPLAR_SIZES]
    report = fitter.analyze(exemplars, ["w_um", "l_um"])
    if verbose:
        print("=== fitter screening ===")
        print(report.summary())
    table = fitter.fit_table(
        report, style="default",
        sample_order=[{"w_um": W, "l_um": L} for W, L in EXEMPLAR_SIZES],
        param_units={"w_um": "um", "l_um": "um"})
    Path(FIT).write_text(json.dumps(table, indent=1))
    return table


def _box_at(table, role, W, L):
    import klink_server.structdevice_pcell as sd  # plugin edge math (off-KLayout safe)
    r = table["styles"]["default"]["roles"][role]
    return [sd._edge_value(r["edges"][k], {"w_um": W, "l_um": L},
                           table["param_order"], table["sample_order"]) / 1000.0
            for k in _EDGES]


def build_geom(table):
    # plugin edge math import (pya is present inside KLayout; this runs in the
    # MCP/venv, so stub pya like the fitter test does)
    import types
    if "pya" not in sys.modules:
        pya = types.ModuleType("pya")
        for n in ("LayerInfo", "Text", "Trans", "Box", "Library"):
            setattr(pya, n, type(n, (), {"__init__": lambda self, *a, **k: None}))
        pya.PCellDeclarationHelper = object
        sys.modules["pya"] = pya
    sys.path.insert(0, str(_ROOT / "klink_plugin" / "python"))
    geom = {}
    for key, (W, L) in BUILD_DEVICES.items():
        ch = _box_at(table, "channel", W, L)
        src = _box_at(table, "source", W, L)
        drn = _box_at(table, "drain", W, L)
        plate = _box_at(table, "gate_plate", W, L)
        g_x1, g_x2 = plate[0], min(src[0], drn[0])
        geom[key] = {
            "body": [min(plate[0], src[0]), src[1], max(plate[2], src[2]), drn[3]],
            "channel": ch,
            "pads": {"G": plate, "S": src, "D": drn},   # full plate is the G keepout
            "terms": {
                "G": {"center": [(g_x1 + g_x2) / 2.0, 0.0], "orientation": 180.0,
                      "length": g_x2 - g_x1, "layer": "101/0"},
                "S": {"center": [0.0, (src[1] + src[3]) / 2.0], "orientation": 270.0,
                      "length": src[3] - src[1], "layer": "104/0"},
                "D": {"center": [0.0, (drn[1] + drn[3]) / 2.0], "orientation": 90.0,
                      "length": drn[3] - drn[1], "layer": "104/0"}}}
    Path(GEOM).write_text(json.dumps(geom, indent=1))
    return geom


def devices_library():
    return {k: {"params": {"w_um": float(W), "l_um": float(L)}, "pcell": PCELL,
                "library": "klink_structdevice", "style": "default", "fit_table": FIT}
            for k, (W, L) in BUILD_DEVICES.items()}


def main():
    table = fit_parametric_table()
    build_geom(table)
    DEVICES = devices_library()
    with KLinkClient(port=PORT).connect() as c:
        eng.ensure_pcell(c, DEVICES)
        # viewer cell
        if "DEMO_DEVICES" in {x["name"] for x in c.cell_list()["cells"]}:
            c.cell_delete("DEMO_DEVICES")
        c.cell_create("DEMO_DEVICES")
        for L in (101, 103, 104, 6):
            c.layer_ensure(L, 0)
        items = [eng._pcell_item(DEVICES, k, i * 180, 0)
                 for i, k in enumerate(BUILD_DEVICES)]
        c.instance_insert_pcell_many("DEMO_DEVICES", items)
        print("drawn DEMO_DEVICES (synthetic fitted device)")
        if DRAW_ONLY:
            return True
        # BUNDLED public device-netlist (an add4 mapped to THIS demo's synthetic
        # device keys). Self-contained: no external path, no lab device names.
        nl = json.loads((Path(__file__).parent / "add4.devnet.json").read_text())
        raw = eng.load_device_geom(GEOM)
        _, _, terms = eng._geom_tables(raw)
        # floorplan density knobs (EXAMPLE-owned): smaller = denser. These route
        # 94/94 + LVS clean down to ~90/30; 100/35 keeps a little margin.
        P = replace(PUBLIC_PROCESS, wire_clear_um=5.0,
                    grid_pitch_um=PUBLIC_PROCESS.wire_width_um + 5.0,
                    col_pitch_um=100.0, y_step_um=35.0)
        rows, cols = derive_grid(len(nl["groups"]))
        layers = list(P.routing_layers)
        rp = derive_row_pitch(nl, rows, cols, terms, y_step=P.y_step_um, width_um=P.wire_width_um,
                              wire_clear_um=P.wire_clear_um, via_pad_um=P.via_pad_um, n_horiz_layers=len(layers))
        placement = eng.place_grid(nl, rows, cols, profile=P, row_pitch=rp)
        cut_layer = {tuple(sorted((lo, up))): P.cut_layer(lo, up) for (lo, _c, up) in P.vias}
        device_terms = {xi: {t: [round(dx + terms[c][t]["center"][0], 3), round(dy + terms[c][t]["center"][1], 3)] for t in terms[c]}
                        for xi, (c, dx, dy) in placement.items()}
        declared = [{"net": n["net_id"], "terminals": n["terminals"]} for n in nl["nets"]]
        t0 = time.time()
        ok, info, _ = eng.route_and_draw_flexdr(c, "DEMO_ADD4", nl, placement, profile=P, layers=layers,
                                                vias=P.via_rules(), cut_layer=cut_layer, geom_path=GEOM,
                                                devices=DEVICES, use_rust=True)
        print(f"[public] FlexDR {time.time()-t0:.1f}s ok={ok} routed={info.get('routed')}/{info.get('nets')} markers={info.get('markers')}")
        if not ok:
            print("PROBLEMS:", info.get("problems")[:2]); return False
        res = lvs_check(c, "DEMO_ADD4", declared=declared, mode="lvsdb", connectivity=P.connectivity_spec(),
                        terminal_provider=geom_terminal_provider(raw), placement=placement, device_terms=device_terms)
        dev = res.get("device_lvs", {})
        ok_all = bool(res["ok"]) and dev.get("match") is True
        print(f"[public] LVS ok={res['ok']} match={dev.get('match')} devices={dev.get('device_count')}")
        print("RESULT:", "PASS (synthetic fitted device: fit -> P&R -> LVS match)" if ok_all else "FAIL")
        return ok_all


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)

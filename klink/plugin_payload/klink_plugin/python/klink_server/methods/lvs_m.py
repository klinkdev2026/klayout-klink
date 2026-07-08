"""General LVS infrastructure (server-side, like DRC) -- pure pya, zero
klink dependency.

`lvs.run` is the LVS analogue of the DRC escape hatch: extract the live
layout into a device netlist (per-cell device extractors driven entirely
by caller-supplied config) and compare it against a REFERENCE netlist that
can be pulled in from outside -- an external SPICE file, or a structured
netlist passed inline. It writes a native `.lvsdb` and (by default) pops
it open in the Netlist/LVS browser for layout<->netlist cross-probing.

Domain-agnostic: this module knows nothing about transistors. Callers
(e.g. structdevice.lvs_check) supply the device-extraction config + the
reference; the LVS engine here is general. Terminal names/layers are all
parameters -- nothing about a specific device is hardcoded.
"""

from __future__ import annotations

import os
import tempfile

import pya

from ..registry import method
from ..errors import RpcError, ErrorCode


def _nm(s):
    return "".join(ch if ch.isalnum() else "_" for ch in str(s))


def _li(layout, key):
    """Layer index for 'L/D'; create an empty layer if undrawn (a declared-
    but-undrawn layer must be a real layer, not None)."""
    l, d = (int(x) for x in str(key).split("/"))
    idx = layout.find_layer(l, d)
    return idx if idx is not None else layout.layer(l, d)


def _cell_extractor(cell_name, terminals):
    """A GenericDeviceExtractor that emits one Device per device-cell
    instance (recognised by a marker = the cell's instance bbox)."""
    class Extractor(pya.GenericDeviceExtractor):
        def setup(self):
            self.name = cell_name
            dc = pya.DeviceClass(); dc.name = cell_name
            for t in terminals:
                td = pya.DeviceTerminalDefinition(); td.name = t
                dc.add_terminal(td)
            self.register_device_class(dc)
            for t in terminals:
                self.define_layer(t, cell_name + "." + t)
            self.define_layer("__marker__", cell_name + ".marker")

        def get_connectivity(self, layout, layers):
            conn = pya.Connectivity()
            marker = layers[-1]
            conn.connect(marker)
            for ly in layers[:-1]:
                conn.connect(marker, ly)
            return conn

        def extract_devices(self, shapes):
            marker = shapes[-1]
            if marker.is_empty():
                return
            dev = self.create_device()
            for tname, treg in zip(terminals, shapes[:-1]):
                ts = treg & marker
                if not ts.is_empty():
                    self.define_terminal(dev, tname, tname, ts.bbox())
    return Extractor()


def _marker_layer(layout, top, cell_name):
    """A temp layer holding each instance bbox of cell_name (in top coords),
    shrunk by 1 dbu -- the device-recognition marker."""
    target = layout.cell(cell_name)
    if target is None:
        return None
    boxes = []

    def walk(cell, trans):
        for inst in cell.each_inst():
            if not inst.is_regular_array():
                tlist = [inst.cplx_trans]
            else:
                tlist = [inst.cplx_trans * pya.CplxTrans(pya.Vector(
                    inst.a.x * ia + inst.b.x * ib, inst.a.y * ia + inst.b.y * ib))
                    for ia in range(inst.na) for ib in range(inst.nb)]
            child = inst.cell
            for lt in tlist:
                t = trans * lt
                if inst.cell_index == target.cell_index():
                    b = child.bbox()
                    if not b.empty():
                        bb = b.transformed(t)
                        box = pya.Box(int(round(bb.left)), int(round(bb.bottom)),
                                      int(round(bb.right)), int(round(bb.top)))
                        if box.width() > 2 and box.height() > 2:
                            box = pya.Box(box.left + 1, box.bottom + 1,
                                          box.right - 1, box.top - 1)
                        boxes.append(box)
                walk(child, t)
    walk(top, pya.CplxTrans())
    if not boxes:
        return None
    dt = sum(ord(c) for c in cell_name) % 32000
    ml = None
    for off in range(1000):
        if layout.find_layer(65000 - off, dt) is None:
            ml = layout.layer(pya.LayerInfo(65000 - off, dt))
            break
    if ml is None:
        return None
    for b in boxes:
        top.shapes(ml).insert(b)
    return ml


def _build_reference(netlist, device_terminals, top_name):
    """Build a reference pya.Netlist from a structured device netlist:
    {instances:[{instance_id, device_cell}], nets:[{net_id, terminals:['X.G',...]}]}."""
    nl = pya.Netlist(); nl.create()
    classes = {}
    for cell in sorted(device_terminals):
        dc = pya.DeviceClass(); dc.name = cell
        for t in device_terminals[cell]:
            td = pya.DeviceTerminalDefinition(); td.name = t
            dc.add_terminal(td)
        nl.add(dc); classes[cell] = dc
    top = pya.Circuit(); top.name = top_name; nl.add(top)
    netobj = {n["net_id"]: top.create_net(n["net_id"]) for n in netlist["nets"]}
    devobj = {}
    for i in netlist["instances"]:
        devobj[i["instance_id"]] = top.create_device(
            classes[i["device_cell"]], i["instance_id"])
    for n in netlist["nets"]:
        for ref in n["terminals"]:
            iid, t = ref.split(".")
            if iid in devobj:
                devobj[iid].connect_terminal(t, netobj[n["net_id"]])
    return nl


def _read_spice(path):
    nl = pya.Netlist()
    reader = pya.NetlistSpiceReader()
    nl.read(path, reader)
    return nl


def _bad(msg, hint=""):
    raise RpcError(ErrorCode.BAD_PARAMS, msg, hint=hint)


def _check_ld(key, what):
    s = str(key)
    parts = s.split("/")
    if len(parts) != 2 or not all(p.strip().lstrip("-").isdigit() for p in parts):
        _bad(f"{what} {s!r} must be 'layer/datatype' integers (e.g. '101/0')")


def _validate(params):
    """Pure validation BEFORE touching the layout (P4: fail leaves no scar;
    P3: errors are instructions). Defends against malformed/dangerous input
    -- bad layer strings, a missing SPICE file, ill-formed netlists, device
    refs to unknown instances, etc."""
    if not str(params.get("cell") or "").strip():
        _bad("cell must be a non-empty cell name")
    conductors = params.get("conductors")
    if not isinstance(conductors, list) or not conductors:
        _bad("conductors must be a non-empty list of 'layer/datatype' strings")
    for c in conductors:
        _check_ld(c, "conductor")
    cset = {str(c) for c in conductors}
    for v in params.get("vias", []) or []:
        if not isinstance(v, (list, tuple)) or len(v) != 3:
            _bad(f"via {v!r} must be [conductor, via_layer, conductor]")
        for x in v:
            _check_ld(x, "via layer")
        if str(v[0]) not in cset or str(v[2]) not in cset:
            _bad(f"via {v!r} bridges a layer not in conductors {sorted(cset)}")
    devices = params.get("devices")
    if not isinstance(devices, dict) or not devices:
        _bad("devices must map device-cell name -> {terminals, ...}",
             hint="e.g. {'mydev': {'terminals': ['A','B'], 'terminal_layer': {...}}}")
    known_terms = {}
    for cell, cfg in devices.items():
        if not isinstance(cfg, dict):
            _bad(f"devices[{cell!r}] must be an object with 'terminals'")
        terms = cfg.get("terminals")
        if not isinstance(terms, list) or not terms or not all(
                isinstance(t, str) and t for t in terms):
            _bad(f"devices[{cell!r}].terminals must be a non-empty list of names")
        if len(set(terms)) != len(terms):
            _bad(f"devices[{cell!r}].terminals has duplicate names")
        for t, lk in (cfg.get("terminal_layer") or {}).items():
            _check_ld(lk, f"devices[{cell!r}].terminal_layer[{t!r}]")
        for t, pts in (cfg.get("terminal_points_um") or {}).items():
            if not isinstance(pts, list) or not all(
                    isinstance(p, (list, tuple)) and len(p) == 2 for p in pts):
                _bad(f"devices[{cell!r}].terminal_points_um[{t!r}] must be a list of [x,y]")
        known_terms[cell] = set(terms)
    ref = params.get("reference")
    if not isinstance(ref, dict) or ("spice" in ref) == ("netlist" in ref):
        _bad("reference must be exactly one of {spice: path} or {netlist: {...}}")
    if "spice" in ref:
        p = str(ref["spice"])
        if not os.path.isfile(p):
            raise RpcError(ErrorCode.NOT_FOUND, f"reference SPICE file not found: {p!r}",
                           hint="write the netlist file first, then pass its absolute path")
    else:
        nl = ref["netlist"]
        if not isinstance(nl, dict) or not isinstance(nl.get("instances"), list) \
                or not isinstance(nl.get("nets"), list):
            _bad("reference.netlist must be {instances:[...], nets:[...]}")
        inst_ids = set()
        for i in nl["instances"]:
            iid = i.get("instance_id"); dc = i.get("device_cell")
            if not iid or not dc:
                _bad("each reference instance needs instance_id + device_cell")
            if dc not in devices:
                _bad(f"reference instance {iid!r} uses device_cell {dc!r} not in devices config")
            inst_ids.add(iid)
        for n in nl["nets"]:
            if not n.get("net_id") or not isinstance(n.get("terminals"), list):
                _bad("each reference net needs net_id + terminals[]")
            for r in n["terminals"]:
                if not isinstance(r, str) or "." not in r:
                    _bad(f"net terminal ref {r!r} must be 'instance.terminal'")
                iid, t = r.split(".", 1)
                if iid not in inst_ids:
                    _bad(f"net terminal ref {r!r} -> unknown instance {iid!r}")


@method(
    "lvs.run",
    description=(
        "General LVS (like the DRC escape hatch, for connectivity): extract "
        "the live layout into a device netlist (per-cell device extractors "
        "from the 'devices' config) and compare against a REFERENCE netlist "
        "you pull in -- an external SPICE file (reference.spice) OR a "
        "structured netlist (reference.netlist). Writes a native .lvsdb and "
        "(show=true, default) opens it in the Netlist/LVS browser for "
        "layout<->netlist cross-probe. Pure pya; domain-agnostic; terminal "
        "names/layers are all parameters. Read-only (adds temp marker layers "
        "to the in-memory layout; does not alter saved geometry)."
    ),
    params_schema={
        "type": "object",
        "required": ["cell", "conductors", "devices", "reference"],
        "properties": {
            "cell": {"type": "string"},
            "conductors": {"type": "array", "items": {"type": "string"},
                           "description": "conductor layers 'L/D'"},
            "vias": {"type": "array", "items": {
                "type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3}},
            "devices": {"type": "object", "description":
                        "cell -> {terminals:[names], terminal_layer:{term:'L/D'}, "
                        "terminal_points_um:{term:[[x,y],...]}} (points separate same-layer terminals)"},
            "reference": {"type": "object", "description":
                          "{spice: path} OR {netlist: {instances, nets}}"},
            "out_lvsdb": {"type": "string"},
            "show": {"type": "boolean", "default": True},
        },
        "additionalProperties": False,
    },
    returns_schema={
        "type": "object",
        "properties": {
            "match": {"type": "boolean"},
            "device_count": {"type": "integer"},
            "lvsdb_path": {"type": "string"},
            "shown": {"type": "boolean"},
        },
    },
    tags=["lvs", "read"],
)
def lvs_run(params, ctx):
    _validate(params)                       # P4: validate-before-mutate
    cell_name = str(params["cell"])
    conductors = [str(c) for c in params["conductors"]]
    vias = [[str(x) for x in v] for v in params.get("vias", [])]
    devices = params["devices"]
    reference = params["reference"]
    show = params.get("show", True)
    out_path = params.get("out_lvsdb") or os.path.join(
        tempfile.gettempdir(), _nm(cell_name) + ".lvsdb")
    out_path = os.path.abspath(out_path)

    mw = pya.Application.instance().main_window()
    if mw is None:
        raise RpcError(ErrorCode.INTERNAL, "no main window")
    view = mw.current_view()
    if view is None:
        raise RpcError(ErrorCode.NO_VIEW, "no current view; open the layout first")
    cv = view.active_cellview()
    layout = cv.layout()
    top = layout.cell(cell_name)
    if top is None:
        raise RpcError(ErrorCode.NOT_FOUND, f"cell {cell_name!r} not in the active layout",
                       hint="activate the tab/session holding the cell, or check the name")
    dbu = layout.dbu
    half = max(1, int(round(0.5 / dbu)))

    temp_layers = []                        # temp layers to remove afterwards (no scar)
    try:
        # per (cell, terminal) region: point-boxes on a temp layer (separates
        # same-layer terminals) or the whole conductor layer
        device_terminals = {}
        temp = {}          # (cell, term) -> (layer_index, conductor_key)
        for c, cfg in devices.items():
            terms = [str(t) for t in cfg["terminals"]]
            device_terminals[c] = terms
            tlayer = cfg.get("terminal_layer", {}) or {}
            tpoints = cfg.get("terminal_points_um", {}) or {}
            for t in terms:
                ck = str(tlayer.get(t, conductors[0]))
                pts = tpoints.get(t)
                if pts:
                    tl = layout.layer(pya.LayerInfo(64000, len(temp_layers)))
                    temp_layers.append(tl)
                    for (x, y) in pts:
                        xi = int(round(float(x) / dbu)); yi = int(round(float(y) / dbu))
                        top.shapes(tl).insert(pya.Box(xi - half, yi - half, xi + half, yi + half))
                    temp[(c, t)] = (tl, ck)
                else:
                    temp[(c, t)] = (_li(layout, ck), ck)

        L = pya.LayoutVsSchematic(pya.RecursiveShapeIterator(
            layout, top, _li(layout, conductors[0])))
        cond = {k: L.make_layer(_li(layout, k), _nm("c_" + k)) for k in conductors}
        viar = {v[1]: L.make_layer(_li(layout, v[1]), _nm("v_" + v[1])) for v in vias}
        for rg in cond.values():
            L.connect(rg)
        for a, v, b in vias:
            L.connect(viar[v]); L.connect(cond[a], viar[v]); L.connect(viar[v], cond[b])
        terminal_layers = {}
        for (c, t), (tl, ck) in temp.items():
            rg = L.make_layer(tl, _nm("t_" + c + "_" + t))
            L.connect(rg)
            if ck in cond:
                L.connect(rg, cond[ck])
            terminal_layers.setdefault(c, {})[t] = rg
        for c, terms in device_terminals.items():
            ml = _marker_layer(layout, top, c)
            if ml is None:
                raise RpcError(ErrorCode.NOT_FOUND,
                               f"no instances of device cell {c!r} under {cell_name!r}",
                               hint="check the device cell name and that it is placed")
            temp_layers.append(ml)
            layers = dict(terminal_layers[c])
            layers["__marker__"] = L.make_layer(ml, _nm(c + "_marker"))
            L.extract_devices(_cell_extractor(c, terms), layers)
        L.extract_netlist()
        ext = L.netlist()
        for c in device_terminals:
            cc = ext.circuit_by_name(c)
            if cc is not None:
                ext.flatten_circuit(cc)

        if "spice" in reference:
            try:
                ref = _read_spice(str(reference["spice"]))
            except Exception as exc:
                raise RpcError(ErrorCode.BAD_PARAMS,
                               f"could not parse reference SPICE: {exc}",
                               hint="check the SPICE syntax / that subckt pins match the device terminals")
        else:
            ref = _build_reference(reference["netlist"], device_terminals, cell_name)
        L.reference = ref
        match = bool(L.compare(pya.NetlistComparer()))
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        L.write(out_path)
    except RpcError:
        raise
    except Exception as exc:
        raise RpcError(ErrorCode.INTERNAL, f"LVS run failed: {exc}",
                       hint="check the conductor/via layers, device config, and reference netlist")
    finally:
        # remove temp marker/region layers so the layout is not scarred
        for tl in temp_layers:
            try:
                layout.clear_layer(tl); layout.delete_layer(tl)
            except Exception:
                pass

    shown = False
    if show:
        try:
            db = pya.LayoutVsSchematic(); db.read(out_path)
            idx = view.add_lvsdb(db)
            view.show_lvsdb(idx, cv.cell_index if cv.cell is not None else 0)
            shown = True
        except Exception:
            shown = False
    topc = ext.circuit_by_name(cell_name)
    ndev = len(list(topc.each_device())) if topc else 0
    return {"match": match, "device_count": ndev,
            "lvsdb_path": out_path, "shown": shown}

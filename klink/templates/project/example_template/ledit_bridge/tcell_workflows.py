"""T-Cell workflows over the klink L-Edit bridge (generic; no device data).

Five verbs cover the agent-facing T-Cell loop:

  read      show a T-Cell's parameters (parsed from its generator code)
            and their default values
  variants  instance a T-Cell at several parameter sets; harvest every
            generated variant's geometry to JSON (exemplar collection
            for fitting or for verifying a transpilation)
  writeback write generator code (a .cpp file you or your agent wrote)
            into a cell, defining its parameter table -> native T-Cell.
            START FROM tcell_template.cpp (byte-exact-verified pattern);
            hand-written UPI C++ usually fails L-Edit's compile, and the
            error dialog pauses the bridge until closed
  verify    BYTE-EXACT differential check of a Python reference
            generator against what L-Edit actually generates
  fit       one call, geometry-only route: harvest exemplars at your
            parameter sets, fit the v3 repeat-group model
            (klink_fitted_device_pcell_v3), differentially verify the
            fitted table against FRESH L-Edit variants at --check
            points, write the table, and (--register NAME) register it
            as a KLayout PCell + place it live + harvest back +
            byte-compare. The fitter REFUSES (exit 2, instructive
            message naming the box family) anything it cannot express
            exactly and uniquely — a refusal is guidance, not a crash:
            follow it (add exemplars on both sides of a count step, or
            take the writeback/transpile route which handles any code).

Usage examples:
  python tcell_workflows.py read NFET_Generator
  python tcell_workflows.py variants NFET_Generator \
      --paramsets "[{\"L\":2,\"W\":5,\"M\":1},{\"L\":3,\"W\":8,\"M\":2}]" \
      --out exemplars.json
  python tcell_workflows.py writeback MyCell --code my_tcell.cpp \
      --params "[{\"name\":\"W\",\"type\":\"float\",\"default\":5}]"
  python tcell_workflows.py verify NFET_Generator \
      --reference my_ref.py:nfet_boxes \
      --paramsets "[{\"L\":2,\"W\":5,\"M\":1},{\"L\":2,\"W\":12,\"M\":3}]"
  python tcell_workflows.py fit MY_TCELL \
      --paramsets "[{\"w\":0.5,\"pitch\":0.8,\"n\":2}, ...vary EVERY \
      parameter, cross EVERY count threshold...]" \
      --check "[{\"w\":0.6,\"pitch\":0.8,\"n\":6}]" \
      --out my_fit.json --register MY_DEVICE

Landmines this script already handles or warns about:
- L-Edit CACHES T-Cell variants: after changing a T-Cell's code, identical
  parameter sets return the STALE variant. Use fresh parameter values or
  run Tools > Regenerate T-Cells in L-Edit.
- A compile error in written-back code pops a MODAL dialog in L-Edit,
  which pauses the bridge heartbeat: close the dialog, fix the code, and
  write back again.
- Generator code must NOT `#define EXCLUDE_LEDIT_LEGACY_UPI` if it uses
  the Ex830 layer-parameter calls (GDS-number stamping needs them).
"""
import argparse
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from klink.bridges.ledit import (LEditBridgeClient, VariantFactory,
                                 parse_tcell_params, verify_differential)
from klink.bridges.ledit.adapter import harvest_boxes

PROBE = "klink_tcell_probe"


def get_code(bridge, tcell):
    g = bridge.get_cell(tcell)
    if not g["is_tcell"]:
        raise SystemExit(f"{tcell} is not a T-Cell (list_cells shows which are)")
    code = g.get("properties", {}).get("System.TCell Code", "")
    if not code:
        raise SystemExit(f"{tcell} has no System.TCell Code property")
    return code


def cmd_read(bridge, args):
    code = get_code(bridge, args.tcell)
    params = parse_tcell_params(code)
    print(f"{args.tcell}: {len(code)} bytes of generator code")
    if not params:
        print("no parameters found: the parser looks for typed getter "
              "calls (LCell_GetParameterAsDouble/Int/Coord) in the "
              "generator code — see tcell_template.cpp for the pattern")
        return
    defaults = bridge.get_tcell_params(args.tcell, list(params))
    for name, typ in params.items():
        print(f"  {name} ({typ}): {defaults.get(name, {})}")


def _load_json_arg(value, what):
    """--paramsets/--check accept inline JSON or a path to a .json file
    (shell quoting of inline JSON is painful, especially on Windows)."""
    text = value
    if value.lstrip()[:1] not in ("[", "{"):   # a list of sets, or one set
        try:
            with open(value, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            raise SystemExit(
                f"{what} must be inline JSON (a [...] list) or a path to a "
                f"JSON file; got {value!r} which is neither")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise SystemExit(f"{what} is not valid JSON ({exc}); expected e.g. "
                         '[{"W": 5, "L": 2, "M": 1}, ...]')


def cmd_variants(bridge, args):
    paramsets = _load_json_arg(args.paramsets, "--paramsets")
    factory = VariantFactory(bridge, args.tcell, probe_cell=PROBE)
    exemplars = []
    for ps in paramsets:
        # announce BEFORE instancing: if the generator errors (which pops a
        # MODAL dialog in L-Edit and stalls the bridge), the culprit
        # parameter set is already on screen
        print(f"  {ps} ...", end="", flush=True)
        try:
            variant = factory.variant(ps)
            boxes = harvest_boxes(bridge.get_cell(variant))
        except Exception:
            print(" FAILED (if the bridge went stale, an error dialog is "
                  "open in L-Edit for THIS parameter set — close it, check "
                  "the values are physically valid, and re-run)")
            raise
        n = sum(len(v) for v in boxes.values())
        print(f" -> {variant}: {n} boxes")
        exemplars.append({"params": ps, "variant": variant, "boxes": boxes})
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(exemplars, f, indent=1)
        print(f"exemplars -> {args.out}")


def cmd_writeback(bridge, args):
    with open(args.code, "r", encoding="utf-8") as f:
        code = f.read()
    if "EXCLUDE_LEDIT_LEGACY_UPI" in code and "Ex830" in code:
        print("WARNING: EXCLUDE_LEDIT_LEGACY_UPI hides the Ex830 calls "
              "your code uses - the L-Edit compile will fail")
    bridge.create_cell(args.cell)
    r = bridge.set_tcell_code(
        args.cell, code, language=args.language,
        params=json.loads(args.params) if args.params else None)
    print(f"{args.cell}: is_tcell={r['is_tcell']} "
          f"params_added={r['params_added']} bytes={r['code_bytes']}")
    print("note: if this REPLACED existing code, stale variants persist - "
          "instance with fresh parameter values or run "
          "Tools > Regenerate T-Cells")


def _need_layer_src():
    """The proven need_layer() helper, taken from tcell_template.cpp.

    Copied at generation time rather than duplicated here: the template is
    the byte-exact-verified source of truth, and two copies of UPI C++ would
    drift silently until a compile error froze someone's bridge.
    """
    tpl = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "tcell_template.cpp")
    with open(tpl, "r", encoding="utf-8") as f:
        src = f.read()
    start = src.index("static LLayer need_layer(")
    depth, i = 0, src.index("{", start)
    for k in range(i, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[start:k + 1]
    raise RuntimeError("need_layer() not found in tcell_template.cpp")


# KLayout PCell parameter type -> the L-Edit typed getter + T-Cell param
# type. UPI offers exactly five getters (ldata.h): Layer, Coord, Double,
# Boolean, Int. Anything else has no reader, so it is reported rather than
# guessed at.
_GETTER = {
    "double": ("LCell_GetParameterAsDouble", "double", "float"),
    "float": ("LCell_GetParameterAsDouble", "double", "float"),
    "int": ("LCell_GetParameterAsInt", "int", "int"),
    "boolean": ("LCell_GetParameterAsBoolean", "LBoolean", "boolean"),
    "bool": ("LCell_GetParameterAsBoolean", "LBoolean", "boolean"),
}


def _cident(name):
    out = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in name)
    return ("p_" + out) if not out[:1].isalpha() else out


def cmd_from_pcell(bridge, args):
    """Scaffold T-Cell generator code from a KLayout PCell.

    The boilerplate is where this goes wrong: a compile error in written-back
    code pops a MODAL dialog that freezes the bridge until a human closes it.
    So emit the proven skeleton with the parameter plumbing and layer stamping
    already correct, plus the PCell's ACTUAL geometry at the sampled
    parameters as concrete LBox_New calls -- turning "write UPI C++" into
    "replace these constants with formulas".
    """
    from klink import KLinkClient

    params_json = _load_json_arg(args.params, "params") if args.params else {}
    with KLinkClient(port=args.port).connect() as k:
        info = k.pcell_info(args.pcell, library=args.library)
        pcell_params = info.get("params", [])

        # place it once at the given/default parameters and harvest the boxes
        probe = "klink_tcell_scaffold_probe"
        boxes, layers, skipped_shapes = [], {}, {}
        try:
            k.cell_delete(probe)
        except Exception:
            pass
        cell = k.cell_create(probe)["name"]
        place = dict(params_json)
        k.instance_insert_pcell(cell, pcell=args.pcell, library=args.library,
                                params=place, position_um=[0, 0])
        flat = k.call("cell.flatten", {"cell": cell, "levels": -1})  # noqa: F841
        dbu = float(k.call("layout.info").get("dbu", 0.001))
        by_index = {e["layer_index"]: e
                    for e in k.call("layer.list")["layers"]}
        for s in k.shape_query(cell).get("shapes", []):
            e = by_index.get(s.get("layer_index"), {})
            lname = e.get("name") or "L%dD%d" % (e.get("layer", 0),
                                                 e.get("datatype", 0))
            layers[lname] = (int(e.get("layer", 0)), int(e.get("datatype", 0)))
            if s.get("type") == "box" and s.get("bbox_dbu"):
                boxes.append((lname, [int(v) for v in s["bbox_dbu"]]))
            else:
                skipped_shapes[s.get("type") or "?"] = \
                    skipped_shapes.get(s.get("type") or "?", 0) + 1
        try:
            k.cell_delete(probe)
        except Exception:
            pass

    name = args.name or ("KLINK_" + _cident(args.pcell).upper())
    reads, decls, unsupported = [], [], []
    for p in pcell_params:
        ptype = str(p.get("type", "")).lower()
        pname = str(p.get("name", ""))
        if ptype in _GETTER:
            getter, ctype, ttype = _GETTER[ptype]
            reads.append('    %s %s = %s(cellCurrent, "%s");'
                         % (ctype, _cident(pname), getter, pname))
            decl = {"name": pname, "type": ttype}
            if p.get("default") is not None:
                decl["default"] = p["default"]
            decls.append(decl)
        else:
            unsupported.append("%s (%s)" % (pname, ptype or "?"))

    L = []
    L.append("/* T-Cell generator scaffolded from the KLayout PCell "
             "'%s' (library %s)." % (args.pcell, args.library))
    L.append(" *")
    L.append(" * Generated from tcell_template.cpp, whose pattern is")
    L.append(" * byte-exact-verified. Keep its rules: no")
    L.append(" * EXCLUDE_LEDIT_LEGACY_UPI, integer internal units only,")
    L.append(" * need_layer() for GDS stamping, guard degenerate input.")
    L.append(" *")
    L.append(" * The geometry below is what the PCell ACTUALLY drew at the")
    L.append(" * sampled parameters, in internal units. Replace the")
    L.append(" * constants with formulas over the parameters -- that edit")
    L.append(" * IS the port. Then:")
    L.append(" *   python tcell_workflows.py writeback %s --code %s "
             "--params <params.json>" % (name, args.out))
    L.append(" *   python tcell_workflows.py verify %s --reference "
             "ref.py:boxes --paramsets ..." % name)
    L.append(" * Acceptance is an ALL-BYTE-EXACT verify report; nothing")
    L.append(" * less counts as a port.")
    if unsupported:
        L.append(" *")
        L.append(" * NOT scaffolded (UPI has no getter for these types): %s"
                 % ", ".join(unsupported))
        L.append(" * Express them another way or hold them constant.")
    if skipped_shapes:
        L.append(" *")
        L.append(" * The PCell also drew non-box shapes (%s); add them with"
                 % skipped_shapes)
        L.append(" * LPolygon_New / LWire_New yourself.")
    L.append(" */")
    L.append("#include <string.h>")
    L.append("#include <ldata.h>")
    L.append("")
    L.append(_need_layer_src().strip())
    L.append("")
    L.append("void %s_main(void)" % name)
    L.append("{")
    L.append("    LCell cellCurrent = (LCell)LMacro_GetNewTCell();")
    L.append("    LFile file = LCell_GetFile(cellCurrent);")
    L.append("")
    if reads:
        L.append("    /* parameters (names match the writeback --params "
                 "table) */")
        L.extend(reads)
        L.append("")
    for lname, (gl, gd) in sorted(layers.items()):
        L.append('    LLayer ly_%s = need_layer(file, "%s", %d, %d);'
                 % (_cident(lname), lname, gl, gd))
    L.append("")
    L.append("    /* GUARD: refuse degenerate input instead of drawing "
             "garbage */")
    if reads:
        first = reads[0].split()[1]
        L.append("    if (%s <= 0) { LUpi_SetReturnCode(1); return; }" % first)
    else:
        L.append("    /* add a guard over your parameters here */")
    L.append("")
    L.append("    /* geometry AT THE SAMPLED PARAMETERS -- internal units. */")
    L.append("    /* EDIT: turn these constants into formulas. */")
    for lname, bb in boxes:
        L.append("    LBox_New(cellCurrent, ly_%s, %d, %d, %d, %d);"
                 % (_cident(lname), bb[0], bb[1], bb[2], bb[3]))
    if not boxes:
        L.append("    /* the probe placement produced no boxes -- check the "
                 "PCell parameters you passed */")
    L.append("}")
    L.append("")
    L.append('extern "C" int UPI_Entry_Point(void)')
    L.append("{")
    L.append("    %s_main();" % name)
    L.append("    return 1;")
    L.append("}")
    src = "\n".join(L) + "\n"

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(src)
    params_path = os.path.splitext(args.out)[0] + ".params.json"
    with open(params_path, "w", encoding="utf-8") as f:
        json.dump(decls, f, indent=1)

    print("scaffolded %s -> %s (%d bytes)" % (args.pcell, args.out, len(src)))
    print("  cell name     : %s" % name)
    print("  parameters    : %d scaffolded, %d unsupported %s"
          % (len(decls), len(unsupported),
             "(%s)" % ", ".join(unsupported) if unsupported else ""))
    print("  layers        : %s"
          % ", ".join("%s=%d/%d" % (n, p[0], p[1])
                      for n, p in sorted(layers.items())) or "none")
    print("  geometry      : %d boxes%s"
          % (len(boxes),
             (", %s NOT scaffolded" % skipped_shapes) if skipped_shapes else ""))
    print("  params table  : %s" % params_path)
    print("\nnext:")
    print("  1. edit %s: replace the constant geometry with formulas over "
          "the parameters" % args.out)
    print("  2. python tcell_workflows.py writeback %s --code %s --params %s"
          % (name, args.out, params_path))
    print("  3. python tcell_workflows.py verify %s --reference "
          "<ref.py:fn> --paramsets <sets>   <- must be ALL byte-exact"
          % name)
    return 0


def cmd_verify(bridge, args):
    mod_path, fn_name = args.reference.rsplit(":", 1)
    spec = importlib.util.spec_from_file_location("_ref", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ref_fn = getattr(mod, fn_name)

    factory = VariantFactory(bridge, args.tcell, probe_cell=PROBE)

    def truth_fn(params):
        return harvest_boxes(bridge.get_cell(factory.variant(params)))

    def render_fn(params):
        return ref_fn(**params)

    report = verify_differential(render_fn, truth_fn,
                                 _load_json_arg(args.paramsets,
                                                "--paramsets"))
    print(report.summary())
    return 0 if report.all_ok else 1


def _gds_layer_map(bridge):
    """L-Edit layer NAME -> KLayout 'L/D' key. Only layers with an
    assigned GDS number map; unassigned (-1) layers refuse at use time
    (GDS numbering is tape-out critical — never invent one here)."""
    out = {}
    for lay in bridge.get_layers():
        if lay.get("special"):
            continue
        gds = lay.get("gds_layer", -1)
        if gds is None or gds < 0:
            continue
        dt = lay.get("gds_datatype", 0)
        out[lay["name"]] = "%d/%d" % (gds, 0 if dt is None or dt < 0 else dt)
    return out


def cmd_fit(bridge, args):
    from klink.domains.structdevice.pcell_repeat import (
        analyze_boxes, fit_table_v3, render_table)
    from klink.domains.structdevice.pcell_fitter import FitterError

    code = get_code(bridge, args.tcell)
    param_names = list(parse_tcell_params(code))
    if not param_names:
        raise SystemExit(f"{args.tcell}: no parameters found in generator "
                         "code; nothing to fit")
    paramsets = _load_json_arg(args.paramsets, "--paramsets")
    missing = [p for ps in paramsets for p in param_names if p not in ps]
    if missing:
        raise SystemExit(f"every --paramsets entry must set ALL of "
                         f"{param_names}; missing: {sorted(set(missing))}")

    lmap = _gds_layer_map(bridge)
    factory = VariantFactory(bridge, args.tcell, probe_cell=PROBE)

    def truth_fn(ps):
        raw = harvest_boxes(bridge.get_cell(factory.variant(ps)))
        out = {}
        for lname, boxes in raw.items():
            if lname not in lmap:
                raise SystemExit(
                    f"layer {lname!r} has no GDS number assigned in "
                    f"L-Edit; assign one (Setup > Layers) and re-run — "
                    f"klink never invents GDS numbers (tape-out critical)")
            out[lmap[lname]] = boxes
        return out

    exemplars = []
    for ps in paramsets:
        print(f"  exemplar {ps} ...", end="", flush=True)
        try:
            boxes = truth_fn(ps)
        except Exception:
            print(" FAILED (if the bridge went stale, an error dialog is "
                  "open in L-Edit for THIS parameter set — close it, check "
                  "the values are physically valid, and re-run)")
            raise
        print(f" {sum(len(v) for v in boxes.values())} boxes")
        exemplars.append({"params": ps, "boxes": boxes})

    report = analyze_boxes(exemplars, param_names)
    summary = report.summary()
    # klink reports layers as GDS 'L/D'; annotate with the L-Edit layer
    # NAMES so refusals read in the user's own vocabulary
    for lname, key in lmap.items():
        summary = summary.replace(f"layer {key} ",
                                  f"layer {key} ({lname}) ")
    print(summary)
    if report.refusals:
        # concrete next step, in THIS device's vocabulary: integer (count)
        # parameters are the usual drivers of refused structure
        int_params = [n for n, t in parse_tcell_params(code).items()
                      if t == "int"]
        suspects = int_params or param_names
        example = json.dumps({p: ("<one value>" if p in suspects
                                  else "<vary>") for p in param_names})
        print(f"next: refused structure usually tracks a count parameter — "
              f"here likely {suspects}. PIN it to ONE value in every "
              f"--paramsets entry and vary the others, e.g. {example} — "
              f"the fit then learns the remaining axes and records the "
              f"envelope honestly. Full-axis fidelity = the writeback/"
              f"transpile route (see README).")
        return 2                       # summary already names each family
    table = fit_table_v3(report, sample_order=paramsets)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(table, f, indent=1)
    print(f"v3 fit table -> {args.out}")

    check = _load_json_arg(args.check, "--check") if args.check else []
    if check:
        diff = verify_differential(lambda ps: render_table(table, ps),
                                   truth_fn, check)
        print(diff.summary())
        if not diff.all_ok:
            print("fitted table does not reproduce L-Edit at the --check "
                  "points; do NOT register it. Add exemplars near the "
                  "failing points and re-run, or take the transpile route.")
            return 1

    if args.register:
        rc = _register_and_probe_klayout(args, check, truth_fn)
        if rc:
            return rc
    return 0


def _register_and_probe_klayout(args, check, truth_fn):
    """Register the fitted table as a live KLayout PCell, place it at the
    first --check point, harvest the drawn boxes and byte-compare against
    a fresh L-Edit variant. Requires a running KLayout with klink."""
    from klink import KLinkClient

    harvest_code = """
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
    point = dict(check[0]) if check else None
    with KLinkClient().connect() as kc:
        reg = kc.call("pcell.register_fitted", {
            "name": args.register,
            "fit_table": os.path.abspath(args.out)})
        print(f"registered KLayout PCell {reg['pcell']} "
              f"(library {reg['library']}, params {reg['param_order']})")
        if point is None:
            print("no --check point given: skipping the live placement "
                  "probe (recommended: pass --check so the registration "
                  "is proven byte-exact in KLayout, not only offline)")
            return 0
        probe = args.register + "_PROBE"
        kc.cell_create(probe)
        try:
            kc.instance_insert_pcell(
                probe, args.register, library="klink_structdevice",
                params={**point, "style": "default"}, position_um=[0, 0])
            res = kc.exec_python(harvest_code % probe)
            if res["exception"] is not None:
                print("KLayout harvest failed:", res["exception"])
                return 1
            got = json.loads(res["return_value"])
            truth = truth_fn(point)
            if got == truth:
                print(f"KLayout placement at {point}: BYTE-EXACT vs L-Edit "
                      f"({sum(len(v) for v in truth.values())} boxes)")
                return 0
            print(f"KLayout placement at {point}: MISMATCH vs L-Edit")
            for layer in sorted(set(truth) | set(got)):
                t, o = truth.get(layer, []), got.get(layer, [])
                if t != o:
                    print(f"  {layer}: truth {len(t)} vs drawn {len(o)} boxes")
            return 1
        finally:
            try:
                kc.cell_delete(probe, recursive=True)
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="verb", required=True)

    p = sub.add_parser("read")
    p.add_argument("tcell")

    p = sub.add_parser("variants")
    p.add_argument("tcell")
    p.add_argument("--paramsets", required=True,
                   help='inline JSON list of parameter sets, e.g. '
                        '[{"W": 5, "L": 2, "M": 1}, ...], OR a path to a '
                        '.json file holding that list (easier than shell '
                        'quoting). Keep values physically plausible — a '
                        'generator error pops a MODAL dialog in L-Edit')
    p.add_argument("--out", default="")

    p = sub.add_parser("writeback")
    p.add_argument("cell")
    p.add_argument("--code", required=True)
    p.add_argument("--params", default="")
    p.add_argument("--language", type=int, default=5)

    p = sub.add_parser("from_pcell")
    p.add_argument("pcell", help="KLayout PCell name to port")
    p.add_argument("--library", default="Basic",
                   help="KLayout library holding the PCell")
    p.add_argument("--name", default="",
                   help="T-Cell name to generate (default KLINK_<PCELL>)")
    p.add_argument("--params", default="",
                   help="inline JSON (or .json path) of PCell parameters to "
                        "sample the geometry at; defaults are used otherwise")
    p.add_argument("--out", default="tcell_generated.cpp")
    p.add_argument("--port", type=int, default=8765,
                   help="klink session port of the running KLayout")

    p = sub.add_parser("verify")
    p.add_argument("tcell")
    p.add_argument("--reference", required=True,
                   help="pythonfile.py:function returning {layer: sorted "
                        "[[x0,y0,x1,y1] int-nm, ...]} for **params")
    p.add_argument("--paramsets", required=True)

    p = sub.add_parser("fit")
    p.add_argument("tcell")
    p.add_argument("--paramsets", required=True,
                   help="exemplar parameter sets: inline JSON list or a "
                        "path to a .json file. Vary EVERY parameter; for "
                        "count-like (integer) parameters cross at least "
                        "one count threshold, sampling BOTH sides of the "
                        "step. Keep values physically plausible — a "
                        "generator error pops a MODAL dialog in L-Edit")
    p.add_argument("--check", default="",
                   help="held-out parameter sets (inline JSON or .json "
                        "path; ideally values never sampled) for the "
                        "byte-exact differential gate")
    p.add_argument("--out", default="tcell_fit_v3.json")
    p.add_argument("--register", default="",
                   help="also register the table as this KLayout PCell "
                        "name and byte-verify a live placement")

    args = ap.parse_args()
    bridge = LEditBridgeClient()
    return {"read": cmd_read, "variants": cmd_variants,
            "writeback": cmd_writeback, "verify": cmd_verify,
            "from_pcell": cmd_from_pcell,
            "fit": cmd_fit}[args.verb](bridge, args) or 0


if __name__ == "__main__":
    raise SystemExit(main())

"""T-Cell workflows over the klink L-Edit bridge (generic; no device data).

Four verbs cover the agent-facing T-Cell loop:

  read      show a T-Cell's parameters (parsed from its generator code)
            and their default values
  variants  instance a T-Cell at several parameter sets; harvest every
            generated variant's geometry to JSON (exemplar collection
            for fitting or for verifying a transpilation)
  writeback write generator code (a .cpp file you or your agent wrote)
            into a cell, defining its parameter table -> native T-Cell
  verify    BYTE-EXACT differential check of a Python reference
            generator against what L-Edit actually generates

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
        print("no parameters found in the DO-NOT-EDIT section")
        return
    defaults = bridge.get_tcell_params(args.tcell, list(params))
    for name, typ in params.items():
        print(f"  {name} ({typ}): {defaults.get(name, {})}")


def cmd_variants(bridge, args):
    paramsets = json.loads(args.paramsets)
    factory = VariantFactory(bridge, args.tcell, probe_cell=PROBE)
    exemplars = []
    for ps in paramsets:
        variant = factory.variant(ps)
        boxes = harvest_boxes(bridge.get_cell(variant))
        n = sum(len(v) for v in boxes.values())
        print(f"  {ps} -> {variant}: {n} boxes")
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
                                 json.loads(args.paramsets))
    print(report.summary())
    return 0 if report.all_ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="verb", required=True)

    p = sub.add_parser("read")
    p.add_argument("tcell")

    p = sub.add_parser("variants")
    p.add_argument("tcell")
    p.add_argument("--paramsets", required=True)
    p.add_argument("--out", default="")

    p = sub.add_parser("writeback")
    p.add_argument("cell")
    p.add_argument("--code", required=True)
    p.add_argument("--params", default="")
    p.add_argument("--language", type=int, default=5)

    p = sub.add_parser("verify")
    p.add_argument("tcell")
    p.add_argument("--reference", required=True,
                   help="pythonfile.py:function returning {layer: sorted "
                        "[[x0,y0,x1,y1] int-nm, ...]} for **params")
    p.add_argument("--paramsets", required=True)

    args = ap.parse_args()
    bridge = LEditBridgeClient()
    return {"read": cmd_read, "variants": cmd_variants,
            "writeback": cmd_writeback, "verify": cmd_verify}[args.verb](
        bridge, args) or 0


if __name__ == "__main__":
    raise SystemExit(main())

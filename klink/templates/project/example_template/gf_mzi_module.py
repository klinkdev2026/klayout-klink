"""Complete thermo-optic MZI module: gdsfactory script -> klink takeover.

A REAL silicon-photonics module, written as an ordinary gdsfactory script:

    tilted fiber GC -> 1x2 MMI splitter -> two arms with thermal phase
    shifters (the bottom arm MIRRORED) -> 2x2 MMI combiner -> two laterally
    offset output GCs, plus electrical pad rows for the heaters and a
    fiber-loopback GC pair for alignment.

The script routes what plain gdsfactory can (the Manhattan optics). ONE
call — ``import_gf_component`` — takes the whole module over into klink's
interactive loop, then the demo shows every way a net enters the table:

* imported: the script's own optical routes collapse into device-level
  nets (klink re-draws them);
* restyled: the offset output GC nets switch to ``router="sbend"``
  (smooth S-transitions instead of double bends);
* added: the TILTED input GC (15 deg, unreachable for a Manhattan router —
  exactly why the script left it unrouted) connects with
  ``router="all_angle"``; the loopback GC pair connects with
  ``router="dubins"`` arcs; the heater-to-pad ELECTRICAL nets use
  ``router="electrical"`` on the metal layer.

All of it lives in ONE persisted net table, so a single
``photonics.reroute`` re-routes optics, odd-angle feeds, and metal together
after any drag.

Run with a live KLayout (klink plugin) and gdsfactory in this interpreter:

    python -m examples_klink.public.demos.gf_mzi_module [--port 8765]

Layers come from the gdsfactory generic PDK the script itself uses
(WG=1/0, heater metal M3=49/0) — swap the script/PDK for your process;
klink ships no process facts.
"""

from __future__ import annotations

import argparse

CELL = "GF_MZI_MODULE"
OPTICAL_LAYER = "1/0"     # gpdk WG        (from the user's own PDK)
METAL_LAYER = "49/0"      # gpdk M3 (heater/pad metal)


def build_user_module():
    """The 'user script': a complete MZI, optics routed in plain gdsfactory."""
    import gdsfactory as gf

    try:
        gf.get_active_pdk()
    except Exception:
        gf.gpdk.PDK.activate()

    c = gf.Component("user_mzi_module")
    gc_in = c.add_ref(gf.components.grating_coupler_elliptical(), name="gc_in")
    splitter = c.add_ref(gf.components.mmi1x2(), name="splitter")
    arm_top = c.add_ref(
        gf.components.straight_heater_metal(length=100), name="arm_top")
    arm_bot = c.add_ref(
        gf.components.straight_heater_metal(length=100), name="arm_bot")
    combiner = c.add_ref(gf.components.mmi2x2(), name="combiner")
    gc_up = c.add_ref(gf.components.grating_coupler_elliptical(), name="gc_up")
    gc_dn = c.add_ref(gf.components.grating_coupler_elliptical(), name="gc_dn")
    # fiber-loopback alignment pair, at odd headings (dubins arcs later)
    gc_ra = c.add_ref(gf.components.grating_coupler_elliptical(), name="gc_ra")
    gc_rb = c.add_ref(gf.components.grating_coupler_elliptical(), name="gc_rb")

    gc_in.rotate(195)                  # tilted fiber feed: NOT Manhattan-routable
    gc_in.move((-80, -25))
    arm_top.move((80, 45))
    arm_bot.mirror_y()                 # deliberately awkward placement
    arm_bot.move((80, -45))
    combiner.move((260, 0))
    gc_up.move((360, 30))              # laterally offset output bank
    gc_dn.move((360, -30))
    gc_ra.rotate(240)
    gc_ra.move((120, -140))
    gc_rb.rotate(60)
    gc_rb.move((260, -190))

    # pads for the two heaters: top row serves the top arm, bottom row the
    # (mirrored) bottom arm whose electrodes face DOWN
    for i in range(2):
        pad = c.add_ref(gf.components.pad(size=(60, 60)), name=f"padtop{i}")
        pad.move((60 + i * 110, 160))
    for i in range(2):
        pad = c.add_ref(gf.components.pad(size=(60, 60)), name=f"padbot{i}")
        pad.move((60 + i * 110, -220))

    # the user routes what a Manhattan router CAN (one pair per call keeps
    # the user's own bundle router out of trouble; klink replaces these
    # routes on import anyway). The tilted gc_in and the loopback pair stay
    # unrouted — plain gdsfactory has no answer for them.
    for pa, pb in [
        (splitter.ports["o2"], arm_top.ports["o1"]),
        (splitter.ports["o3"], arm_bot.ports["o1"]),
        (arm_top.ports["o2"], combiner.ports["o2"]),   # o2 = upper west port
        (arm_bot.ports["o2"], combiner.ports["o1"]),   # o1 = lower west port
        (combiner.ports["o3"], gc_up.ports["o1"]),
        (combiner.ports["o4"], gc_dn.ports["o1"]),
    ]:
        gf.routing.route_bundle(c, [pa], [pb], cross_section="strip")
    return c


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    from klink import KLinkClient
    from klink.domains.photonics.gf_import import import_gf_component
    from klink.domains.photonics.net_intent import NetTable, RouteStyle, reroute

    component = build_user_module()
    with KLinkClient(port=args.port).connect() as client:
        # 1) take the module over: devices placed + ports marked + the
        # script's routed connections captured as nets (route later, once,
        # after every net style is in place)
        result = import_gf_component(
            client, component, cell=CELL, route_layer=OPTICAL_LAYER,
            route=False)
        print("import ok:", result["ok"])
        print("  imported optical nets:", len(result["nets"]),
              "| instances:", result["instances"],
              "| device cells:", len(result["device_cells"]))
        for problem in result.get("problems", []):
            print("  problem:", problem)

        ports = client.call(
            "port.list", {"cell": CELL, "layer": "999/99", "sort": "name"}
        ).get("ports", [])
        by_name = {p["name"]: p for p in ports}

        def _xsorted(names):
            return sorted(names, key=lambda n: by_name[n]["center_um"][0])

        table = NetTable.load(CELL)

        # 2) RESTYLE the offset output-bank nets: laterally offset facing
        # ports are S-bend territory (smooth transition, no double bend).
        sbent = 0
        for entry in table.entries:
            members = {entry["a"], entry["b"]}
            if any(m.startswith("mmi2x2") and m.endswith(("_o3", "_o4"))
                   for m in members):
                entry["style"]["router"] = "sbend"
                sbent += 1
        print("  restyled to sbend:", sbent, "output-bank nets")

        # 3) ADD what plain gdsfactory could not route at all.
        # Tilted fiber feed (15 deg): all_angle. Ordinals follow sorted
        # netlist names (gc_dn, gc_in, gc_ra, gc_rb, gc_up -> 0..4).
        aa = RouteStyle(router="all_angle", route_layer=OPTICAL_LAYER)
        table.add_pair("grating_coupler_elliptical1_o1", "mmi1x20_o1", aa)
        # Loopback alignment pair at arbitrary headings: dubins arcs.
        dub = RouteStyle(router="dubins", radius_um=40.0,
                         route_layer=OPTICAL_LAYER)
        table.add_pair("grating_coupler_elliptical2_o1",
                       "grating_coupler_elliptical3_o1", dub)
        print("  added: 1 all_angle net (tilted GC) + 1 dubins net (loopback)")

        # 4) heater -> pad ELECTRICAL nets, orientation-paired: up-facing
        # terminals (top arm) to the top pad row's south port, down-facing
        # (mirrored bottom arm) to the bottom row's north port.
        heater_up = _xsorted(
            n for n, p in by_name.items()
            if n.endswith(("_l_e2", "_r_e2")) and round(p["orientation"]) == 90)
        heater_dn = _xsorted(
            n for n, p in by_name.items()
            if n.endswith(("_l_e2", "_r_e2")) and round(p["orientation"]) == 270)
        pads_top = _xsorted(
            n for n, p in by_name.items()
            if n.endswith("_e4") and n.startswith("pad")
            and p["center_um"][1] > 0)
        pads_bot = _xsorted(
            n for n, p in by_name.items()
            if n.endswith("_e2") and n.startswith("pad") and p["center_um"][1] < 0)
        metal = RouteStyle(router="electrical", route_layer=METAL_LAYER,
                           separation_um=12.0)
        for heater, pad in list(zip(heater_up, pads_top)) + list(zip(heater_dn, pads_bot)):
            table.add_pair(heater, pad, metal)
        print("  added: %d electrical nets (heaters -> pads on %s)"
              % (len(heater_up) + len(heater_dn), METAL_LAYER))
        table.save()

        # 5) ONE reroute draws everything: Manhattan optics, S-bends, the
        # all-angle feed, the dubins loopback, and the metal.
        report = reroute(client, cell=CELL)
        print("reroute ok:", report["ok"],
              "| routes:", report.get("routes"),
              "| abutted:", report.get("abutted"),
              "| crossings:", report.get("crossings"),
              "| device_hits:", report.get("device_hits"))
        for problem in report.get("problems", []):
            print("  problem:", problem)

    print("\nNow drag any component in KLayout, then re-run just:")
    print("  photonics.reroute cell=%s   (or reroute(client, cell=%r))"
          % (CELL, CELL))


if __name__ == "__main__":
    main()

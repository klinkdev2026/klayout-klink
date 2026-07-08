"""Process profile -- the ProcessProfile MECHANISM (one editable home for a
technology's layer/via/spacing config).

The standing user ruling: the place & route algorithm must hold NO
process constants. Which layers carry routing, which are device-terminal layers,
which are untouchable, which via bridges which conductors, the wire/spacing/litho
numbers -- these are PROCESS facts the EXAMPLE AUTHOR owns, passed in as a
profile, not values baked into the code. Everything (router layer stack,
keep-outs, LVS connectivity, derived row pitch, via drawing) derives from a
profile instance.

Process INSTANCES (your ProcessProfile objects) are DATA and live in
``your pdk.py``, NOT here -- this module ships only the mechanism.
A different process is a different profile, zero code change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Tuple


@dataclass(frozen=True)
class ProcessProfile:
    routing_layers: Tuple[str, ...]              # ALL signal conductors (grid + LVS + via stack)
    gate_layer: str                              # device gate terminal layer
    sd_layer: str                                # device source/drain terminal layer
    channel_layer: str                           # recipe channel / marker layer
    vias: Tuple[Tuple[str, str, str], ...]       # signal vias (lower_cond, cut_layer, upper_cond)
    # PROCESS-PHYSICAL dimensions -- REQUIRED, no default. These are process facts
    # the example author owns (a wire width is YOUR process's value, not a klink
    # default); klink ships none. See your pdk.py.
    wire_width_um: float                         # drawn wire width
    wire_clear_um: float                         # min same-layer, different-net clearance
    via_pad_um: float                            # via landing pad size
    litho_tol_um: float                          # via cut inset per edge at overlaps
    y_step_um: float                             # device slot pitch within a gate
    col_pitch_um: float                          # gate-to-gate within a row
    # --- optional below: structural ("absent = this process has none") + neutral/solver knobs ---
    keepout_layers: Tuple[str, ...] = ()         # layers routing must never touch
    # OPTIONAL dedicated SIGNAL-BACKBONE layers (Track 2 multi-layer P&R). When set,
    # long signal backbones are confined to THESE layers -- a clean subset of
    # routing_layers that carries NO device terminals / PDN -- while pin access still
    # vias DOWN to the terminal layers through the full routing_layers stack. This is
    # the OpenROAD layer-separation model (pins low, routing high). When EMPTY
    # it falls back to routing_layers, so the router is byte-identical --
    # backbones may live on the terminal layers.
    signal_layers: Tuple[str, ...] = ()
    layer_directions: Mapping[str, str] = field(default_factory=dict)  # preferred H/V per conductor
    # OPTIONAL dedicated power layers. Most processes have NONE -- power shares
    # the routing stack the OpenROAD/Virtuoso way: followpin
    # rails on the device-terminal layer (overlap the cell power pins = connect),
    # straps + rings on an upper routing layer, joined by the existing signal via.
    # When these are empty, the PDN uses routing_layers[-2] as rail, [-1] as strap,
    # and the via between them (see pdn.default_pdn_layers). Set these ONLY for a
    # process that genuinely has separate power metals.
    power_rail_layer: str = ""
    power_strap_layer: str = ""
    power_vias: Tuple[Tuple[str, str, str], ...] = ()
    # Parallel-run-length spacing (OpenROAD SPACINGTABLE PARALLELRUNLENGTH): two
    # DIFFERENT-net wires on the same layer that run side-by-side (parallel) for a
    # contiguous length >= prl_length_um must be separated by >= prl_spacing_um
    # (edge-to-edge), even though a short adjacency / crossing is fine at
    # wire_clear_um. 0 disables the rule (neutral default, not a lab value).
    prl_spacing_um: float = 0.0
    prl_length_um: float = 0.0
    via_clear_um: float = 0.0                     # EXTRA clearance around a via (beyond the
    #                                              wire halo); 0 = a via is spaced like a wire
    grid_pitch_um: float = 5.0                   # router grid resolution (examples derive/override)
    margin_um: float = 60.0                      # routing bbox margin around devices
    y_top_um: float = 0.0                        # placement origin (row 0 top)

    def __post_init__(self):
        if not self.routing_layers:
            raise ValueError("profile needs at least one routing layer")
        rl = set(self.routing_layers)
        for lo, _cut, up in self.vias:
            if lo not in rl or up not in rl:
                raise ValueError(f"via {lo}<->{up} bridges a non-routing layer")
        for layer, direction in self.layer_directions.items():
            if layer in rl and direction.upper() not in {"H", "V"}:
                raise ValueError(f"layer direction for {layer} must be H or V")
        for sl in self.signal_layers:
            if sl not in rl:
                raise ValueError(f"signal layer {sl} not in routing_layers")

    def signal_routing_layers(self) -> Tuple[str, ...]:
        """Layers a SIGNAL backbone may run on: the dedicated `signal_layers` when
        set (Track 2), else every routing layer (Track 1, byte-identical)."""
        return self.signal_layers or self.routing_layers

    def layer_direction(self, layer: str) -> str:
        """Preferred routing direction for a conductor: H or V."""
        direction = self.layer_directions.get(layer)
        if direction:
            return direction.upper()
        if layer not in self.routing_layers:
            return "H"
        idx = self.routing_layers.index(layer)
        return "H" if idx % 2 == 0 else "V"

    def cut_layer(self, a: str, b: str) -> int:
        """Cut layer number that bridges conductors a and b (order-free)."""
        for lo, cut, up in self.vias:
            if {lo, up} == {a, b}:
                return int(cut.split("/")[0])
        raise KeyError(f"no via between {a} and {b} in profile")

    def via_rules(self):
        """capacity_grid ViaRule list (lazy import keeps this module dep-free)."""
        from klink.routing.grid.capacity_grid import ViaRule
        fp = (self.via_pad_um, self.via_pad_um)
        return [ViaRule(lo, up, f"via_{cut.replace('/', '_')}", fp, 3.0)
                for (lo, cut, up) in self.vias]

    def connectivity_spec(self):
        """LVS ConnectivitySpec derived from the same profile."""
        from klink.domains.structdevice.connectivity import ConnectivitySpec
        return ConnectivitySpec(conductors=tuple(self.routing_layers), vias=tuple(self.vias))

    def drc_script(self, **kwargs) -> str:
        """KLayout DRC runset derived from the same profile (width / space /
        via-cut enclosure). See klink.routing.grid.profile_drc for options;
        run it with client.drc_run or profile_drc.run_drc. With this, routing,
        DRC, and LVS all read ONE process declaration."""
        from klink.routing.grid.profile_drc import drc_script
        return drc_script(self, **kwargs)

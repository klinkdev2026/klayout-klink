"""Structure-as-Device domain: any structure -> terminals -> circuits.

Design doc: docs/STRUCTURE_AS_DEVICE_IR.md.  Terminals materialize as
the existing Port IR; routing goes through the existing backend matrix.
"""

from klink.domains.structdevice.lvs_lite import (
    DeclarationError,
    DeclaredNet,
    declared_nets_from_dicts,
    reconcile,
)
from klink.domains.structdevice.logic_map import (
    LogicMapError,
    map_logic_to_devices,
)
from klink.domains.structdevice.netlist_build import (
    NetlistBuildError,
    build_from_netlist,
)
from klink.domains.structdevice.orchestrators import (
    collect_placed_terminals,
    declare_nets_from_sends,
    lvs_check,
    write_spec_file,
)
from klink.domains.structdevice.recipes import (
    DerivedTerminal,
    RecipeError,
    geom_terminal_provider,
)

__all__ = [
    "DeclarationError",
    "DeclaredNet",
    "DerivedTerminal",
    "LogicMapError",
    "NetlistBuildError",
    "RecipeError",
    "build_from_netlist",
    "collect_placed_terminals",
    "declare_nets_from_sends",
    "declared_nets_from_dicts",
    "geom_terminal_provider",
    "lvs_check",
    "map_logic_to_devices",
    "reconcile",
    "write_spec_file",
]

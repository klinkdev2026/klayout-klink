"""Derived connectivity: thin wrapper over KLayout-native LayoutToNetlist.

Structure-as-Device M3 (docs/STRUCTURE_AS_DEVICE_IR.md).  Nets are
DERIVED facts traced from drawn metal/via geometry; this module never
invents its own pathfinding or union-find — extraction is delegated to
``pya.LayoutToNetlist`` (the engine behind open-source silicon LVS
flows), whose exact API shapes were live-probed (see the
M3 section of the design doc).

Runs OFFLINE on a GDS file or an in-memory ``klayout.db`` Layout, in an
interpreter that has the ``klayout`` package (the project venv).  The
zero-dependency rule of klink core is preserved: the import is deferred
and failure names the exact install command.

The reconciliation target: recipe-derived terminals (recipes.py) are
placed through instance transforms and probed against extracted nets,
yielding the per-instance terminal->net table that declared nets will
be audited against (LVS-lite).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


class ConnectivityError(ValueError):
    """Connectivity-spec or extraction failure.  Messages instruct."""


def _kdb() -> Any:
    try:
        import klayout.db as kdb
    except ImportError as exc:
        import sys

        raise ConnectivityError(
            "the 'klayout' package is required for connectivity extraction; "
            f"run: {sys.executable} -m pip install klayout"
        ) from exc
    return kdb


def _parse_ld(key: str, what: str) -> Tuple[int, int]:
    try:
        layer_s, dt_s = key.split("/")
        return int(layer_s), int(dt_s)
    except (ValueError, AttributeError):
        raise ConnectivityError(
            f"{what}: layer must be a 'layer/datatype' string, got {key!r}"
        ) from None


@dataclass(frozen=True)
class ConnectivitySpec:
    """Which layers conduct and which via layers bridge which conductors.

    ``vias`` entries are (conductor_a, via_layer, conductor_b): the via
    layer connects the two conductors wherever all overlap.  This is a
    per-layout declaration (layer roles are never global constants).
    """

    conductors: Tuple[str, ...]
    vias: Tuple[Tuple[str, str, str], ...] = ()

    @classmethod
    def from_stack(cls, stack: Any) -> "ConnectivitySpec":
        """LVS-side projection of the process stack — reads the stack's
        already-parsed layer views, never re-parses layer relations
        (single-parser constraint, FEATURE_GRID_ROUTER_DESIGN F0)."""
        return cls(
            conductors=tuple(stack.conductor_layers()),
            vias=tuple(stack.via_triples()),
        ).validated()

    def validated(self) -> "ConnectivitySpec":
        if not self.conductors:
            raise ConnectivityError("at least one conductor layer required")
        seen = set()
        for c in self.conductors:
            _parse_ld(c, "conductor")
            if c in seen:
                raise ConnectivityError(f"duplicate conductor layer {c}")
            seen.add(c)
        for entry in self.vias:
            if len(entry) != 3:
                raise ConnectivityError(
                    f"via entry must be (conductor, via_layer, conductor), "
                    f"got {entry!r}"
                )
            a, v, b = entry
            _parse_ld(v, "via layer")
            for side in (a, b):
                if side not in self.conductors:
                    raise ConnectivityError(
                        f"via entry {entry!r} references {side!r} which is "
                        "not in conductors; declare it as a conductor first."
                    )
        return self



@dataclass(frozen=True)
class PlacedTerminal:
    """A recipe terminal after instance placement (top-cell um coords).

    ``width_um`` is the terminal pad size; gesture matching uses it as
    the physical tolerance (a wire lands ON the pad, whose center is at
    most one pad-extent away from the wire bbox)."""

    instance: str
    terminal: str
    layer: str
    point_um: Tuple[float, float]
    width_um: float = 0.0
    orientation_deg: float = 0.0
    length_um: float = 0.0
    forbidden_attach_deg: Optional[float] = None


class ConnectivityExtractor:
    """One extraction run over one cell subtree.  Read-only."""

    def __init__(self, layout: Any, top: str, spec: ConnectivitySpec):
        kdb = _kdb()
        self._spec = spec.validated()
        self._layout = layout
        cell = layout.cell(top)
        if cell is None:
            raise ConnectivityError(
                f"cell {top!r} not found; check cell.list / the GDS file."
            )
        self._top = cell

        present = {}
        for li in layout.layer_indexes():
            info = layout.get_info(li)
            present[f"{info.layer}/{info.datatype}"] = li
        needed = list(self._spec.conductors) + [v[1] for v in self._spec.vias]
        # a declared-but-undrawn layer is legal (GDS drops empty layers
        # on write; stacks are declared per layout, not per cell) — but
        # it is recorded honestly so a typo'd layer cannot hide
        self.missing_layers = sorted(k for k in needed if k not in present)
        for key in needed:
            if key not in present:
                layer, datatype = _parse_ld(key, "spec layer")
                present[key] = layout.layer(layer, datatype)

        # API shapes pinned by the live probe (design doc M3)
        any_li = present[self._spec.conductors[0]]
        self._l2n = kdb.LayoutToNetlist(
            kdb.RecursiveShapeIterator(layout, cell, any_li)
        )
        self._regions: Dict[str, Any] = {}
        for key in needed:
            if key not in self._regions:
                self._regions[key] = self._l2n.make_layer(present[key], key)
        for key in self._spec.conductors:
            self._l2n.connect(self._regions[key])
        for a, v, b in self._spec.vias:
            self._l2n.connect(self._regions[v])
            self._l2n.connect(self._regions[a], self._regions[v])
            self._l2n.connect(self._regions[v], self._regions[b])
        self._l2n.extract_netlist()
        nl = self._l2n.netlist()
        nl.flatten()
        circuits = list(nl.each_circuit())
        if len(circuits) > 1:
            raise ConnectivityError(
                f"expected one flattened circuit, got {len(circuits)}; "
                "report this layout to the main lane before proceeding."
            )
        # zero circuits = no conducting geometry at all (legal: empty
        # cell or all spec layers undrawn) -> zero nets
        self._circuit = circuits[0] if circuits else None
        self._nets = [] if self._circuit is None else sorted(
            self._circuit.each_net(), key=lambda n: n.cluster_id
        )
        # boundary-touching nets carry negative cluster ids (rendered as
        # huge unsigned values); expose stable ordinal ids instead
        self._id_by_cluster = {
            n.cluster_id: f"net_{i}" for i, n in enumerate(self._nets)
        }

    @classmethod
    def from_file(
        cls, gds_path: str, top: str, spec: ConnectivitySpec
    ) -> "ConnectivityExtractor":
        kdb = _kdb()
        layout = kdb.Layout()
        layout.read(gds_path)
        # LESSONS #63: Netlist.flatten() over deep/partially-connected
        # hierarchies does not reduce to one circuit; flatten the cell
        # tree first. This layout object is private to the extractor,
        # so in-place flattening is safe.
        cellobj = layout.cell(top)
        if cellobj is not None:
            cellobj.flatten(-1)
        return cls(layout, top, spec)

    def _net_id(self, net: Any) -> str:
        return self._id_by_cluster.get(
            net.cluster_id, f"net_c{net.cluster_id}"
        )

    def nets(self) -> List[Dict[str, Any]]:
        """Flat net summary: id, name (if labeled), shape count per layer."""
        out = []
        for net in self._nets:
            shapes = {}
            for key in self._spec.conductors:
                region = self._l2n.shapes_of_net(net, self._regions[key], True)
                count = sum(1 for _ in region.each())
                if count:
                    shapes[key] = count
            out.append({
                "net_id": self._net_id(net),
                "name": net.name or None,
                "shapes_by_layer": shapes,
            })
        return out

    def net_shape_bboxes_um(self, net_id: str, layer: str) -> List[
            Tuple[float, float, float, float]]:
        """Bounding boxes (um) of one net's shapes on one conductor
        layer.  Used to build automatic keepouts: everything that is
        not the routed net's own geometry is an obstacle (Update 24
        ruling)."""
        if layer not in self._regions:
            raise ConnectivityError(
                f"layer {layer!r} is not part of the connectivity spec"
            )
        dbu = self._layout.dbu
        out: List[Tuple[float, float, float, float]] = []
        for net in self._nets:
            if self._net_id(net) != net_id:
                continue
            region = self._l2n.shapes_of_net(net, self._regions[layer], True)
            for poly in region.each():
                b = poly.bbox()
                out.append((b.left * dbu, b.bottom * dbu,
                            b.right * dbu, b.top * dbu))
        return out

    def probe_um(self, layer: str, x_um: float, y_um: float) -> Optional[str]:
        """Map a top-cell um coordinate on a conductor layer to a net id."""
        kdb = _kdb()
        if layer not in self._regions:
            raise ConnectivityError(
                f"layer {layer!r} is not part of the connectivity spec"
            )
        net = self._l2n.probe_net(
            self._regions[layer], kdb.DPoint(float(x_um), float(y_um))
        )
        return self._net_id(net) if net is not None else None

    def terminal_net_table(
        self, placed: Sequence[PlacedTerminal]
    ) -> Dict[str, Any]:
        """Probe placed terminals against extracted nets.

        Returns rows plus ``problems``: a terminal landing on no net is
        floating (instruction-grade finding, not an exception — the
        whole point of LVS-lite is to report exactly this).
        """
        rows = []
        problems = []
        for t in placed:
            net_id = self.probe_um(t.layer, *t.point_um)
            rows.append({
                "instance": t.instance,
                "terminal": t.terminal,
                "layer": t.layer,
                "point_um": list(t.point_um),
                "net_id": net_id,
            })
            if net_id is None:
                problems.append(
                    f"{t.instance}.{t.terminal} at {t.point_um} on "
                    f"{t.layer} hits no conducting geometry (floating "
                    "terminal). Check the instance transform, the layer "
                    "roles, or the drawn wiring."
                )
        nets_used: Dict[str, List[str]] = {}
        for row in rows:
            if row["net_id"]:
                nets_used.setdefault(row["net_id"], []).append(
                    f"{row['instance']}.{row['terminal']}"
                )
        return {"rows": rows, "nets": nets_used, "problems": problems}

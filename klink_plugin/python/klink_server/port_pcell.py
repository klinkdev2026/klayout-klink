"""
klink_Port PCell — triangle marker + text label for port visualisation.

Each port is a PCell instance on the port marker layer. The PCell
auto-generates a directional triangle and optional text label from its
parameters, so the triangle orientation and label text can never drift out
of sync with the stored parameters.

Registered as library "klink_port", PCell name "Port".
"""

from __future__ import annotations

import math
import pya


def _string_param_with_choices(owner, name: str, description: str,
                               default: str, choices: list[str]):
    decl = owner.param(name, owner.TypeString, description, default=default)
    try:
        for choice in choices:
            decl.add_choice(choice, choice)
    except Exception:
        pass
    return decl


class KlinkPortPcell(pya.PCellDeclarationHelper):
    """PCell that draws a directional triangle marker + optional text label.

    Parameters (8):
      layer        - TypeLayer  : which layer the triangle+text are drawn on
      port_name    - TypeString : unique port identifier (read-only)
      label        - TypeString : optional display label
      orientation  - TypeDouble : direction in degrees (0=east, 90=north)
      width_um     - TypeDouble : port width in microns
      port_type    - TypeString : "electrical" | "optical" | "placement"
      net          - TypeString : net / pin label (shown in GDS text)
      target_layer - TypeString : target signal layer in "L/D" format
      show_label   - TypeBoolean: show/hide name/net label
      access_mode  - TypeString : point | edge
      slide_allowed - TypeBoolean: router may move landing along slide_edge
      slide_edge   - TypeString : x0,y0,x1,y1 in DBU
    """

    def __init__(self):
        super().__init__()

        # param(name, type, description, default=..., choices=..., unit=...)
        self.param("layer", self.TypeLayer,
                   "Port marker layer", default=pya.LayerInfo(999, 99))
        self.param("port_name", self.TypeString,
                   "Port name (unique ID, read-only)", default="",
                   readonly=True)
        self.param("label", self.TypeString,
                   "Optional display label", default="")
        self.param("orientation", self.TypeDouble,
                   "Direction angle in degrees (0=east, 90=north)",
                   default=0.0, unit="deg")
        self.param("width_um", self.TypeDouble,
                   "Port width in microns", default=5.0, unit="um")
        _string_param_with_choices(
            self,
            "port_type",
            "Port type",
            "electrical",
            ["electrical", "optical", "placement", "cpw"],
        )
        self.param("net", self.TypeString,
                   "Net name (pin label, shown on GDS)", default="")
        self.param("target_layer", self.TypeString,
                   "Target signal layer in L/D format", default="1/0")
        self.param("show_label", self.TypeBoolean,
                   "Show port name/net label", default=False)
        _string_param_with_choices(
            self,
            "access_mode",
            "Access mode",
            "point",
            ["point", "edge"],
        )
        self.param("slide_allowed", self.TypeBoolean,
                   "Allow router to slide landing point along slide_edge", default=False)
        self.param("slide_edge", self.TypeString,
                   "Slide edge in DBU: x0,y0,x1,y1", default="")

    def display_text_impl(self):
        pname = self.label or self.port_name
        net = self.net
        if net:
            return "%s (%s)" % (pname, net)
        return pname

    def coerce_parameters_impl(self):
        # Clamp orientation to [0, 360)
        if self.orientation < 0:
            self.orientation += 360.0
        self.orientation %= 360.0
        # Clamp width
        if self.width_um < 0.1:
            self.width_um = 0.1
        if self.access_mode not in ("point", "edge"):
            self.access_mode = "point"

    def produce_impl(self):
        pname = self.port_name
        display = self.label or pname
        orientation = self.orientation
        width_um = self.width_um
        net = self.net
        show_label = bool(self.show_label)

        dbu = self.layout.dbu
        w_dbu = int(round(width_um / dbu))
        if w_dbu < 10:
            w_dbu = 10

        hw = w_dbu // 2
        # Canonical port marker: an isosceles triangle with a 120 degree
        # front angle. This is intentionally blunt so hand-drawn markers are
        # easier to recognize than sharp arrowheads.
        d = max(1, int(round(hw / math.sqrt(3.0))))
        tip_x = d
        base_x = 0

        # Triangle pointing east (0 deg) with its BASE EDGE ON the origin:
        # the instance origin is the port contact point (center_um in
        # port.list), routes terminate exactly there, and the visible base
        # edge must sit on that line — never behind it inside the device
        # (klink/routing/geom/constraints.py port_launch_point contract).
        pts = [
            pya.Point(tip_x, 0),
            pya.Point(base_x, -hw),
            pya.Point(base_x, hw),
        ]

        if orientation != 0.0:
            rad = math.radians(orientation)
            cos_r = math.cos(rad)
            sin_r = math.sin(rad)
            rotated = []
            for pt in pts:
                rx = pt.x * cos_r - pt.y * sin_r
                ry = pt.x * sin_r + pt.y * cos_r
                rotated.append(pya.Point(int(round(rx)), int(round(ry))))
            pts = rotated

        poly = pya.Polygon(pts)

        # self.layer_layer is the auto-generated layer index for TypeLayer
        layer_idx = self.layer_layer
        self.cell.shapes(layer_idx).insert(poly)

        if not show_label:
            return

        # Keep port identity visible when requested. net determines
        # connectivity, but name is the unique handle used by update/unmark
        # workflows.
        label_text = "%s:%s" % (display, net) if net else display

        # Position text away from the marker.
        rad = math.radians(orientation)
        label_offset = max(int(round(w_dbu * 0.55)), 12)
        tx = int(round(-label_offset * math.cos(rad)))
        ty = int(round(-label_offset * math.sin(rad)))
        # Slight perpendicular offset for readability.
        tx += int(round(hw * 0.25 * math.sin(rad)))
        ty += int(round(-hw * 0.25 * math.cos(rad)))

        text_obj = pya.Text(label_text, pya.Trans(tx, ty))
        text_obj.size = max(min(int(round(w_dbu * 0.35)), 200), 20)
        self.cell.shapes(layer_idx).insert(text_obj)

    def can_create_from_shape_impl(self):
        return False


# ---------------------------------------------------------------------------
# Library registration
# ---------------------------------------------------------------------------

_PORT_LIB_REF = None  # module-level anchor against GC


def register_port_library():
    """Create (once) and return the klink_port library.

    Always creates a fresh library — never reuses a stale one from a
    previous plugin load. The module-level reference prevents garbage
    collection.
    """
    global _PORT_LIB_REF
    if _PORT_LIB_REF is not None:
        return _PORT_LIB_REF

    lib = pya.Library()
    lib.description = "klink Port markers"
    lib.layout().register_pcell("Port", KlinkPortPcell())
    lib.register("klink_port")
    _PORT_LIB_REF = lib
    return lib

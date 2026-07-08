"""klink Anchor PCells for routing anchor visualization.

Registered as library "klink_anchor" with three concrete PCells:

- BendAnchor
- WaypointAnchor
- CorridorAnchor

The common RPC surface can still return normalized Anchor dictionaries, but the
KLayout GUI exposes each anchor kind as its own concrete PCell.
"""

from __future__ import annotations

import math

import pya

def _draw_label(cell, layer_idx: int, text: str, mode: str, net: str,
                show_label: bool, extent_dbu: int, dbu: float) -> None:
    if not bool(show_label):
        return
    label = text
    if net:
        label = "%s:%s" % (label, net)
    if mode == "fixed":
        label = "%s!" % label
    min_size_dbu = max(1, int(round(0.05 / dbu)))
    max_size_dbu = max(min_size_dbu, int(round(20.0 / dbu)))
    size_dbu = max(min(int(round(extent_dbu * 0.35)), max_size_dbu), min_size_dbu)
    text_obj = pya.Text(label, pya.Trans(0, 0))
    text_obj.size = size_dbu
    cell.shapes(layer_idx).insert(text_obj)


def _circle_outline(radius_dbu: int, width_dbu: int) -> pya.Path:
    points = []
    segments = 64
    for i in range(segments + 1):
        angle = 2.0 * math.pi * i / segments
        points.append(pya.Point(
            int(round(radius_dbu * math.cos(angle))),
            int(round(radius_dbu * math.sin(angle))),
        ))
    return pya.Path(points, max(1, int(width_dbu)))


def _string_param_with_choices(owner, name: str, description: str,
                               default: str, choices: list[str]):
    decl = owner.param(name, owner.TypeString, description, default=default)
    try:
        for choice in choices:
            decl.add_choice(choice, choice)
    except Exception:
        pass
    return decl


class _AnchorBase(pya.PCellDeclarationHelper):
    kind = ""

    def _add_common_params(self):
        self.param("layer", self.TypeLayer,
                   "Anchor marker layer", default=pya.LayerInfo(999, 1))
        self.param("anchor_id", self.TypeString,
                   "Anchor id (unique handle, read-only)", default="",
                   readonly=True)
        _string_param_with_choices(
            self,
            "mode",
            "Anchor mode",
            "flexible",
            ["flexible", "fixed"],
        )
        self.param("net", self.TypeString,
                   "Net this anchor applies to", default="")
        self.param("label", self.TypeString,
                   "Optional display label", default="")
        self.param("show_label", self.TypeBoolean,
                   "Show anchor id/net/label", default=False)
        self.param("required", self.TypeBoolean,
                   "Whether routing must satisfy this anchor", default=True)
        self.param("priority", self.TypeInt,
                   "Routing priority", default=0)

    def display_text_impl(self):
        text = self.label or self.anchor_id
        if self.net:
            return "%s (%s)" % (text, self.net)
        return text

    def coerce_parameters_impl(self):
        if self.mode not in ("flexible", "fixed"):
            self.mode = "flexible"
        self._coerce_specific()

    def _coerce_specific(self):
        pass

    def can_create_from_shape_impl(self):
        return False


class KlinkBendAnchorPcell(_AnchorBase):
    """Flexible/fixed bend-region anchor.

    A bend anchor marks a redundant region where the router may choose the
    actual bend point.
    """

    kind = "bend_region"

    def __init__(self):
        super().__init__()
        self._add_common_params()
        self.param("radius_um", self.TypeDouble,
                   "Bend search radius", default=5.0, unit="um")
        self.param("orientation", self.TypeDouble,
                   "Marker orientation", default=0.0, unit="deg")

    def _coerce_specific(self):
        if self.radius_um < 0.1:
            self.radius_um = 0.1
        self.orientation %= 360.0

    def produce_impl(self):
        dbu = self.layout.dbu
        layer_idx = self.layer_layer
        radius_dbu = max(10, int(round(float(self.radius_um) / dbu)))

        x = max(10, int(round(math.sqrt(3.0) * radius_dbu)))
        pts = [
            pya.Point(0, 2 * radius_dbu),
            pya.Point(-x, -radius_dbu),
            pya.Point(x, -radius_dbu),
        ]
        if self.orientation:
            rad = math.radians(float(self.orientation))
            cos_r = math.cos(rad)
            sin_r = math.sin(rad)
            pts = [
                pya.Point(
                    int(round(pt.x * cos_r - pt.y * sin_r)),
                    int(round(pt.x * sin_r + pt.y * cos_r)),
                )
                for pt in pts
            ]

        self.cell.shapes(layer_idx).insert(pya.Polygon(pts))
        outline_width = max(1, int(round(radius_dbu * 0.035)))
        self.cell.shapes(layer_idx).insert(_circle_outline(radius_dbu, outline_width))
        _draw_label(
            self.cell, layer_idx, self.label or self.anchor_id, self.mode,
            self.net, self.show_label, radius_dbu, dbu,
        )


class KlinkWaypointAnchorPcell(_AnchorBase):
    """Waypoint-region anchor.

    A waypoint anchor marks a box region the route must pass through.
    """

    kind = "waypoint_region"

    def __init__(self):
        super().__init__()
        self._add_common_params()
        self.param("width_um", self.TypeDouble,
                   "Waypoint region width", default=10.0, unit="um")
        self.param("height_um", self.TypeDouble,
                   "Waypoint region height", default=10.0, unit="um")

    def _coerce_specific(self):
        if self.width_um < 0.1:
            self.width_um = 0.1
        if self.height_um < 0.1:
            self.height_um = 0.1

    def produce_impl(self):
        dbu = self.layout.dbu
        layer_idx = self.layer_layer
        hw = max(5, int(round(float(self.width_um) / dbu / 2.0)))
        hh = max(5, int(round(float(self.height_um) / dbu / 2.0)))
        self.cell.shapes(layer_idx).insert(pya.Box(-hw, -hh, hw, hh))
        _draw_label(
            self.cell, layer_idx, self.label or self.anchor_id, self.mode,
            self.net, self.show_label, max(hw, hh), dbu,
        )


class KlinkCorridorAnchorPcell(_AnchorBase):
    """Corridor anchor.

    A corridor anchor marks a path region the route should pass through/along.
    """

    kind = "corridor"

    def __init__(self):
        super().__init__()
        self._add_common_params()
        self.param("width_um", self.TypeDouble,
                   "Corridor width", default=3.0, unit="um")
        self.param("path_points", self.TypeString,
                   "Corridor points relative to center: x,y;x,y;...", default="")

    def _coerce_specific(self):
        if self.width_um < 0.1:
            self.width_um = 0.1

    def _parse_path_points(self, dbu: float) -> list[pya.Point]:
        pts = []
        for item in str(self.path_points or "").split(";"):
            item = item.strip()
            if not item:
                continue
            try:
                x_s, y_s = item.split(",", 1)
                pts.append(pya.Point(int(round(float(x_s) / dbu)),
                                     int(round(float(y_s) / dbu))))
            except Exception:
                return []
        return pts

    def produce_impl(self):
        dbu = self.layout.dbu
        layer_idx = self.layer_layer
        pts = self._parse_path_points(dbu)
        width_dbu = max(1, int(round(float(self.width_um) / dbu)))
        if len(pts) < 2:
            half = max(5, width_dbu * 2)
            pts = [pya.Point(-half, 0), pya.Point(half, 0)]
        self.cell.shapes(layer_idx).insert(pya.Path(pts, width_dbu))
        extent = max(max(abs(pt.x), abs(pt.y)) for pt in pts)
        _draw_label(
            self.cell, layer_idx, self.label or self.anchor_id, self.mode,
            self.net, self.show_label, max(extent, width_dbu), dbu,
        )


_ANCHOR_LIB_REF = None


def register_anchor_library():
    global _ANCHOR_LIB_REF
    if _ANCHOR_LIB_REF is not None:
        return _ANCHOR_LIB_REF

    lib = pya.Library()
    lib.description = "klink Anchor markers"
    lib.layout().register_pcell("BendAnchor", KlinkBendAnchorPcell())
    lib.layout().register_pcell("WaypointAnchor", KlinkWaypointAnchorPcell())
    lib.layout().register_pcell("CorridorAnchor", KlinkCorridorAnchorPcell())
    lib.register("klink_anchor")
    _ANCHOR_LIB_REF = lib
    return lib

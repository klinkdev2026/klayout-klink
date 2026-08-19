"""
klink_Region PCell -- reserved-layer outline + name text for an Intent Region.

The third klink PCell marker family (after Port and Anchor), fourth
application of the reserved-layer pattern (keepout 900/0 is a layer
convention only). Design: docs/REGION_INTENT_DESIGN.md.

Coordinate contract (sect.3.1): the `contours` parameter is PCell-LOCAL, with
the region's bbox lower-left anchor at the local origin. The instance
transform places the anchor in the target cell; consumers MUST compose
`instance transform x local contours` and refuse magnification != 1.

Registered as library "klink_region", PCell name "Region".
"""

from __future__ import annotations

import pya

from .region_geom import decode_contours


def _outline_width_dbu(bbox: pya.Box) -> int:
    """Deterministic outline width: thin relative to the region, never 0."""
    dim = min(bbox.width(), bbox.height())
    return max(10, dim // 200)


class KlinkRegionPcell(pya.PCellDeclarationHelper):
    """Draws the region outline (hull + holes) and the region name text.

    Parameters:
      layer        - TypeLayer  : reserved marker layer (default 999/10)
      region_name  - TypeString : short display id, e.g. "R001" (read-only)
      contours     - TypeString : canonical integer-DBU contour encoding,
                                  PCell-local (anchor at origin); see
                                  region_geom.encode_contours
      npoints      - TypeInt    : ellipse discretization used at claim time
                                  (audit record, not re-applied here)
    """

    def __init__(self):
        super().__init__()
        self.param("layer", self.TypeLayer,
                   "Region marker layer", default=pya.LayerInfo(999, 10))
        self.param("region_name", self.TypeString,
                   "Region name (display id, read-only)", default="",
                   readonly=True)
        self.param("contours", self.TypeString,
                   "Canonical contour encoding (integer DBU, local)",
                   default="")
        self.param("npoints", self.TypeInt,
                   "Ellipse discretization npoints used at claim time",
                   default=64)

    def display_text_impl(self):
        return "Region %s" % (self.region_name or "?")

    def produce_impl(self):
        layer_idx = self.layer_layer
        try:
            poly = decode_contours(self.contours)
        except Exception:
            # Never hard-fail a layout load on a corrupt parameter: draw an
            # explicit INVALID marker instead so the problem is visible.
            self.cell.shapes(layer_idx).insert(pya.Box(0, 0, 1000, 1000))
            text = pya.Text("REGION %s INVALID" % (self.region_name or "?"),
                            pya.Trans(0, 0))
            text.size = 500
            self.cell.shapes(layer_idx).insert(text)
            return

        bbox = poly.bbox()
        width = _outline_width_dbu(bbox)

        rings = [list(poly.each_point_hull())]
        for h in range(poly.holes()):
            rings.append(list(poly.each_point_hole(h)))
        for ring in rings:
            if len(ring) < 3:
                continue
            closed = ring + [ring[0]]
            self.cell.shapes(layer_idx).insert(pya.Path(closed, width))

        # Name text just inside the anchor corner (local origin area).
        text_size = max(100, min(bbox.width(), bbox.height()) // 8)
        text = pya.Text(self.region_name or "?",
                        pya.Trans(bbox.left + width * 2, bbox.bottom + width * 2))
        text.size = text_size
        self.cell.shapes(layer_idx).insert(text)

    def can_create_from_shape_impl(self):
        return False


# ---------------------------------------------------------------------------
# Library registration (same lifecycle pattern as port_pcell.py)
# ---------------------------------------------------------------------------

_REGION_LIB_REF = None  # module-level anchor against GC


def register_region_library():
    """Create (once) and return the klink_region library."""
    global _REGION_LIB_REF
    if _REGION_LIB_REF is not None:
        return _REGION_LIB_REF

    lib = pya.Library()
    lib.description = "klink Intent Region markers"
    lib.layout().register_pcell("Region", KlinkRegionPcell())
    lib.register("klink_region")
    _REGION_LIB_REF = lib
    return lib

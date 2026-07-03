"""Small orchestration helpers for nanodevice examples and tests."""

from __future__ import annotations

from .devices.hallbar import HallBarSpec, build_hallbar
from .ebl.writefield import plan_writefields


def build_hallbar_bundle(spec: HallBarSpec | None = None, *, writefield: dict | None = None) -> dict:
    """Build a Hall bar plus optional writefield obstacles."""

    bundle = build_hallbar(spec)
    if writefield is not None:
        wf = plan_writefields(**writefield)
        bundle["writefield"] = wf.to_dict()
        bundle["obstacle_boxes_um"] = wf.obstacle_boxes_um
        bundle["anchor_marks"] = [*bundle["anchor_marks"], *wf.corridor_anchor_specs]
    return bundle


def route_hallbar_offline(bundle: dict, *, spacing_um: float = 4.0) -> dict:
    """Route a generated Hall bar with existing klink routing backends."""

    from klink.routing.backends.geometric.tapered_segments import route_tapered_hybrid_many

    contact_ports = list(bundle["contact_ports"])
    pad_ports = list(bundle["pad_ports"])
    pairs = []
    for contact, pad in zip(contact_ports, pad_ports):
        pairs.append({"net": contact["net"], "source": contact, "target": pad, "route_layer": contact.get("target_layer")})
    return route_tapered_hybrid_many(
        pairs,
        anchors=bundle.get("anchor_marks") or [],
        obstacle_bboxes=bundle.get("obstacle_boxes_um") or [],
        spacing_um=spacing_um,
        validate_sibling_overlap=True,
    )

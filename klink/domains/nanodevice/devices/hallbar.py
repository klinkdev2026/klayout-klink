"""Hall bar generator that emits shapes plus klink Port/Anchor semantics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Sequence


@dataclass(frozen=True)
class HallBarSpec:
    name: str = "HB"
    center_um: tuple[float, float] = (0.0, 0.0)
    bar_length_um: float = 144.0
    bar_width_um: float = 8.0
    contact_count: int = 6
    contact_width_um: float = 4.0
    contact_length_um: float = 14.0
    contact_pitch_um: float = 36.0
    pad_size_um: float = 12.0
    pad_gap_um: float = 34.0
    # Process layers are REQUIRED and example-owned: klink ships no process
    # layers, so the caller (your example / example_template/nanodevice/hallbar.py) passes
    # them for YOUR process. (999/99 Port + 999/1 Anchor are klink's reserved
    # marker layers, like 900/0 keepout — those keep sensible defaults.)
    device_layer: str | None = None
    metal_layer: str | None = None
    label_layer: str | None = None
    port_layer: str = "999/99"
    anchor_layer: str = "999/1"
    route_layer: str | None = None

    def __post_init__(self) -> None:
        missing = [n for n in ("device_layer", "metal_layer", "label_layer",
                               "route_layer") if getattr(self, n) is None]
        if missing:
            raise ValueError(
                f"HallBarSpec is missing process layer(s) {missing}; klink ships "
                "no process layers -- pass them for YOUR process, e.g. "
                "device_layer='1/0', metal_layer='10/0', label_layer='6/0', "
                "route_layer='12/0'. See example_template/nanodevice/hallbar.py.")


def _parse_layer(layer: str) -> tuple[int, int]:
    parts = layer.split("/")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def _box_item(layer: str, bbox: Sequence[float]) -> dict:
    layer_num, datatype = _parse_layer(layer)
    return {"kind": "box", "layer": layer_num, "datatype": datatype, "bbox_um": [float(v) for v in bbox]}


def _text_item(layer: str, text: str, position: Sequence[float], size_um: float) -> dict:
    layer_num, datatype = _parse_layer(layer)
    return {
        "kind": "text",
        "layer": layer_num,
        "datatype": datatype,
        "text": text,
        "position_um": [float(position[0]), float(position[1])],
        "size_um": float(size_um),
    }


def _port_mark(spec: HallBarSpec, name: str, center: Sequence[float], orientation: float, width: float, net: str, *, port_type: str = "electrical") -> dict:
    return {
        "layer": spec.port_layer,
        "name": name,
        "center_um": [float(center[0]), float(center[1])],
        "orientation": float(orientation),
        "width_um": float(width),
        "port_type": port_type,
        "net": net,
        "target_layer": spec.route_layer,
        "access_mode": "point",
        "show_label": True,
    }


def build_hallbar(spec: HallBarSpec | None = None) -> dict:
    """Build a Hall bar bundle without routing.

    The returned dictionaries are ready for ``shape.insert_many``,
    ``port.mark`` and ``anchor.mark``.  Every device contact receives a real
    net and every pad is a netless ``candidate_sink`` Port.
    """

    spec = spec or HallBarSpec()
    if spec.contact_count < 2:
        raise ValueError("contact_count must be at least 2")
    if spec.contact_count % 2:
        raise ValueError("contact_count must be even so top/bottom contacts pair cleanly")
    cx, cy = spec.center_um
    half_l = spec.bar_length_um / 2.0
    half_w = spec.bar_width_um / 2.0
    shape_items = [
        _box_item(spec.device_layer, [cx - half_l, cy - half_w, cx + half_l, cy + half_w]),
        _text_item(spec.label_layer, spec.name, [cx - half_l, cy + half_w + 6.0], 3.0),
    ]

    port_marks: list[dict] = []
    contact_ports: list[dict] = []
    pad_ports: list[dict] = []
    half_contacts = spec.contact_count // 2
    first_x = cx - (half_contacts - 1) * spec.contact_pitch_um / 2.0
    pad_y_top = cy + half_w + spec.contact_length_um + spec.pad_gap_um
    pad_y_bottom = cy - half_w - spec.contact_length_um - spec.pad_gap_um

    for side, sign, orientation in (("T", 1.0, 90.0), ("B", -1.0, 270.0)):
        for i in range(half_contacts):
            x = first_x + i * spec.contact_pitch_um
            contact_name = f"{spec.name}_{side}{i}"
            net = f"{spec.name.lower()}_{side.lower()}{i}"
            y0 = cy + sign * half_w
            y1 = y0 + sign * spec.contact_length_um
            bbox = [x - spec.contact_width_um / 2.0, min(y0, y1), x + spec.contact_width_um / 2.0, max(y0, y1)]
            shape_items.append(_box_item(spec.metal_layer, bbox))
            contact_center = [x, y1]
            contact_port = _port_mark(spec, contact_name, contact_center, orientation, spec.contact_width_um, net)
            contact_ports.append(contact_port)
            port_marks.append(contact_port)

            pad_y = pad_y_top if sign > 0 else pad_y_bottom
            pad_name = f"{spec.name}_PAD_{side}{i}"
            pad_bbox = [
                x - spec.pad_size_um / 2.0,
                pad_y - spec.pad_size_um / 2.0,
                x + spec.pad_size_um / 2.0,
                pad_y + spec.pad_size_um / 2.0,
            ]
            shape_items.append(_box_item(spec.metal_layer, pad_bbox))
            pad_orientation = 270.0 if sign > 0 else 90.0
            # The pad metal is large, but the route access neck is contact-sized.
            # Marking the whole pad width as the Port width makes simple vertical
            # fanout look like a large taper and can force avoidable jogs.
            pad_port = _port_mark(
                spec,
                pad_name,
                [x, pad_y - sign * spec.pad_size_um / 2.0],
                pad_orientation,
                spec.contact_width_um,
                "",
                port_type="candidate_sink",
            )
            pad_ports.append(pad_port)
            port_marks.append(pad_port)

    anchor_marks = _fanout_waypoints(spec, contact_ports, pad_ports)
    return {
        "spec": asdict(spec),
        "shape_items": shape_items,
        "port_marks": port_marks,
        "anchor_marks": anchor_marks,
        "contact_ports": contact_ports,
        "pad_ports": pad_ports,
        "report": {
            "device": "hallbar",
            "contact_count": len(contact_ports),
            "pad_count": len(pad_ports),
            "shape_count": len(shape_items),
            "port_count": len(port_marks),
            "anchor_count": len(anchor_marks),
        },
    }


def _fanout_waypoints(spec: HallBarSpec, contact_ports: Sequence[dict], pad_ports: Sequence[dict]) -> list[dict]:
    anchors = []
    for contact, pad in zip(contact_ports, pad_ports):
        x = (float(contact["center_um"][0]) + float(pad["center_um"][0])) / 2.0
        y = (float(contact["center_um"][1]) + float(pad["center_um"][1])) / 2.0
        anchors.append({
            "layer": spec.anchor_layer,
            "id": f"{contact['name']}_WAYPOINT",
            "center_um": [x, y],
            "kind": "waypoint_region",
            "mode": "flexible",
            "net": contact["net"],
            "label": "fanout",
            "show_label": True,
            "required": True,
            "priority": 0,
            "width_um": spec.contact_width_um * 2.0,
            "height_um": spec.pad_gap_um,
            "path_points": "",
        })
    return anchors

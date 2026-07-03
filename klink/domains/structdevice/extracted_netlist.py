"""Build extracted-side device netlists from layout connectivity."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from .reference_netlist import build_reference_netlist


class ExtractedNetlistError(ValueError):
    """Layout extraction or terminal probing failed with an instructive message."""


def build_extracted_netlist(
    gds_path: str | Path,
    top: str,
    *,
    conductors: Sequence[str],
    vias: Sequence[Sequence[str]],
    device_instances: Sequence[Mapping[str, Any]],
    device_terminals: Mapping[str, Sequence[str]],
    terminal_points: Mapping[str, Sequence[Any]],
) -> Any:
    """Extract conductor/via connectivity and bind declared device terminals."""

    pya = _pya()
    layout = pya.Layout()
    layout.read(str(gds_path))
    cell = layout.cell(_nonempty_string(top, "top"))
    if cell is None:
        raise ExtractedNetlistError(f"top cell {top!r} not found in {gds_path}")
    cell.flatten(-1)
    conductors_t = tuple(_layer_key(layer, "conductor") for layer in conductors)
    vias_t = tuple(_via_entry(entry) for entry in vias)
    conductor_set = set(conductors_t)
    for a, _, b in vias_t:
        if a not in conductor_set or b not in conductor_set:
            raise ExtractedNetlistError(f"via entry {(a, _, b)!r} references undeclared conductor")

    present = _present_layers(layout)
    needed = list(conductors_t) + [entry[1] for entry in vias_t]
    for key in needed:
        if key not in present:
            layer, datatype = _parse_layer(key)
            present[key] = layout.layer(layer, datatype)

    any_layer = present[conductors_t[0]]
    l2n = pya.LayoutToNetlist(pya.RecursiveShapeIterator(layout, cell, any_layer))
    regions = {key: l2n.make_layer(present[key], key) for key in needed}
    for layer in conductors_t:
        l2n.connect(regions[layer])
    for a, via_layer, b in vias_t:
        l2n.connect(regions[via_layer])
        l2n.connect(regions[a], regions[via_layer])
        l2n.connect(regions[via_layer], regions[b])
    l2n.extract_netlist()
    extracted = l2n.netlist()
    extracted.flatten()
    circuits = list(extracted.each_circuit())
    if len(circuits) > 1:
        raise ExtractedNetlistError(f"expected one flattened extracted circuit, got {len(circuits)}")
    nets = [] if not circuits else sorted(circuits[0].each_net(), key=lambda net: net.cluster_id)
    net_id_by_cluster = {net.cluster_id: f"net_{index}" for index, net in enumerate(nets)}

    net_terms: dict[str, list[str]] = {}
    for raw in _instances(device_instances):
        iid = _required_str(raw, "instance_id", "device instance")
        cell_name = _required_str(raw, "device_cell", f"device instance {iid}")
        if cell_name not in device_terminals:
            raise ExtractedNetlistError(f"device instance {iid!r} uses unknown device_cell {cell_name!r}")
        for terminal in device_terminals[cell_name]:
            ref = f"{iid}.{terminal}"
            point = _terminal_point(terminal_points.get(ref), ref, conductor_set)
            net = l2n.probe_net(regions[point[2]], pya.DPoint(point[0], point[1]))
            if net is None:
                raise ExtractedNetlistError(f"terminal {ref!r} at ({point[0]}, {point[1]}, {point[2]}) is floating")
            net_id = net_id_by_cluster.get(net.cluster_id, f"net_c{net.cluster_id}")
            net_terms.setdefault(net_id, []).append(ref)

    device_netlist = {
        "top": top,
        "instances": [dict(raw) for raw in device_instances],
        "nets": [{"net_id": net_id, "terminals": refs} for net_id, refs in sorted(net_terms.items())],
    }
    return build_reference_netlist(device_netlist, device_terminals, top_name=top)


def _pya() -> Any:
    try:
        import klayout.db as pya
    except ImportError as exc:
        raise ExtractedNetlistError("klayout.db is required; run tests with the project venv") from exc
    return pya


def _present_layers(layout: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for index in layout.layer_indexes():
        info = layout.get_info(index)
        out[f"{info.layer}/{info.datatype}"] = index
    return out


def _instances(value: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ExtractedNetlistError("device_instances must be a sequence")
    out = tuple(value)
    if not out:
        raise ExtractedNetlistError("device_instances must not be empty")
    return out


def _terminal_point(value: Any, ref: str, conductors: set[str]) -> tuple[float, float, str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        raise ExtractedNetlistError(f"terminal_points[{ref!r}] must be (x_um, y_um, layer)")
    x = _number(value[0], f"terminal_points[{ref!r}].x")
    y = _number(value[1], f"terminal_points[{ref!r}].y")
    layer = _layer_key(value[2], f"terminal_points[{ref!r}].layer")
    if layer not in conductors:
        raise ExtractedNetlistError(f"terminal {ref!r} uses layer {layer!r}, not declared as a conductor")
    return (x, y, layer)


def _via_entry(value: Sequence[str]) -> tuple[str, str, str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        raise ExtractedNetlistError("via entries must be (conductor, via_layer, conductor)")
    return (_layer_key(value[0], "via conductor"), _layer_key(value[1], "via layer"), _layer_key(value[2], "via conductor"))


def _layer_key(value: Any, label: str) -> str:
    text = _nonempty_string(value, label)
    _parse_layer(text)
    return text


def _parse_layer(value: str) -> tuple[int, int]:
    try:
        layer, datatype = value.split("/")
        return int(layer), int(datatype)
    except (AttributeError, ValueError):
        raise ExtractedNetlistError(f"layer must be 'layer/datatype', got {value!r}") from None


def _required_str(mapping: Mapping[str, Any], field: str, label: str) -> str:
    return _nonempty_string(mapping.get(field), f"{label}.{field}")


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExtractedNetlistError(f"{label} must be a non-empty string")
    return value


def _number(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ExtractedNetlistError(f"{label} must be a number")
    return float(value)

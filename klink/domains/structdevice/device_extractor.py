"""Generic device-cell extraction helpers for KLayout LayoutToNetlist."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class DeviceExtractorError(ValueError):
    """Device extractor registration failed with an instructive message."""


@dataclass(frozen=True)
class DeviceExtractorRegistration:
    """Registration data returned for one declared device cell."""

    cell_name: str
    extractor: Any
    layers: Mapping[str, Any]


def register_device_extractors(
    l2n: Any,
    *,
    device_terminals: Mapping[str, Sequence[str]],
    terminal_layers: Mapping[str, Any],
    layout: Any = None,
    top_cell: Any = None,
) -> list[DeviceExtractorRegistration]:
    """Register GenericDeviceExtractor instances for declared device cells.

    ``device_terminals`` maps a device cell name to its caller-declared terminal
    names. ``terminal_layers`` can either be keyed by cell name, then terminal
    name, or directly by terminal name when all cells share the same layer map.
    Layer values may be KLayout Regions, integer layer indexes, ``LayerInfo``
    objects, or ``(layer, datatype)`` tuples.

    ``layout`` / ``top_cell`` are the source layout and top cell behind the
    RecursiveShapeIterator the ``l2n`` was built from. On klayout >= 0.29 they
    can be omitted (recovered via ``original_layout``/``original_top_cell``);
    older klayout exposes no original-layout accessors, so there they are
    required.
    """

    pya = _pya()
    if not hasattr(l2n, "extract_devices"):
        raise DeviceExtractorError("l2n must be a klayout.db.LayoutToNetlist-like object")

    if layout is None and hasattr(l2n, "original_layout"):
        layout = l2n.original_layout()
    if top_cell is None and hasattr(l2n, "original_top_cell"):
        top_cell = l2n.original_top_cell()
    if layout is None or top_cell is None:
        raise DeviceExtractorError(
            "pass layout= and top_cell= (the layout and top cell behind the "
            "RecursiveShapeIterator this l2n was built from); this klayout "
            "version's LayoutToNetlist exposes no original_layout/original_top_cell"
        )

    registrations: list[DeviceExtractorRegistration] = []
    for cell_name in sorted(device_terminals):
        terminals = _terminal_names(device_terminals[cell_name], f"device_terminals[{cell_name!r}]")
        layer_spec = _layer_spec_for_cell(terminal_layers, cell_name)
        layers: dict[str, Any] = {}
        for terminal in terminals:
            if terminal not in layer_spec:
                raise DeviceExtractorError(f"terminal_layers for {cell_name!r} is missing terminal {terminal!r}")
            layers[terminal] = _region_for_layer(l2n, layout, layer_spec[terminal], f"{cell_name}.{terminal}")

        marker = _device_marker_region(pya, l2n, layout, top_cell, cell_name)
        if marker.is_empty():
            raise DeviceExtractorError(f"no instances of device cell {cell_name!r} found below top cell {top_cell.name!r}")
        layers[_DeviceCellExtractor.MARKER_LAYER] = marker

        extractor = _DeviceCellExtractor(cell_name, terminals)
        l2n.extract_devices(extractor, layers)
        registrations.append(DeviceExtractorRegistration(cell_name=cell_name, extractor=extractor, layers=layers))
    return registrations


class _DeviceCellExtractor:
    """Factory wrapper for a KLayout GenericDeviceExtractor subclass."""

    MARKER_LAYER = "__device_cell_marker__"

    def __new__(cls, cell_name: str, terminals: Sequence[str]) -> Any:
        pya = _pya()

        class Extractor(pya.GenericDeviceExtractor):
            def __init__(self) -> None:
                super().__init__()
                self._cell_name = cell_name
                self._terminals = tuple(terminals)

            def setup(self) -> None:
                self.name = self._cell_name
                device_class = pya.DeviceClass()
                device_class.name = self._cell_name
                for terminal_name in self._terminals:
                    definition = pya.DeviceTerminalDefinition()
                    definition.name = terminal_name
                    device_class.add_terminal(definition)
                self.register_device_class(device_class)
                for terminal_name in self._terminals:
                    self.define_layer(terminal_name, f"{self._cell_name}.{terminal_name}")
                self.define_layer(_DeviceCellExtractor.MARKER_LAYER, f"{self._cell_name}.marker")

            def get_connectivity(self, _layout: Any, layers: Sequence[int]) -> Any:
                connectivity = pya.Connectivity()
                marker_layer = layers[-1]
                connectivity.connect(marker_layer)
                for layer_index in layers[:-1]:
                    connectivity.connect(marker_layer, layer_index)
                return connectivity

            def extract_devices(self, shapes: Sequence[Any]) -> None:
                marker = shapes[-1]
                if marker.is_empty():
                    return
                device = self.create_device()
                for terminal_name, terminal_region in zip(self._terminals, shapes[:-1]):
                    terminal_shape = terminal_region & marker
                    if terminal_shape.is_empty():
                        self.warn(f"{self._cell_name}: missing terminal geometry for {terminal_name}")
                        continue
                    self.define_terminal(device, terminal_name, terminal_name, terminal_shape.bbox())

        return Extractor()


def _pya() -> Any:
    try:
        import klayout.db as pya
    except ImportError as exc:
        raise DeviceExtractorError("klayout.db is required; run with the project venv") from exc
    return pya


def _terminal_names(value: Sequence[str], label: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise DeviceExtractorError(f"{label} must be a sequence of terminal names")
    names = tuple(_nonempty_string(item, f"{label} entry") for item in value)
    if not names:
        raise DeviceExtractorError(f"{label} must not be empty")
    if len(set(names)) != len(names):
        raise DeviceExtractorError(f"{label} contains duplicate terminal names")
    return names


def _layer_spec_for_cell(terminal_layers: Mapping[str, Any], cell_name: str) -> Mapping[str, Any]:
    if cell_name in terminal_layers:
        value = terminal_layers[cell_name]
        if not isinstance(value, Mapping):
            raise DeviceExtractorError(f"terminal_layers[{cell_name!r}] must map terminal names to layers")
        return value
    if not isinstance(terminal_layers, Mapping):
        raise DeviceExtractorError("terminal_layers must be a mapping")
    return terminal_layers


def _region_for_layer(l2n: Any, layout: Any, spec: Any, name: str) -> Any:
    pya = _pya()
    if isinstance(spec, pya.Region):
        return spec
    if isinstance(spec, int):
        return l2n.make_layer(spec, name)
    if isinstance(spec, pya.LayerInfo):
        return l2n.make_layer(layout.layer(spec), name)
    if isinstance(spec, tuple) and len(spec) == 2 and all(isinstance(part, int) for part in spec):
        return l2n.make_layer(layout.layer(spec[0], spec[1]), name)
    raise DeviceExtractorError(
        f"layer spec for {name!r} must be a Region, layer index, LayerInfo, or (layer, datatype) tuple"
    )


def _device_marker_region(pya: Any, l2n: Any, layout: Any, top_cell: Any, cell_name: str) -> Any:
    marker_boxes: list[Any] = []
    target = layout.cell(cell_name)
    if target is None:
        return pya.Region()
    _collect_instance_boxes(pya, top_cell, target.cell_index(), pya.CplxTrans(), marker_boxes)
    if not marker_boxes:
        return pya.Region()

    marker_layer = _temporary_marker_layer(pya, layout, cell_name)
    for box in marker_boxes:
        top_cell.shapes(marker_layer).insert(box)
    return l2n.make_layer(marker_layer, f"{cell_name}.__device_cell_marker__")


def _collect_instance_boxes(pya: Any, cell: Any, target_index: int, parent_trans: Any, marker_boxes: list[Any]) -> None:
    for instance in cell.each_inst():
        instance_transforms = _instance_transforms(pya, instance)
        child = instance.cell
        for local_trans in instance_transforms:
            trans = parent_trans * local_trans
            if instance.cell_index == target_index:
                box = child.bbox()
                if not box.empty():
                    marker_boxes.append(_shrunk_box(_box_from_dbox(pya, box.transformed(trans))))
            _collect_instance_boxes(pya, child, target_index, trans, marker_boxes)


def _temporary_marker_layer(pya: Any, layout: Any, cell_name: str) -> int:
    base_layer = 65000
    datatype = sum(ord(ch) for ch in cell_name) % 32000
    for offset in range(1000):
        layer = base_layer - offset
        if layout.find_layer(layer, datatype) is None:
            return layout.layer(pya.LayerInfo(layer, datatype))
    raise DeviceExtractorError("could not allocate a temporary marker layer")


def _instance_transforms(pya: Any, instance: Any) -> tuple[Any, ...]:
    if not instance.is_regular_array():
        return (instance.cplx_trans,)
    transforms = []
    base = instance.cplx_trans
    for ia in range(instance.na):
        for ib in range(instance.nb):
            offset = pya.CplxTrans(pya.Vector(instance.a.x * ia + instance.b.x * ib, instance.a.y * ia + instance.b.y * ib))
            transforms.append(base * offset)
    return tuple(transforms)


def _box_from_dbox(pya: Any, box: Any) -> Any:
    return pya.Box(
        int(round(box.left)),
        int(round(box.bottom)),
        int(round(box.right)),
        int(round(box.top)),
    )


def _shrunk_box(box: Any) -> Any:
    if box.width() <= 2 or box.height() <= 2:
        return box
    return type(box)(box.left + 1, box.bottom + 1, box.right - 1, box.top - 1)


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeviceExtractorError(f"{label} must be a non-empty string")
    return value

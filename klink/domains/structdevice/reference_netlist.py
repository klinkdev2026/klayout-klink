"""Build reference-side KLayout netlists from device-level netlists."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


class ReferenceNetlistError(ValueError):
    """Reference netlist construction failed with an instructive message."""


def device_classes(device_terminals: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    """Create one ``pya.DeviceClass`` per device cell.

    Terminal names are entirely caller-provided.  No transistor vocabulary is
    assumed here.
    """

    pya = _pya()
    classes: dict[str, Any] = {}
    for cell_name in sorted(device_terminals):
        terminals = _terminal_names(device_terminals[cell_name], f"device_terminals[{cell_name!r}]")
        cls = pya.DeviceClass()
        cls.name = cell_name
        for terminal in terminals:
            definition = pya.DeviceTerminalDefinition()
            definition.name = terminal
            cls.add_terminal(definition)
        classes[cell_name] = cls
    return classes


def build_reference_netlist(
    device_netlist: Mapping[str, Any],
    device_terminals: Mapping[str, Sequence[str]],
    *,
    top_name: str = "TOP",
) -> Any:
    """Convert a device netlist dict into a ``pya.Netlist``."""

    pya = _pya()
    classes = device_classes(device_terminals)
    netlist = pya.Netlist()
    netlist.create()
    for cls in classes.values():
        netlist.add(cls)
    circuit = pya.Circuit()
    circuit.name = _nonempty_string(top_name, "top_name")
    netlist.add(circuit)

    devices: dict[str, Any] = {}
    for raw in _instances(device_netlist):
        instance_id = _required_str(raw, "instance_id", "instance")
        cell = _required_str(raw, "device_cell", f"instance {instance_id}")
        if cell not in classes:
            known = ", ".join(sorted(classes))
            raise ReferenceNetlistError(f"instance {instance_id!r} uses unknown device_cell {cell!r}; known: {known}")
        devices[instance_id] = circuit.create_device(classes[cell], instance_id)

    nets: dict[str, Any] = {}
    for raw_net in _nets(device_netlist):
        net_id = _required_str(raw_net, "net_id", "net")
        if net_id not in nets:
            nets[net_id] = circuit.create_net(net_id)
        net = nets[net_id]
        for ref in _terminal_refs(raw_net.get("terminals"), f"net {net_id!r} terminals"):
            instance_id, terminal = _split_ref(ref)
            if instance_id not in devices:
                raise ReferenceNetlistError(f"net {net_id!r} references unknown instance {instance_id!r}")
            cell = _instance_cell(device_netlist, instance_id)
            if terminal not in device_terminals[cell]:
                raise ReferenceNetlistError(
                    f"net {net_id!r} references terminal {terminal!r} not declared for device_cell {cell!r}"
                )
            devices[instance_id].connect_terminal(terminal, net)
    return netlist


def _pya() -> Any:
    try:
        import klayout.db as pya
    except ImportError as exc:
        raise ReferenceNetlistError("klayout.db is required; run tests with the project venv") from exc
    return pya


def _instances(device_netlist: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    instances = device_netlist.get("instances")
    if not isinstance(instances, list):
        raise ReferenceNetlistError("device_netlist.instances must be a list")
    return instances


def _nets(device_netlist: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    nets = device_netlist.get("nets")
    if not isinstance(nets, list):
        raise ReferenceNetlistError("device_netlist.nets must be a list")
    return nets


def _instance_cell(device_netlist: Mapping[str, Any], instance_id: str) -> str:
    for raw in _instances(device_netlist):
        if raw.get("instance_id") == instance_id:
            return str(raw.get("device_cell"))
    raise ReferenceNetlistError(f"unknown instance {instance_id!r}")


def _terminal_names(value: Sequence[str], label: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ReferenceNetlistError(f"{label} must be a sequence of terminal names")
    out = tuple(_nonempty_string(item, f"{label} entry") for item in value)
    if not out:
        raise ReferenceNetlistError(f"{label} must not be empty")
    if len(set(out)) != len(out):
        raise ReferenceNetlistError(f"{label} contains duplicate terminal names")
    return out


def _terminal_refs(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ReferenceNetlistError(f"{label} must be a sequence")
    return tuple(_nonempty_string(item, f"{label} entry") for item in value)


def _required_str(mapping: Mapping[str, Any], field: str, context: str) -> str:
    return _nonempty_string(mapping.get(field), f"{context}.{field}")


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReferenceNetlistError(f"{label} must be a non-empty string")
    return value


def _split_ref(ref: str) -> tuple[str, str]:
    if "." not in ref:
        raise ReferenceNetlistError(f"terminal reference {ref!r} must be 'instance.terminal'")
    instance_id, terminal = ref.split(".", 1)
    if not instance_id or not terminal:
        raise ReferenceNetlistError(f"terminal reference {ref!r} must be 'instance.terminal'")
    return instance_id, terminal

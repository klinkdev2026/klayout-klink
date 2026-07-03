"""Gate-level netlist to structdevice device netlist mapping.

This module consumes a small, flat subset of Yosys ``write_json`` output or a
trivial hand-authored gate list.  It does not synthesize, place, route, or
interpret geometry; it only expands declarative gate-library connectivity into
the device-level netlist vocabulary used by structdevice extraction.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Mapping, Sequence

from .lvs_lite import declared_nets_from_dicts


class LogicMapError(ValueError):
    """Bad logic-map input.  Messages tell the caller what to fix."""


def map_logic_to_devices(netlist: Mapping[str, Any], gate_library: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Expand a flat gate netlist through a declarative device gate library."""

    library = _validate_library(gate_library)
    gates = _normalize_netlist(netlist)
    _validate_drivers(gates, library)

    instances: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    nets: OrderedDict[str, list[str]] = OrderedDict()
    next_instance = 1
    for gate_index, gate in enumerate(gates):
        rule = library["gates"].get(gate["type"])
        if rule is None:
            known = ", ".join(sorted(library["gates"]))
            raise LogicMapError(
                f"gate {gate['name']!r} has unknown type {gate['type']!r}; "
                f"add it to the gate library. Available gates: {known}"
            )
        local_ids: dict[str, str] = {}
        for role, device_cell in rule["devices"].items():
            instance_id = f"X{next_instance}"
            next_instance += 1
            local_ids[role] = instance_id
            instances.append({"instance_id": instance_id, "device_cell": device_cell})
        # gate grouping survives expansion so the placer can place by
        # module instead of one flat column (user ruling)
        groups.append({"group": gate["name"],
                       "gate_type": gate["type"],
                       "instances": list(local_ids.values())})
        for port_name, terminal_refs in rule["connect"].items():
            net_id = _resolve_port_net(gate, rule, port_name, gate_index)
            bucket = nets.setdefault(net_id, [])
            for terminal_ref in terminal_refs:
                role, terminal = _split_terminal_ref(terminal_ref, gate["type"])
                if role not in local_ids:
                    raise LogicMapError(
                        f"gate type {gate['type']!r} connect references device role {role!r}; "
                        "add that role under devices or fix the connect table."
                    )
                if terminal not in {"G", "S", "D"}:
                    raise LogicMapError(
                        f"gate type {gate['type']!r} terminal {terminal!r} is not in G/S/D; "
                        "fix the library terminal vocabulary."
                    )
                bucket.append(f"{local_ids[role]}.{terminal}")

    output = {
        "instances": instances,
        "nets": [{"net_id": net_id, "terminals": tuple(terminals)} for net_id, terminals in nets.items()],
        "groups": groups,
    }
    validate_device_netlist(output)
    output["nets"] = [{"net_id": net["net_id"], "terminals": list(net["terminals"])} for net in output["nets"]]
    return output


def validate_device_netlist(netlist: Mapping[str, Any]) -> None:
    """Validate one-terminal-one-net semantics using lvs_lite's declarations."""

    declared_nets_from_dicts([
        {"net": item.get("net_id"), "terminals": item.get("terminals")}
        for item in netlist.get("nets", [])
    ])


def _validate_library(gate_library: Mapping[str, Any]) -> dict[str, Any]:
    gates = gate_library.get("gates")
    if not isinstance(gates, Mapping) or not gates:
        raise LogicMapError("gate library must provide a non-empty 'gates' mapping.")
    normalized: dict[str, Any] = {"family": str(gate_library.get("family") or ""), "gates": OrderedDict()}
    for gate_type in sorted(gates):
        rule = gates[gate_type]
        if not isinstance(rule, Mapping):
            raise LogicMapError(f"gate library entry {gate_type!r} must be an object.")
        devices = rule.get("devices")
        connect = rule.get("connect")
        internal_nets = rule.get("internal_nets", [])
        if not isinstance(devices, Mapping) or not devices:
            raise LogicMapError(f"gate {gate_type!r} must name device cells under 'devices'.")
        if not isinstance(connect, Mapping) or not connect:
            raise LogicMapError(f"gate {gate_type!r} must provide a port-to-terminal 'connect' table.")
        if not isinstance(internal_nets, list) or not all(isinstance(net, str) and net for net in internal_nets):
            raise LogicMapError(f"gate {gate_type!r} internal_nets must be a list of non-empty strings.")
        output_ports = rule.get("output_ports", ["Y"])
        if not isinstance(output_ports, list) or not all(isinstance(port, str) and port for port in output_ports):
            raise LogicMapError(f"gate {gate_type!r} output_ports must list the driven logic ports.")
        for role, cell in devices.items():
            if not isinstance(role, str) or not role or not isinstance(cell, str) or not cell:
                raise LogicMapError(f"gate {gate_type!r} devices must map non-empty role names to cell names.")
        for port, refs in connect.items():
            if not isinstance(port, str) or not port:
                raise LogicMapError(f"gate {gate_type!r} has an empty connect port name.")
            if not isinstance(refs, list) or not refs:
                raise LogicMapError(f"gate {gate_type!r} connect port {port!r} must list terminals.")
            for ref in refs:
                _split_terminal_ref(ref, gate_type)
        normalized["gates"][str(gate_type)] = {
            "devices": OrderedDict((str(role), str(cell)) for role, cell in devices.items()),
            "internal_nets": list(internal_nets),
            "output_ports": list(output_ports),
            "connect": OrderedDict((str(port), list(refs)) for port, refs in connect.items()),
        }
    return normalized


def _normalize_netlist(netlist: Mapping[str, Any]) -> list[dict[str, Any]]:
    if "gates" in netlist:
        return _normalize_hand_netlist(netlist)
    return _normalize_yosys_json(netlist)


def _normalize_hand_netlist(netlist: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_gates = netlist.get("gates")
    if not isinstance(raw_gates, list):
        raise LogicMapError("hand netlist 'gates' must be a list.")
    gates = []
    for index, raw in enumerate(raw_gates):
        if not isinstance(raw, Mapping):
            raise LogicMapError(f"gates[{index}] must be an object with type, inputs, and output.")
        gate_type = str(raw.get("type") or "")
        if not gate_type:
            raise LogicMapError(f"gates[{index}] is missing type; set it to a library gate such as NAND2.")
        inputs = raw.get("inputs", {})
        if not isinstance(inputs, Mapping):
            raise LogicMapError(f"gates[{index}] inputs must map port names to net names.")
        connections = OrderedDict((str(port), str(net)) for port, net in inputs.items())
        if "output" in raw:
            connections[str(raw.get("output_port") or "Y")] = str(raw["output"])
        for power in ("VDD", "GND"):
            if power in raw:
                connections[power] = str(raw[power])
        gates.append({"name": str(raw.get("name") or f"gate_{index}"), "type": gate_type, "connections": connections})
    return gates


def _normalize_yosys_json(netlist: Mapping[str, Any]) -> list[dict[str, Any]]:
    modules = netlist.get("modules")
    if not isinstance(modules, Mapping) or not modules:
        raise LogicMapError("Yosys JSON must contain one flat module under 'modules'.")
    if len(modules) != 1:
        raise LogicMapError("flatten first; this mapper consumes one flat module.")
    module_name, module = next(iter(modules.items()))
    if not isinstance(module, Mapping):
        raise LogicMapError(f"module {module_name!r} must be an object.")
    if module.get("memories"):
        raise LogicMapError("flatten first; this mapper consumes one flat module and no memories.")
    cells = module.get("cells", {})
    if not isinstance(cells, Mapping):
        raise LogicMapError(f"module {module_name!r} cells must be a mapping.")
    bit_names = _bit_names(module)
    gates = []
    for cell_name, cell in cells.items():
        if not isinstance(cell, Mapping):
            raise LogicMapError(f"cell {cell_name!r} must be an object.")
        gate_type = str(cell.get("type") or "")
        if not gate_type:
            raise LogicMapError(f"cell {cell_name!r} is missing type; set it to a library gate.")
        connections = OrderedDict()
        raw_connections = cell.get("connections", {})
        if not isinstance(raw_connections, Mapping):
            raise LogicMapError(f"cell {cell_name!r} connections must map ports to bit lists.")
        for port, bits in raw_connections.items():
            resolved = _resolve_bits(bits, bit_names, cell_name, str(port))
            connections[str(port)] = resolved
        gates.append({"name": str(cell_name), "type": gate_type, "connections": connections})
    return gates


def _bit_names(module: Mapping[str, Any]) -> dict[str, str]:
    bit_to_name: dict[str, str] = {}
    netnames = module.get("netnames", {})
    if isinstance(netnames, Mapping):
        for net_name, info in netnames.items():
            bits = info.get("bits", []) if isinstance(info, Mapping) else []
            for bit in bits:
                bit_to_name.setdefault(str(bit), str(net_name))
    ports = module.get("ports", {})
    if isinstance(ports, Mapping):
        for port_name, info in ports.items():
            bits = info.get("bits", []) if isinstance(info, Mapping) else []
            for bit in bits:
                bit_to_name.setdefault(str(bit), str(port_name))
    return bit_to_name


def _resolve_bits(bits: Any, bit_names: Mapping[str, str], cell_name: str, port: str) -> str:
    raw_bits = bits if isinstance(bits, list) else [bits]
    if len(raw_bits) != 1:
        raise LogicMapError(
            f"cell {cell_name!r} port {port!r} is connected to {len(raw_bits)} bits; "
            "split buses before logic mapping."
        )
    bit = raw_bits[0]
    if bit == "0":
        return "GND"
    if bit == "1":
        return "VDD"
    return bit_names.get(str(bit), f"net_{bit}")


def _resolve_port_net(gate: Mapping[str, Any], rule: Mapping[str, Any], port_name: str, gate_index: int) -> str:
    if port_name == "VDD":
        return "VDD"
    if port_name == "GND":
        return "GND"
    if port_name in rule["internal_nets"]:
        return f"{gate['name']}.{port_name}" if gate.get("name") else f"gate_{gate_index}.{port_name}"
    connections = gate["connections"]
    if port_name not in connections:
        raise LogicMapError(
            f"gate {gate['name']!r} type {gate['type']!r} is missing port {port_name!r}; "
            "connect the dangling port or remove it from the gate library connect table."
        )
    return str(connections[port_name])


def _validate_drivers(gates: Sequence[Mapping[str, Any]], library: Mapping[str, Any]) -> None:
    drivers: dict[str, str] = {}
    for gate in gates:
        rule = library["gates"].get(gate["type"])
        if rule is None:
            continue
        for output_port in rule["output_ports"]:
            if output_port not in gate["connections"]:
                raise LogicMapError(
                    f"gate {gate['name']!r} type {gate['type']!r} is missing output port {output_port!r}; "
                    "connect the output in the netlist."
                )
            net_id = str(gate["connections"][output_port])
            if net_id in drivers:
                raise LogicMapError(
                    f"net {net_id!r} is driven by both {drivers[net_id]!r} and "
                    f"{gate['name'] + '.' + output_port!r}; split the net or fix the gate outputs."
                )
            drivers[net_id] = f"{gate['name']}.{output_port}"


def _split_terminal_ref(ref: Any, gate_type: str) -> tuple[str, str]:
    if not isinstance(ref, str) or "." not in ref:
        raise LogicMapError(
            f"gate {gate_type!r} terminal ref {ref!r} must be 'device_role.G/S/D'."
        )
    role, terminal = ref.split(".", 1)
    if not role or not terminal:
        raise LogicMapError(f"gate {gate_type!r} terminal ref {ref!r} has an empty side.")
    return role, terminal

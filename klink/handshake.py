"""Client/plugin version handshake — a pure, testable comparison.

The KLayout plugin reports its identity via ``hello()`` (server name, package
version, wire ``protocol``, KLayout version). ``evaluate_handshake`` compares
that against the client's expected protocol and, on a mismatch, returns an
INSTRUCTIVE ``next_action`` naming the exact fix — never a bare boolean.

This is the "errors are instructions" doctrine applied to version skew: a
stale plugin used to surface as a cryptic ``ERR_UNKNOWN_METHOD`` mid-call;
here the mismatch is named up front with the command to run.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def evaluate_handshake(
    client_version: str,
    client_protocol: int,
    server_info: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compare client vs. plugin and return a structured compatibility report.

    ``server_info`` is the dict returned by ``hello()`` (or ``{}`` / ``None``
    if the plugin could not be reached or returned no version).
    """
    server = server_info or {}
    server_protocol = server.get("protocol")
    result: Dict[str, Any] = {
        "client": "klink",
        "client_version": client_version,
        "client_protocol": client_protocol,
        "server": server.get("server"),
        "server_version": server.get("version"),
        "server_protocol": server_protocol,
        "klayout_version": server.get("klayout_version"),
        "compatible": server_protocol == client_protocol,
    }
    if result["compatible"]:
        return result

    if server_protocol is None:
        result["next_action"] = (
            "The KLayout plugin did not report a protocol version (hello "
            "returned no 'protocol'). It is missing or too old. Install/"
            "reinstall the klink plugin in KLayout (Tools > Manage Packages) "
            f"to one speaking protocol {client_protocol}, then reload it."
        )
    elif server_protocol < client_protocol:
        result["next_action"] = (
            f"The KLayout plugin speaks protocol {server_protocol}, older than "
            f"this klink client (protocol {client_protocol}). Update the klink "
            "plugin in KLayout (Tools > Manage Packages) and reload it."
        )
    else:
        result["next_action"] = (
            f"The KLayout plugin speaks protocol {server_protocol}, newer than "
            f"this klink client (protocol {client_protocol}). Upgrade the klink "
            "Python package: pip install -U klink."
        )
    return result

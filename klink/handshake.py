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

from typing import Any, Dict, Optional, Tuple


def _version_key(version: str) -> Tuple[int, ...]:
    """Leading numeric dotted prefix, '0.28.1rc1' -> (0, 28, 1)."""
    out = []
    for chunk in str(version).split("."):
        digits = ""
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


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
        # Same protocol is NECESSARY, not sufficient: a plugin many
        # releases behind still answers protocol 1 but is missing every
        # RPC added since — that staleness used to be invisible here
        # (a live setup ran plugin 0.3.8 under client 0.5.2, all green).
        sv = result["server_version"]
        if sv and str(sv) != str(client_version):
            if _version_key(sv) < _version_key(client_version):
                result["version_skew"] = "plugin_older"
                result["next_action"] = (
                    f"protocol-compatible, but the KLayout plugin is "
                    f"{sv} while this client is {client_version} — RPCs "
                    f"added since {sv} are missing on the plugin side. "
                    "Run `klink plugin install`, then restart KLayout "
                    "(or reload the klink macro)."
                )
            elif _version_key(sv) > _version_key(client_version):
                result["version_skew"] = "client_older"
                result["next_action"] = (
                    f"protocol-compatible, but this klink client is "
                    f"{client_version} while the KLayout plugin is {sv}. "
                    "Upgrade the Python package: pip install -U "
                    "klayout-klink."
                )
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

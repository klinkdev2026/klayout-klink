"""klink doctor — one-command preflight for a klink install.

    python -m klink.doctor [--host H] [--port P] [--gdsfactory] [--scan] [--json]

Checks, in order: this interpreter, the klink package, the Rust acceleration
kernels (informational), the `klayout` pip package version floor
(informational), the live plugin connection + version handshake, and
(optionally) gdsfactory. Every failing check carries an instructive ``fix`` so
the user knows the exact next step. Exit code is 0 when all checks pass, 1
otherwise.

``--scan`` probes a range of localhost ports for a live klink session instead
of (or in addition to) the single configured port — handy when you don't
remember which port KLayout is listening on.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Tuple

from ._meta import PROTOCOL_VERSION, __version__

_SCAN_PORT_LO = 8765
_SCAN_PORT_HI = 8799
_SCAN_PROBE_TIMEOUT = 0.3


def _parse_version_prefix(version: str) -> Tuple[int, ...]:
    """Parse the leading numeric dotted-version prefix, e.g. '0.28.1rc1' -> (0, 28, 1)."""
    parts: List[int] = []
    for chunk in version.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits == "":
            break
        parts.append(int(digits))
    return tuple(parts)


def _version_at_least(version: str, floor: Tuple[int, ...]) -> bool:
    return _parse_version_prefix(version) >= floor


def _check_kernels(add) -> None:
    present: List[str] = []
    missing: List[str] = []
    for mod in ("klink_boxmaze_rs", "klink_trackmaze_rs"):
        try:
            __import__(mod)
            present.append(mod)
        except Exception:
            missing.append(mod)

    if not missing:
        detail = f"present: {', '.join(present)}"
    elif not present:
        detail = "not installed (pure-Python fallback active, slower on large P&R)"
    else:
        detail = (
            f"present: {', '.join(present)}; missing: {', '.join(missing)} "
            "(pure-Python fallback active for those, slower on large P&R)"
        )
    add("kernels", True, detail)


def _check_klayout_pip(add) -> None:
    try:
        import klayout  # noqa: F401
    except Exception:
        add(
            "klayout_pip",
            True,
            "not installed (only needed for offline DB/LVS workflows)",
        )
        return

    import importlib.metadata as importlib_metadata

    try:
        version = importlib_metadata.version("klayout")
    except importlib_metadata.PackageNotFoundError:
        add("klayout_pip", True, "installed but version could not be determined")
        return

    floor = (0, 28)
    if _version_at_least(version, floor):
        add("klayout_pip", True, f"klayout {version}")
    else:
        add(
            "klayout_pip",
            False,
            f"klayout {version}",
            "pip install -U 'klayout>=0.28'",
        )


def _scan_for_sessions(
    host: str,
    lo: int = _SCAN_PORT_LO,
    hi: int = _SCAN_PORT_HI,
    probe_timeout: float = _SCAN_PROBE_TIMEOUT,
) -> List[Dict[str, Any]]:
    import socket

    from .client import KLinkClient
    from .errors import KLinkError

    found: List[Dict[str, Any]] = []
    for port in range(lo, hi + 1):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(probe_timeout)
        try:
            is_open = sock.connect_ex((host, port)) == 0
        except OSError:
            is_open = False
        finally:
            sock.close()

        if not is_open:
            continue

        try:
            client = KLinkClient(
                host=host,
                port=port,
                connect_timeout=probe_timeout,
                default_call_timeout=max(probe_timeout * 4, 1.0),
            )
            client.connect()
            try:
                handshake = client.handshake()
            finally:
                client.close()
            found.append({"port": port, "version": handshake.get("server_version")})
        except KLinkError:
            found.append({"port": port, "version": None})

    return found


def run_doctor(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    want_gdsfactory: bool = False,
    want_scan: bool = False,
) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, fix: str = "") -> None:
        entry: Dict[str, Any] = {"name": name, "ok": bool(ok), "detail": detail}
        if fix:
            entry["fix"] = fix
        checks.append(entry)

    add("interpreter", True, sys.executable)
    add("klink", True, f"klink {__version__} (protocol {PROTOCOL_VERSION})")
    _check_kernels(add)
    _check_klayout_pip(add)

    # Live plugin connection + version handshake.
    from .client import KLinkClient
    from .errors import KLinkError

    handshake: Optional[Dict[str, Any]] = None
    try:
        client = KLinkClient(host=host, port=port).connect()
        try:
            handshake = client.handshake()
        finally:
            client.close()
        add("plugin_connection", True, f"connected to {host}:{port}")
    except KLinkError as exc:
        fix = (
            f"Start KLayout with the klink plugin loaded and listening on "
            f"{host}:{port}."
        )
        if not want_scan:
            fix += " or run with --scan to look for sessions on other ports"
        add("plugin_connection", False, str(exc), fix)

    if handshake is not None:
        add(
            "protocol",
            handshake["compatible"],
            f"client protocol {handshake['client_protocol']} / "
            f"plugin protocol {handshake.get('server_protocol')}",
            handshake.get("next_action", ""),
        )
        if handshake.get("klayout_version"):
            add("klayout", True, f"KLayout {handshake['klayout_version']}")

    if want_scan:
        sessions = _scan_for_sessions(host)
        if sessions:
            parts = []
            for s in sessions:
                if s["version"]:
                    parts.append(f"{s['port']} (plugin {s['version']})")
                else:
                    parts.append(f"{s['port']} (open, no klink handshake)")
            detail = (
                f"{len(sessions)} session{'s' if len(sessions) != 1 else ''}: "
                + ", ".join(parts)
            )
            add("port_scan", True, detail)
        else:
            add(
                "port_scan",
                True,
                f"no listeners in {_SCAN_PORT_LO}-{_SCAN_PORT_HI}",
                "Start KLayout with the klink plugin loaded, then rerun the scan.",
            )

    if want_gdsfactory:
        try:
            import gdsfactory  # noqa: F401

            add("gdsfactory", True, getattr(gdsfactory, "__version__", "?"))
        except Exception as exc:  # pragma: no cover - import-time env detail
            add(
                "gdsfactory",
                False,
                str(exc),
                f"pip install gdsfactory into this interpreter ({sys.executable}).",
            )

    return {"ok": all(c["ok"] for c in checks), "checks": checks}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m klink.doctor",
        description="Preflight check for a klink install (interpreter, package, "
        "kernels, klayout pip floor, plugin connection + version handshake, "
        "gdsfactory).",
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument(
        "--gdsfactory",
        action="store_true",
        help="also check gdsfactory is importable in this interpreter",
    )
    ap.add_argument(
        "--scan",
        action="store_true",
        help=(
            f"scan 127.0.0.1 ports {_SCAN_PORT_LO}-{_SCAN_PORT_HI} for a live "
            "klink session instead of guessing the port"
        ),
    )
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = ap.parse_args(argv)

    report = run_doctor(
        args.host,
        args.port,
        want_gdsfactory=args.gdsfactory,
        want_scan=args.scan,
    )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for c in report["checks"]:
            print(f"[{'OK' if c['ok'] else 'XX'}] {c['name']}: {c['detail']}")
            if not c["ok"] and c.get("fix"):
                print(f"      fix: {c['fix']}")
        print("\nDOCTOR:", "all checks passed" if report["ok"] else "PROBLEMS FOUND")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

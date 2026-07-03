"""klink doctor — one-command preflight for a klink install.

    python -m klink.doctor [--host H] [--port P] [--gdsfactory] [--json]

Checks, in order: this interpreter, the klink package, the live plugin
connection + version handshake, and (optionally) gdsfactory. Every failing
check carries an instructive ``fix`` so the user knows the exact next step.
Exit code is 0 when all checks pass, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

from ._meta import PROTOCOL_VERSION, __version__


def run_doctor(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    want_gdsfactory: bool = False,
) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, fix: str = "") -> None:
        entry: Dict[str, Any] = {"name": name, "ok": bool(ok), "detail": detail}
        if fix and not ok:
            entry["fix"] = fix
        checks.append(entry)

    add("interpreter", True, sys.executable)
    add("klink", True, f"klink {__version__} (protocol {PROTOCOL_VERSION})")

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
        add(
            "plugin_connection",
            False,
            str(exc),
            f"Start KLayout with the klink plugin loaded and listening on "
            f"{host}:{port}.",
        )

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
        "plugin connection + version handshake, gdsfactory).",
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument(
        "--gdsfactory",
        action="store_true",
        help="also check gdsfactory is importable in this interpreter",
    )
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = ap.parse_args(argv)

    report = run_doctor(args.host, args.port, want_gdsfactory=args.gdsfactory)

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

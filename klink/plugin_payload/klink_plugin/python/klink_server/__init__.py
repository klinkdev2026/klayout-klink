"""
klink server package
====================

TCP RPC server embedded in KLayout.

Public entry point
------------------
    import klink_server
    klink_server.start()

Protocol
--------
See ``protocol.py`` for the NDJSON frame format, and ``meta.methods``
at runtime for the method catalogue.
"""

# Clear the registry first so that reloading this package in the
# Macro IDE does not fail with "duplicate method" errors when the
# method modules re-run their @method decorators.
from .registry import reset_for_reload as _reset_for_reload
_reset_for_reload()

from .txn import reset_for_reload as _reset_txn
_reset_txn()

try:
    from .server import start, stop, instance, mark_klive_target  # noqa: F401
except Exception as _server_import_error:  # pragma: no cover - non-KLayout unit tests
    def start() -> None:
        raise RuntimeError(f"klink_server requires KLayout pya runtime: {_server_import_error}")

    def stop() -> None:
        return None

    def instance():
        return None

    def mark_klive_target() -> dict:
        return {"ok": False, "error": str(_server_import_error)}
from .errors import ErrorCode, RpcError  # noqa: F401

__version__ = "0.1.0"

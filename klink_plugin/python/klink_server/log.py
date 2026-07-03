"""Central logging for klink_server.

Two sinks:

* KLayout console (stdout): INFO and above by default, formatted like the
  historical ``[klink...]`` prints. Level via ``KLINK_LOG_LEVEL``.
* Rotating file: always DEBUG, at ``%LOCALAPPDATA%/klink/logs/klink_server.log``
  (override directory with ``KLINK_LOG_DIR``). This is where intentionally
  swallowed exceptions become visible instead of vanishing.

Usage::

    from .log import get_logger
    _log = get_logger("recorder")
    _log.info("wrote %d actions", n)
    try:
        ...
    except Exception as exc:
        _log.debug("optional cleanup failed: %s", exc, exc_info=True)
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_ROOT_NAME = "klink"
_configured = False


def _log_dir() -> str:
    explicit = os.environ.get("KLINK_LOG_DIR")
    if explicit:
        return explicit
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "klink", "logs")


def _configure() -> None:
    global _configured
    if _configured:
        return
    _configured = True
    root = logging.getLogger(_ROOT_NAME)
    root.propagate = False
    # Handlers gate the levels; the logger itself passes everything through.
    root.setLevel(logging.DEBUG)
    if root.handlers:
        # Macro re-run in the same KLayout process: keep existing handlers.
        return

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    level_name = os.environ.get("KLINK_LOG_LEVEL", "INFO").upper()
    console.setLevel(getattr(logging, level_name, logging.INFO))
    root.addHandler(console)

    try:
        log_dir = _log_dir()
        os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, "klink_server.log"),
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        file_handler.setLevel(logging.DEBUG)
        root.addHandler(file_handler)
    except Exception as exc:  # file sink is best-effort; console still works
        root.warning("file logging unavailable: %s", exc)


def get_logger(name: str = "") -> logging.Logger:
    _configure()
    return logging.getLogger(_ROOT_NAME + ("." + name if name else ""))

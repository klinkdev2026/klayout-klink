"""Minimal in-process pending transfer state for the KLayout plugin.

Transfer intelligence lives outside the plugin. This module only stores an
already-reviewed package so the target KLayout window can expose a local PASTE
button.
"""

from __future__ import annotations

from copy import deepcopy


_PENDING: dict | None = None


def set_pending(package: dict) -> dict:
    global _PENDING
    if not isinstance(package, dict):
        raise ValueError("package must be an object")
    if package.get("version") != 1:
        raise ValueError("unsupported transfer package version")
    if package.get("copy_mode") not in {"flat_selection", "shallow_instance"}:
        raise ValueError("only flat_selection and shallow_instance pending transfers are supported")
    if not package.get("package_id"):
        raise ValueError("package_id is required")
    if not isinstance(package.get("items"), list) or not package["items"]:
        raise ValueError("package items must not be empty")
    _PENDING = deepcopy(package)
    _update_toolbar()
    return status()


def get_pending() -> dict | None:
    return deepcopy(_PENDING) if _PENDING is not None else None


def clear_pending() -> dict:
    global _PENDING
    old = _PENDING
    _PENDING = None
    _update_toolbar()
    return {"ok": True, "cleared": old is not None}


def status() -> dict:
    if _PENDING is None:
        return {"pending": False}
    review = _PENDING.get("review") or {}
    return {
        "pending": True,
        "package_id": _PENDING.get("package_id"),
        "source_session": _PENDING.get("source_session"),
        "target_session": _PENDING.get("target_session"),
        "target_cell": _PENDING.get("target_cell"),
        "copy_mode": _PENDING.get("copy_mode"),
        "shape_count": review.get("shape_count"),
        "layers": review.get("layers"),
        "target_layers": review.get("target_layers"),
        "warnings": review.get("warnings", []),
    }


def _update_toolbar() -> None:
    try:
        import klink_server

        action = getattr(klink_server, "_paste_transfer_action", None)
        if action is None:
            return
        info = status()
        if info.get("pending"):
            action.enabled = True
            action.icon_text = "PASTE"
            action.tool_tip = (
                f"Paste transfer {info.get('package_id')} from "
                f"{info.get('source_session')} ({info.get('shape_count')} shapes)"
            )
        else:
            action.enabled = False
            action.icon_text = "PASTE"
            action.tool_tip = "No pending klink transfer"
    except Exception:
        pass

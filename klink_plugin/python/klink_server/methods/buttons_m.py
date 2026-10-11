"""Custom toolbar button RPCs: re-sync the toolbar to the manifests on disk, list them."""

from __future__ import annotations

import pya

from ..registry import method
from ..errors import RpcError, ErrorCode
from .. import buttons


@method(
    "buttons.reload",
    description=(
        "Re-read the button manifests under <registry_root>/buttons/*.json and sync the "
        "KLayout toolbar to them: add new buttons, update changed texts in place, delete "
        "buttons whose manifest is gone. Returns installed/updated/removed/skipped ids and "
        "the current button list. Write manifests with `klink button add` first."
    ),
    params_schema={"type": "object"},
    tags=["buttons"],
)
def buttons_reload(params, ctx):
    app = pya.Application.instance()
    window = app.main_window() if app is not None else None
    if window is None:
        raise RpcError(ErrorCode.INTERNAL, "no KLayout main window (headless run); toolbar buttons "
                       "need the GUI", hint="start KLayout with its GUI and call buttons.reload again")
    return buttons.install_all(window.menu(), pya)


@method(
    "buttons.list",
    description="List the custom toolbar buttons currently installed, plus the manifest directory.",
    params_schema={"type": "object"},
    tags=["buttons"],
)
def buttons_list(params, ctx):
    return {"buttons": buttons.current(), "dir": str(buttons.buttons_dir())}

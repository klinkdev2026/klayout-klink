"""nanodevice.* local MCP tool handlers.

Handlers are functions ``(ctx, arguments)`` where ``ctx`` is the bridge; they
reach session-scoped clients through ``ctx._session_scoped_client`` and build
result envelopes with the shared helpers.
"""

from __future__ import annotations

from ..results import _error_result, _json_result
from . import local_tool


@local_tool(
    "nanodevice.hallbar",
    "Build, route, validate, and commit one Hall bar device in one call: "
    "generates geometry + Ports/Anchors from a spec, routes contacts to "
    "pads with klink's existing router (overlap validation ON, optional "
    "writefield walls), writes into a disposable cell, persists state. "
    "On problems it returns instructions (problems/next_action); a failed "
    "call changes nothing.",
    {
        "type": "object",
        "properties": {
            "cell": {"type": "string", "description": "Target cell (created/replaced; device-owned)."},
            "spec": {"type": "object", "description": "HallBarSpec fields. MUST include your process layers (device_layer/metal_layer/label_layer/route_layer 'L/D'); klink ships no process layers. See example_template/nanodevice/hallbar.py."},
            "writefield": {"type": "object", "description": "Writefield plan kwargs; omit to skip EBL walls."},
            "route_layer": {"type": "string", "description": "optional override for the routing layer 'L/D'; defaults to the spec's route_layer."},
            "spacing_um": {"type": "number", "default": 4.0},
            "dry_run": {"type": "boolean", "default": False},
            "session": {"type": "string", "description": "KLayout session id/label/alias (default: primary)."},
        },
        "additionalProperties": False,
    },
)
def _tool_nanodevice_hallbar(ctx, arguments: dict) -> dict:
    try:
        from ...domains.nanodevice import build_and_route_hallbar

        client, close_after = ctx._session_scoped_client(arguments.get("session"))
        try:
            kwargs = {
                k: arguments[k]
                for k in ("cell", "spec", "writefield", "route_layer",
                          "spacing_um", "dry_run")
                if k in arguments
            }
            return _json_result(build_and_route_hallbar(client, **kwargs))
        finally:
            if close_after:
                try:
                    client.close()
                except Exception:
                    pass
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "nanodevice.detect_commit",
    "Load (or detect) nanodevice flake traces and commit them as polygons "
    "into a live KLayout cell, in one call. Pass traces_path for a "
    "precomputed traces.json, or image (+pixel_size_um) to run detection "
    "(requires cv2/opencv + numpy in this interpreter). "
    "State persists on disk; failures return instructions and change "
    "nothing.",
    {
        "type": "object",
        "properties": {
            "cell": {"type": "string", "description": "Target cell (created/replaced; tool-owned)."},
            "traces_path": {"type": "string", "description": "Path to a precomputed traces.json."},
            "image": {"type": "string", "description": "Microscope image path for live detection."},
            "pixel_size_um": {"type": "number", "description": "your microscope's um/pixel calibration; REQUIRED with image (klink ships no default). Omit for traces_path mode."},
            "coordinate": {"type": "string", "enum": ["um", "gds"], "default": "um"},
            "dry_run": {"type": "boolean", "default": False},
            "session": {"type": "string", "description": "KLayout session id/label/alias (default: primary)."},
        },
        "additionalProperties": False,
    },
)
def _tool_nanodevice_detect_commit(ctx, arguments: dict) -> dict:
    try:
        from ...domains.nanodevice import detect_and_commit

        client, close_after = ctx._session_scoped_client(arguments.get("session"))
        try:
            kwargs = {
                k: arguments[k]
                for k in ("cell", "traces_path", "image", "pixel_size_um",
                          "coordinate", "dry_run")
                if k in arguments
            }
            return _json_result(detect_and_commit(client, **kwargs))
        finally:
            if close_after:
                try:
                    client.close()
                except Exception:
                    pass
    except Exception as exc:
        return _error_result(str(exc))

"""
recorder.* RPCs: external control over the Recorder singleton.

The toolbar button in klink.lym also goes through the same Recorder
instance, so state stays coherent regardless of who drives it.
"""

from __future__ import annotations

from ..errors import ErrorCode, RpcError
from ..recorder import instance as _rec
from ..registry import method


@method(
    "recorder.start",
    description=(
        "Start recording all layout-mutating events and translating "
        "them into a replayable Python script. Idempotent: calling "
        "while already recording returns the current status without "
        "starting a new session. Pass `output_path` to override the "
        "default location (~/Documents/klink_recordings/klink_record_"
        "YYYYMMDD_HHMMSS.py)."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "output_path": {"type": ["string", "null"]},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "recording": {"type": "boolean"},
            "started_at": {"type": ["number", "null"]},
            "elapsed": {"type": "number"},
            "event_count": {"type": "integer"},
            "action_count": {"type": "integer"},
            "output_path": {"type": ["string", "null"]},
        },
    },
    mutates=False,
    tags=["recorder"],
)
def recorder_start(params, ctx):
    path = params.get("output_path")
    return _rec().start(output_path=path if isinstance(path, str) else None)


@method(
    "recorder.stop",
    description=(
        "Stop the active recording and write the replayable script to "
        "disk. Returns the final stats plus `wrote` (bool) indicating "
        "whether the file was successfully written. Idempotent: "
        "calling when not recording returns `wrote=false` with the "
        "last known status."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "output_path": {"type": ["string", "null"]},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "recording": {"type": "boolean"},
            "wrote": {"type": "boolean"},
            "output_path": {"type": ["string", "null"]},
            "action_count": {"type": "integer"},
            "event_count": {"type": "integer"},
            "elapsed": {"type": "number"},
        },
    },
    mutates=False,
    tags=["recorder"],
)
def recorder_stop(params, ctx):
    path = params.get("output_path")
    return _rec().stop(output_path=path if isinstance(path, str) else None)


@method(
    "recorder.status",
    description=(
        "Return current recorder state (is it recording, how many "
        "events and translated actions so far, configured output "
        "path). Safe to call at any time."
    ),
    params_schema={"type": "object"},
    returns_schema={
        "type": "object",
        "properties": {
            "recording": {"type": "boolean"},
            "started_at": {"type": ["number", "null"]},
            "elapsed": {"type": "number"},
            "event_count": {"type": "integer"},
            "action_count": {"type": "integer"},
            "output_path": {"type": ["string", "null"]},
        },
    },
    mutates=False,
    tags=["recorder"],
)
def recorder_status(params, ctx):
    return _rec().status()

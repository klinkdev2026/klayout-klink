"""
Wire protocol helpers.

Frame format
------------
NDJSON: exactly one JSON object per line, terminated by '\\n', UTF-8 encoded.

Message shapes
--------------
request:
    {"id": <int|str>, "method": "<ns.verb>", "params": {...}}
response (ok):
    {"id": <int|str>, "ok": true,  "result": {...}}
response (err):
    {"id": <int|str>, "ok": false, "error": {"code": "...", "message": "...",
                                              "hint": "...", "data": {...}}}
event (server-initiated, no id):
    {"event": "<channel>", "data": {...}}
"""

from __future__ import annotations

import json


def encode_frame(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def decode_frame(line: str) -> dict:
    return json.loads(line)


def make_response_ok(req_id, result) -> dict:
    return {"id": req_id, "ok": True, "result": result}


def make_response_err(req_id, code: str, message: str, hint: str = "", data=None) -> dict:
    err = {"code": code, "message": message}
    if hint:
        err["hint"] = hint
    if data is not None:
        err["data"] = data
    return {"id": req_id, "ok": False, "error": err}


def make_event(event: str, data: dict) -> dict:
    return {"event": event, "data": data}

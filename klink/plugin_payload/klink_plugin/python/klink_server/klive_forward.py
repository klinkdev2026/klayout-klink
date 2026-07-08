"""klive-compatible request forwarding for multi-session klink."""

from __future__ import annotations

import itertools
import json
import socket
from typing import Any, Callable

from .session_registry import KLayoutSessionRegistry


class KliveCompatError(RuntimeError):
    pass


def choose_klive_target_session(registry: KLayoutSessionRegistry, *, stale_after_s: float = 15.0) -> dict[str, Any]:
    state = registry.read_state()
    sessions = _live_sessions(registry, stale_after_s=stale_after_s)
    by_id = {str(s.get("session_id")): s for s in sessions}

    target_id = state.get("klive_target_session")
    if target_id:
        target = by_id.get(str(target_id))
        if target is not None:
            return target
        raise KliveCompatError(f"configured klive-compatible target is unavailable or stale: {target_id}")

    if len(sessions) == 1:
        return sessions[0]
    if not sessions:
        raise KliveCompatError("no live KLayout/klink sessions are registered")
    raise KliveCompatError(
        "multiple live KLayout sessions are registered; set a klive-compatible target first"
    )


def forward_klive_request(
    request: dict[str, Any],
    *,
    registry: KLayoutSessionRegistry | None = None,
    rpc_call: Callable[..., dict[str, Any]] | None = None,
    timeout: float = 30.0,
    avoid_session_id: str | None = None,
) -> dict[str, Any]:
    gds_path = request.get("gds")
    if not gds_path:
        raise KliveCompatError("missing required field: gds")

    registry = registry or KLayoutSessionRegistry()
    session = choose_klive_target_session(registry)
    if avoid_session_id and session.get("session_id") == avoid_session_id:
        raise KliveCompatError(f"target session is the local klive-compatible server: {avoid_session_id}")

    host = str(session.get("host") or "127.0.0.1")
    port = int(session.get("rpc_port") or session.get("port"))
    params = {
        "path": str(gds_path),
        "mode": "new",
        "keep_position": bool(request.get("keep_position", True)),
    }
    if request.get("technology") is not None:
        params["technology"] = request.get("technology")

    call = rpc_call or _rpc_call
    result = call(host=host, port=port, method="layout.show_file", params=params, timeout=timeout)

    response = {
        "version": "0.4.1",
        "type": result.get("type", "open") if isinstance(result, dict) else "open",
        "file": str(gds_path),
        "handled_by": "klink",
        "target_session": session.get("session_id"),
        "target_port": port,
    }
    if isinstance(result, dict) and result.get("loaded"):
        response["file"] = result["loaded"]
    return response


def _live_sessions(registry: KLayoutSessionRegistry, *, stale_after_s: float) -> list[dict[str, Any]]:
    import time

    now = time.time()
    sessions = []
    for path in sorted(registry.sessions_dir.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        try:
            last_seen = float(record.get("last_seen"))
        except (TypeError, ValueError):
            continue
        if now - last_seen <= stale_after_s:
            sessions.append(record)
    return sessions


_IDS = itertools.count(1)


def _rpc_call(*, host: str, port: int, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    req_id = next(_IDS)
    frame = json.dumps({"id": req_id, "method": method, "params": params}, ensure_ascii=False) + "\n"
    with socket.create_connection((host, port), timeout=min(float(timeout), 5.0)) as sock:
        sock.settimeout(float(timeout))
        sock.sendall(frame.encode("utf-8"))
        file = sock.makefile("rb")
        raw = file.readline()
    if not raw:
        raise KliveCompatError(f"empty RPC response from {host}:{port}")
    try:
        response = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise KliveCompatError(f"invalid RPC response from {host}:{port}: {exc}") from exc
    if response.get("ok"):
        result = response.get("result")
        return result if isinstance(result, dict) else {}
    error = response.get("error") or {}
    message = error.get("message") if isinstance(error, dict) else None
    raise KliveCompatError(message or f"RPC {method} failed on {host}:{port}")

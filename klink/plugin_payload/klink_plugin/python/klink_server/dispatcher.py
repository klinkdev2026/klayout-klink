"""
Request dispatcher.

Runs in the Qt main thread. For M1 every method is synchronous.
Future long-running methods will hand the work off to the Job manager
(M5) and immediately respond with a job_id so the connection stays
responsive.
"""

from __future__ import annotations

import traceback

from .registry import get as get_method
from .errors import RpcError, ErrorCode
from .protocol import make_response_ok, make_response_err
from .ctx import RequestContext

_TRACE_SEQ = [0]


def _next_trace_id() -> str:
    _TRACE_SEQ[0] += 1
    return f"t{_TRACE_SEQ[0]}"


# Pointer to the currently-executing RPC (if any), set just before
# handler invocation and cleared afterwards. Consumers like SignalHub
# read this at emit time to attach a `caused_by` field to events, so
# LLM agents can correlate an event back to the RPC that triggered it.
#
# Safe as a plain module global: KLayout serves everything on the Qt
# main thread (QTcpServer + QTcpSocket signals), so there is no
# multi-threaded contention. A list is used as a stack just in case
# a handler ever invokes dispatch re-entrantly - today none do.
_REQUEST_STACK: list = []


def current_request() -> dict | None:
    """Return {request_id, method, trace_id, conn_id} for the RPC
    currently being dispatched, or None if no RPC is in flight."""
    if not _REQUEST_STACK:
        return None
    return _REQUEST_STACK[-1]


class Dispatcher:
    def __init__(self):
        self.log_calls = True

    def dispatch(self, req_id, method: str, params: dict, conn) -> None:
        spec = get_method(method)
        if spec is None:
            conn.send(make_response_err(
                req_id, ErrorCode.UNKNOWN_METHOD,
                f"unknown method: {method}",
                hint="call 'meta.methods' to list available methods",
            ))
            return

        if not isinstance(params, dict):
            conn.send(make_response_err(
                req_id, ErrorCode.BAD_PARAMS,
                "params must be an object",
                hint="pass params as a JSON object, e.g. {\"verbosity\":\"normal\"}",
            ))
            return

        ctx = RequestContext(
            request_id=req_id,
            method=method,
            params=params,
            conn_id=conn.conn_id,
            trace_id=_next_trace_id(),
            emit_event=conn.send_event,
        )

        if self.log_calls:
            print(f"[klink] conn#{conn.conn_id} {ctx.trace_id} -> {method}")

        cause = {
            "request_id": req_id,
            "method": method,
            "trace_id": ctx.trace_id,
            "conn_id": conn.conn_id,
        }
        _REQUEST_STACK.append(cause)
        try:
            result = spec.handler(params, ctx)
            conn.send(make_response_ok(req_id, result))
        except RpcError as e:
            conn.send(make_response_err(
                req_id, e.code, e.message, hint=e.hint, data=e.data,
            ))
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[klink] {method} failed: {tb}")
            conn.send(make_response_err(
                req_id, ErrorCode.INTERNAL,
                f"{type(e).__name__}: {e}",
                hint="check the KLayout console for the full stack trace",
                data={"traceback_tail": tb.strip().splitlines()[-5:]},
            ))
        finally:
            try:
                _REQUEST_STACK.pop()
            except IndexError:
                pass

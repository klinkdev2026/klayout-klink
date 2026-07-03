"""
Request context passed to every method handler.

Centralised so that future features (cancellation, tracing, LLM-friendly
logging hooks, transaction state, job manager reference) can be added
here without touching individual method signatures.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class RequestContext:
    request_id: Any
    method: str
    params: dict
    conn_id: int
    trace_id: str
    started_at: float = field(default_factory=time.monotonic)

    # Cooperative cancellation signal. Long-running method handlers
    # should periodically check `cancel_flag.is_set()` and raise
    # `RpcError(ErrorCode.CANCELLED, ...)` if True.
    cancel_flag: threading.Event = field(default_factory=threading.Event)

    # Populated by the dispatcher once a response has been sent.
    responded: bool = False

    # Future: reference to transaction manager, job manager, emit-event fn
    emit_event: Optional[Any] = None

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.started_at) * 1000.0

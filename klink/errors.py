"""Client-side exception hierarchy."""

from __future__ import annotations


class KLinkError(Exception):
    """Base class for all klink client errors."""


class KLinkTransportError(KLinkError):
    """TCP-level problem: connection refused, timeout, broken pipe, ..."""


class KLinkServerError(KLinkError):
    """The server returned a structured error for an RPC call."""

    def __init__(self, code: str, message: str, hint: str = "", data=None):
        parts = [f"[{code}] {message}"]
        if hint:
            parts.append(f"-- {hint}")
        super().__init__(" ".join(parts))
        self.code = code
        self.message = message
        self.hint = hint
        self.data = data

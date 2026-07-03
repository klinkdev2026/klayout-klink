"""
Typed data models mirroring common server response shapes.

M1 keeps this minimal (just ServerInfo). More DTOs will arrive as the
method catalogue grows in later milestones.

These are pure dataclasses: the client returns plain dicts by default so
callers who don't want typing stay unaffected. Call
`ServerInfo.from_dict(client.hello())` if you want a typed wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class ServerInfo:
    server: str
    version: str
    protocol: int
    klayout_version: str
    capabilities: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "ServerInfo":
        return cls(
            server=d.get("server", ""),
            version=d.get("version", ""),
            protocol=int(d.get("protocol", 0)),
            klayout_version=d.get("klayout_version", ""),
            capabilities=list(d.get("capabilities", [])),
        )

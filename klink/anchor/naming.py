"""Anchor naming helpers."""

from __future__ import annotations


def auto_id(existing_ids: set[str], prefix: str = "A", index: int = 0) -> str:
    i = int(index)
    while True:
        candidate = "%s%d" % (prefix, i)
        if candidate not in existing_ids:
            return candidate
        i += 1

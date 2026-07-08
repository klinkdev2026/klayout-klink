"""
Event broadcaster.

In M1 this provides only the subscribe/unsubscribe bookkeeping so the
protocol surface is stable. M3 will hook the real pya signals
(`view.on_selection_changed`, `view.on_cellview_changed`, ...) and call
`emit(channel, data)` from those callbacks (which run in the Qt main
thread).

Per-connection event queueing (so a slow client cannot block the main
thread when a pya signal fires) is done inside `ConnState`, not here.
This class only decides *who* receives *what*.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Set


VALID_CHANNELS = {
    "selection_sent",
    "selection_changed",
    "cellview_changed",
    "viewport_changed",
    "layer_list_changed",
    "shapes_changed",
    "cells_changed",
    "instances_changed",
    "job_progress",
    "job_done",
}


class EventBroadcaster:
    def __init__(self, server):
        self.server = server
        self._subs = defaultdict(set)
        self.emit_counts = defaultdict(int)
        self.delivered_counts = defaultdict(int)

    def subscribe(self, conn, channels: Iterable[str]) -> Set[str]:
        accepted = set()
        for ch in channels:
            if ch not in VALID_CHANNELS:
                continue
            self._subs[ch].add(conn)
            conn.subscriptions.add(ch)
            accepted.add(ch)
        return accepted

    def unsubscribe(self, conn, channels: Iterable[str]) -> None:
        for ch in channels:
            self._subs[ch].discard(conn)
            conn.subscriptions.discard(ch)

    def unsubscribe_all(self, conn) -> None:
        for ch in list(conn.subscriptions):
            self._subs[ch].discard(conn)
        conn.subscriptions.clear()

    def emit(self, channel: str, data: dict) -> int:
        """Fan out an event to all subscribers. Non-blocking: each
        connection queues the event and drains it via QTimer.

        Returns the number of subscriber connections that accepted the event.
        This lets explicit UX actions such as Selection Send avoid reporting
        success when no external MCP/runtime is listening.
        """
        delivered = 0
        self.emit_counts[channel] += 1
        for conn in list(self._subs.get(channel, [])):
            conn.send_event(channel, data)
            delivered += 1
        self.delivered_counts[channel] += delivered
        return delivered

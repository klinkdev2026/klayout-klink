"""Explicit layout-context capture helpers.

The plugin does not store interaction memory. It only turns a user action
("Send Selection") into a structured event for the external MCP/runtime.
"""

from __future__ import annotations


def send_current_selection(source: str = "toolbar", max_items: int = 50) -> dict:
    from .server import instance as _srv_instance
    from .signals import _summarise_selection

    srv = _srv_instance()
    if srv is None:
        return {"ok": False, "status": "failed", "message": "server unavailable"}

    try:
        view = srv.signals._view
        if view is None:
            try:
                import pya
                mw = pya.Application.instance().main_window()
                view = mw.current_view() if mw is not None else None
            except Exception:
                view = None
        if view is None:
            return {"ok": False, "status": "failed", "message": "no active view"}

        data = _summarise_selection(view, max_items=max_items)
        count = int(data.get("count") or 0)
        data["capture_reason"] = "selection_sent"
        data["source"] = source
        data["klayout_session_id"] = getattr(srv, "session_id", None)
        data["klayout_rpc_port"] = getattr(srv, "port", None)
        try:
            record = srv.session_record()
            data["klayout_pid"] = record.get("pid")
            data["layout_path"] = record.get("layout_path")
            data["active_cell"] = record.get("active_cell")
        except Exception:
            pass

        if count <= 0:
            return {
                "ok": False,
                "status": "empty",
                "message": "no selection",
                "count": 0,
            }

        # Durability first: journal the SEND unconditionally, then push the
        # live event to whoever is currently listening. A SEND is OK once
        # it is journaled, even with zero listeners — consumers catch up
        # from the journal (see send_journal.py).
        send_seq = None
        journal_error = None
        try:
            journal = getattr(srv, "send_journal", None)
            if journal is None:
                from .send_journal import SendJournal

                journal = SendJournal(getattr(srv, "session_id", None) or "unknown")
                srv.send_journal = journal
            send_seq = journal.append(data)
            data["send_seq"] = send_seq
        except Exception as exc:
            journal_error = str(exc)

        delivered = srv.events.emit("selection_sent", data)
        if send_seq is None and delivered <= 0:
            return {
                "ok": False,
                "status": "lost",
                "message": "journal write failed and no listener subscribed: "
                           + (journal_error or "unknown error"),
                "count": count,
                "truncated": bool(data.get("truncated")),
            }
        status = "sent" if delivered > 0 else "journaled_no_listener"
        return {
            "ok": True,
            "status": status,
            "message": f"sent {count} selected object(s)"
                       + ("" if delivered > 0 else "; no live listener, journaled for catch-up"),
            "count": count,
            "truncated": bool(data.get("truncated")),
            "delivered": delivered,
            "send_seq": send_seq,
            "journaled": send_seq is not None,
            "journal_error": journal_error,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "failed",
            "message": str(exc),
        }

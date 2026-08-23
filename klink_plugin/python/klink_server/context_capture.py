"""Explicit layout-context capture helpers.

The plugin does not store interaction memory. It only turns a user action
("Send Selection") into a structured event for the external MCP/runtime.
"""

from __future__ import annotations


def _summarise_selected_rulers(view, max_items: int) -> dict:
    """Selected rulers (annotations) as plain JSON, ascending by id.

    Rulers live in the view, not the layout, so `each_object_selected`
    never yields them; without this a SEND of "the ruler I drew" is
    invisible to the interaction context. Ascending id order is creation
    order among live rulers (KLayout allocates max(live id)+1, verified
    empirically) -- but an id can be reused after the newest ruler is
    deleted, which is why the summary carries points/bbox too.
    """
    rulers: list = []
    n = 0
    truncated = False
    try:
        from .methods.annotation_m import _ruler_dict

        selected = list(view.each_annotation_selected())
        ids = set()
        for a in selected:
            try:
                ids.add(int(a.id()))
            except Exception:
                pass
        selected.sort(key=lambda a: int(a.id()))
        for a in selected:
            n += 1
            if len(rulers) >= max_items:
                truncated = True
                continue
            try:
                rulers.append(_ruler_dict(a, ids))
            except Exception:
                pass
    except Exception:
        pass
    return {"ruler_count": n, "rulers": rulers, "rulers_truncated": truncated}


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
        # `count` keeps meaning layout OBJECTS (older consumers read it);
        # rulers are reported alongside under their own keys.
        data.update(_summarise_selected_rulers(view, max_items=max_items))
        ruler_count = int(data.get("ruler_count") or 0)
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

        if count <= 0 and ruler_count <= 0:
            return {
                "ok": False,
                "status": "empty",
                "message": "no selection (no layout objects and no rulers selected)",
                "count": 0,
                "ruler_count": 0,
            }
        sent_msg = "sent %d selected object(s)" % count
        if ruler_count:
            sent_msg += " + %d ruler(s)" % ruler_count

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
                "ruler_count": ruler_count,
                "truncated": bool(data.get("truncated")),
            }
        status = "sent" if delivered > 0 else "journaled_no_listener"
        return {
            "ok": True,
            "status": status,
            "message": sent_msg
                       + ("" if delivered > 0 else "; no live listener, journaled for catch-up"),
            "count": count,
            "ruler_count": ruler_count,
            "truncated": bool(data.get("truncated")),
            "rulers_truncated": bool(data.get("rulers_truncated")),
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

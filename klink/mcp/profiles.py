"""
Profile-based method filtering for klink-mcp.

Two ORTHOGONAL axes select which tools a deployment exposes:

* INTENT (capability) — ``read`` / ``write`` / ``verify`` / ``escape`` (+
  ``all``). Cross-domain: it selects plugin RPCs by what they DO, so the four
  capabilities together span every area. This is what the DEFAULT is built
  from (``read,write,verify,escape``), NOT a list of domains — local tools are
  always included and read/write already cover the read/write RPCs of every
  area.
* DOMAIN (area) — the catalog domain tokens (``connection_and_view``, …,
  ``device_photonics``, ``routing_backends``, …). This axis is for NAVIGATION
  (klink.find_tools) and for narrowing a deployment to one area
  (``--profile device_photonics``); see ``klink/mcp/catalog.py``.

A method is included if it matches ANY requested intent OR falls in any
requested domain. Local tools are always included for intent profiles; a
domain-ONLY profile also restricts local tools (see ``registry.list_tools``).

Back-compat aliases for the historical intent names (so existing
``--profile basic,draw,drc,advanced`` configs keep working):

    basic -> read, draw -> write, advanced -> escape, drc -> verify

``drc`` was never an intent in the read/write sense — it aliases to the
``verify`` capability (run DRC/LVS checks), which covers ``drc.run`` + ``lvs.run``.

Methods whose tags match no rule are silently dropped. This is intentional for
the bridge-wrapped session/transfer plugin RPCs (``session.label_set`` etc.),
which are surfaced via the ``klink.session_*`` / ``klink.transfer_*`` local
tools instead.
"""

from __future__ import annotations

from typing import List

from .catalog import domain_for, domain_tokens

#: Capability profiles (the intent axis), distinct from domain selectors.
INTENT_PROFILES = {"read", "write", "verify", "escape", "all"}

#: Historical intent names -> their modern equivalent. ``drc`` aliases to the
#: ``verify`` capability (run DRC/LVS), not to a domain.
PROFILE_ALIASES = {
    "basic": "read",
    "draw": "write",
    "advanced": "escape",
    "drc": "verify",
}


def normalize_profiles(profiles: List[str]) -> List[str]:
    """Map legacy profile names to their modern equivalents (idempotent)."""
    return [PROFILE_ALIASES.get(p, p) for p in profiles]


def filter_methods(specs: List[dict], profiles: List[str]) -> List[dict]:
    """Return the subset of ``specs`` matching the requested profiles.

    A method is included if it matches ANY requested intent profile OR falls in
    any requested domain selector. ``all`` returns everything.

    Intent rules
    ------------
    read    ``tags`` contains ``"meta"`` or ``"read"`` (plus view-only
            navigation, recorder, and selection.send_context helpers).
    write   ``tags`` contains ``"write"`` or ``"undo"`` (plus selection.clear /
            selection.set_box); never DRC/LVS.
    verify  ``name`` starts with ``"drc."`` or ``"lvs."`` (run checks).
    escape  ``name`` starts with ``"exec."`` or ``"events."``.
    """
    profiles = normalize_profiles(profiles)
    if "all" in profiles:
        return list(specs)

    want_read = "read" in profiles
    want_write = "write" in profiles
    want_verify = "verify" in profiles
    want_escape = "escape" in profiles
    requested_domains = {p for p in profiles if p in set(domain_tokens())}

    out: List[dict] = []
    seen: set = set()

    for m in specs:
        name = m["name"]
        tags = set(m.get("tags") or [])

        matched = False

        if want_read and (
            tags & {"meta", "read"}
            or name in (
                "view.zoom_fit", "view.zoom_box", "view.show_cell",
                "view.hier_levels",
                "view.highlight", "view.highlight_clear",
                "layer.set_visible", "layer.set_style",
                "layer.load_lyp", "layer.save_lyp",
                "recorder.start", "recorder.stop", "recorder.status",
                "selection.clear", "selection.set_box",
                "selection.send_context",
            )
        ):
            matched = True

        if want_write and not matched and not (
            name.startswith("drc.") or name.startswith("lvs.")
        ) and (
            tags & {"write", "undo"}
            or name in ("selection.clear", "selection.set_box", "view.new_tab")
        ):
            matched = True

        if want_verify and not matched and (
            name.startswith("drc.") or name.startswith("lvs.")
        ):
            matched = True

        if want_escape and not matched and (
            name.startswith("exec.") or name.startswith("events.")
        ):
            matched = True

        if not matched and requested_domains and domain_for(name) in requested_domains:
            matched = True

        if matched and name not in seen:
            seen.add(name)
            out.append(m)

    return out

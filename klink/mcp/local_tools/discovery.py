"""klink.find_tools — domain-aware tool discovery (progressive disclosure).

tools/list already advertises EVERY tool (so any client can call them); this is
navigation + ON-DEMAND detailed usage, never a gate. Three modes:

* no args            -> the domain INDEX (token, title, summary, live tool count)
* domain=<token>     -> that domain's tools + its detailed `usage` text
* query=<keywords>   -> tools ranked by keyword (optionally within one domain),
                        plus the detailed usage of the domains they fall in

The detailed per-domain usage lives in klink/mcp/catalog.py.
"""

from __future__ import annotations

from ..catalog import DOMAINS, UNCATEGORIZED, domain_for, domain_tokens, ext_domains


def _domain_meta(token: str) -> dict:
    """Built-in or extension-contributed domain metadata."""
    meta = DOMAINS.get(token) or ext_domains().get(token)
    if meta is not None:
        return meta
    return {"title": token, "summary": "", "usage": ""}
from ..results import _error_result, _json_result
from . import local_tool

_MAX_TOOLS = 60


@local_tool(
    "klink.find_tools",
    "Discover klink tools by domain or keyword. Call with NO args for the domain "
    "index; domain=<token> for that domain's tools + detailed usage; "
    "query=<keywords> to rank matching tools (optionally within one domain). "
    "tools/list already contains every tool and they are all callable — this is "
    "for NAVIGATION and on-demand detailed usage, not a gate. Use it whenever you "
    "are unsure which tool to use.",
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keywords to match against tool name/description/usage.",
            },
            "domain": {
                "type": "string",
                "enum": domain_tokens(),
                "description": "Restrict to one domain (also returns its detailed usage).",
            },
        },
        "additionalProperties": False,
    },
)
def _tool_klink_find_tools(ctx, arguments: dict) -> dict:
    try:
        query = str(arguments.get("query") or "").strip().lower()
        domain = str(arguments.get("domain") or "").strip()
        if domain and domain not in domain_tokens():
            return _error_result(
                f"unknown domain {domain!r}; valid domains: {', '.join(domain_tokens())}"
            )

        # The set the agent can actually call right now (active profile).
        tools = ctx.list_tools()["tools"]
        by_domain: dict[str, list] = {}
        for t in tools:
            by_domain.setdefault(domain_for(t["name"]), []).append(t)

        # Mode 1: index (no query, no domain).
        if not query and not domain:
            index = [
                {
                    "domain": tok,
                    "title": _domain_meta(tok)["title"],
                    "summary": _domain_meta(tok)["summary"],
                    "tool_count": len(by_domain.get(tok, [])),
                }
                for tok in domain_tokens()
            ]
            return _json_result({
                "mode": "index",
                "total_tools": len(tools),
                "domains": index,
                "hint": "call klink.find_tools with domain=<token> for that "
                        "domain's tools + usage, or query=<keywords> to search.",
            })

        # Candidate set: one domain, or everything.
        candidates = by_domain.get(domain, []) if domain else list(tools)

        # Rank by query if given (match name + description only — the domain
        # usage is returned separately and must not pollute tool matching);
        # otherwise keep candidate order.
        if query:
            terms = query.split()
            scored = []
            for t in candidates:
                hay = (t["name"] + " " + str(t.get("description", ""))).lower()
                score = sum(hay.count(term) for term in terms)
                if score > 0:
                    scored.append((score, t["name"], t))
            scored.sort(key=lambda x: (-x[0], x[1]))
            matched = [t for _, _, t in scored]
        else:
            matched = candidates

        result_tools = [
            {
                "name": t["name"],
                "domain": domain_for(t["name"]),
                "description": str(t.get("description", "")),
            }
            for t in matched[:_MAX_TOOLS]
        ]

        out: dict = {
            "mode": "search",
            "query": query or None,
            "domain": domain or None,
            "match_count": len(matched),
            "returned": len(result_tools),
            "tools": result_tools,
        }
        if domain:
            # Focused on one domain -> return its detailed, skill-like usage.
            out["domain_usage"] = {
                "domain": domain,
                "title": _domain_meta(domain)["title"],
                "usage": _domain_meta(domain)["usage"],
            }
        else:
            # Broad query -> just name the domains represented; the agent drills
            # in with domain=<token> to get the detailed usage on demand.
            represented = [tok for tok in domain_tokens() if tok in {r["domain"] for r in result_tools}]
            out["domains_represented"] = represented
            out["hint"] = "call klink.find_tools with domain=<token> for that domain's detailed usage."
        return _json_result(out)
    except Exception as exc:
        return _error_result(str(exc))

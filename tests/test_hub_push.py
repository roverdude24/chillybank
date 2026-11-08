"""ChillyBank pushes the month to the family TV hub in the hub's /api/finance shape."""

from __future__ import annotations

import json

import httpx
import pytest

from backend.core.user_time import user_today
from backend.services import hub_push
from backend.services.accounts import create_account
from backend.services.ledger import create_transaction


@pytest.mark.parametrize(
    ("note", "due", "minimum", "estimate"),
    [
        ("Sao kê 26/10 — hạn trả 15/11/2026 (tối thiểu 153.974đ)", "2026-11-15", 153_974, False),
        ("ước tính, hạn thanh toán 03/11", "2026-11-03", None, True),  # no year: this year, overdue stays overdue
        ("hạn trả 31/02/2026", None, None, False),
        ("trả tự động từ MB", None, None, False),
    ],
)
def test_card_due_date_and_minimum_come_from_the_note(note, due, minimum, estimate) -> None:
    from datetime import date

    info = hub_push._card_info(note, date(2026, 11, 8))
    assert (info["dueDate"], info["minPayment"], info["estimate"]) == (due, minimum, estimate)


def _book() -> None:
    from backend.db.profile_store import ProfileStore

    ProfileStore().set_raw({"base_currency": "VND"})
    today = user_today().isoformat()
    bank = create_account(
        {"name": "Vietcombank", "type": "cash", "currency": "VND", "initial_balance": 20_000_000, "initial_balance_date": today}
    )
    create_account(
        {"name": "Thẻ TPBank", "type": "credit", "currency": "VND", "initial_balance": -3_000_000, "initial_balance_date": today}
    )
    create_transaction(
        {"date": today, "kind": "expense", "description": "Bách Hóa Xanh", "from_account_id": bank["id"], "from_amount": 150_000}
    )
    create_transaction(
        {"date": today, "kind": "income", "description": "Lương", "to_account_id": bank["id"], "to_amount": 30_000_000}
    )


def test_snapshot_has_the_hub_shape(db_isolated) -> None:
    _book()
    snap = hub_push.build_snapshot()
    assert snap["month"]["label"] == user_today().strftime("%m/%Y")
    assert snap["month"]["spend"] == pytest.approx(150_000)
    assert snap["month"]["incomeActual"] == pytest.approx(30_000_000)
    assert snap["month"]["salaryRecorded"] is True
    assert snap["creditCardDebt"] == pytest.approx(3_000_000)
    assert snap["cards"][0]["name"] == "Thẻ TPBank"
    assert [r["title"] for r in snap["recent"]][:2] in (["Lương", "Bách Hóa Xanh"], ["Bách Hóa Xanh", "Lương"])
    assert {"allocated", "remaining"} <= snap["fund"].keys()
    assert all({"name", "remaining", "status"} <= e.keys() for e in snap["envelopes"])


async def test_push_is_off_without_config(db_isolated, monkeypatch) -> None:
    monkeypatch.delenv("CHILLY_HUB_URL", raising=False)
    assert await hub_push.push_to_hub() is False


async def test_push_sends_the_snapshot_with_the_push_key(db_isolated, monkeypatch) -> None:
    seen: list[httpx.Request] = []
    real = httpx.AsyncClient
    monkeypatch.setattr(
        hub_push.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(lambda r: (seen.append(r), httpx.Response(200))[1]), **kw),
    )
    monkeypatch.setenv("CHILLY_HUB_URL", "https://hub.example/")
    monkeypatch.setenv("CHILLY_HUB_PUSH_KEY", "push-only")
    assert await hub_push.push_to_hub() is True
    assert str(seen[0].url) == "https://hub.example/api/finance/snapshot"
    assert seen[0].headers["authorization"] == "Bearer push-only"
    assert json.loads(seen[0].content)["source"] == "chillybank"

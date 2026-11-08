"""Push this month's money snapshot to the family TV hub (ChillyBank).

The hub's Tài chính screen used to read Notion. ChillyBank is now the book, so it sends the
hub a small snapshot in the hub's own ``/api/finance`` shape. Outbound only: the hub holds a
key that can write ONE thing (this snapshot) and nothing that can read or change the book.

Off unless ``CHILLY_HUB_URL`` and ``CHILLY_HUB_PUSH_KEY`` are both set.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import httpx

from backend.core.user_time import user_today
from backend.services import ledger as svc
from backend.services import planning as planning_service

logger = logging.getLogger(__name__)


def _amount(t: dict) -> float:
    return abs(float(t.get("base_amount") if t.get("base_amount") is not None else t.get("amount") or 0))


def _envelopes(month: dict, txns: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """The hub's envelope model is one cumulative pot (Notion: Tổng phân bổ − Đã chi = Còn lại), not
    ChillyBank's monthly rollover, where an overspent month resets to zero and card purchases move
    money into a card-payment envelope. So spend is summed over the whole ledger per envelope, and
    card-payment envelopes are left out."""
    spent_by_category: dict[str, float] = {}
    for t in txns:
        if t.get("category") and t.get("kind") in ("expense", "income"):
            sign = 1 if t["kind"] == "expense" else -1
            spent_by_category[t["category"]] = spent_by_category.get(t["category"], 0.0) + sign * _amount(t)
    rows, by_category = [], {}
    for group in month.get("groups") or []:
        for c in group.get("categories") or []:
            for cid in c.get("category_ids") or []:
                by_category[cid] = c.get("label") or ""
            if c.get("is_payment_envelope"):
                continue
            allocated = float(c.get("assigned_to_date") or 0)
            spent = sum(spent_by_category.get(cid, 0.0) for cid in c.get("category_ids") or [])
            remaining = allocated - spent
            if not (allocated or spent):
                continue  # an unused envelope (a fresh book's starters) would only clutter the TV list
            # shortcut: no "fast" (spending ahead of plan); Notion's pace formula is not readable, add it once ChillyBank has a plan horizon.
            status = "neg" if remaining < 0 else "done" if allocated and remaining == 0 else "ok" if allocated else None
            rows.append(
                {
                    "id": c.get("envelope_id") or c.get("key"),
                    "name": c.get("label") or "",
                    "group": group.get("name"),
                    "allocated": allocated,
                    "spent": spent,
                    "remaining": remaining,
                    "usedPct": round(spent / allocated * 100) if allocated else 0,
                    "status": status,
                    "monthsLeft": None,
                    "targetMonthly": (c.get("target") or {}).get("amount") if isinstance(c.get("target"), dict) else None,
                    "note": "",
                }
            )
    return rows, by_category


def build_snapshot() -> dict:
    today = user_today()
    ym = today.strftime("%Y-%m")
    month = planning_service.budget_month(ym)
    txns = svc.transactions.all()
    envelopes, envelope_of = _envelopes(month, txns)

    accts = svc.accounts.query_accounts()
    accounts = svc.accounts_with_balances(accts, txns, idx=svc.opening_index(txns, accts))
    acct_rows = [
        {
            "name": a.get("name") or "",
            "type": "Thẻ tín dụng" if a.get("type") == "credit" else a.get("type"),
            "balance": float(a.get("balance_base") if a.get("balance_base") is not None else a.get("balance") or 0),
        }
        for a in accounts
    ]
    cards = sorted(
        (
            {"name": a["name"], "debt": -a["balance"], "dueDate": None, "minPayment": None, "estimate": False}
            for a in acct_rows
            if a["type"] == "Thẻ tín dụng"
        ),
        key=lambda c: -c["debt"],
    )

    in_month = [t for t in txns if str(t.get("date") or "").startswith(ym)]
    income = sum(_amount(t) for t in in_month if t.get("kind") == "income")
    spend = sum(_amount(t) for t in in_month if t.get("kind") == "expense")
    spenders = [t for t in txns if t.get("kind") in ("expense", "income")]
    recent = [
        {
            "date": str(t.get("date") or "")[:10],
            "title": t.get("description") or t.get("memo") or "",
            "amount": _amount(t) * (-1 if t.get("kind") == "expense" else 1),
            "kind": "Chi" if t.get("kind") == "expense" else "Thu",
            "envelope": envelope_of.get(t.get("category") or ""),
        }
        for t in sorted(spenders, key=lambda t: str(t.get("date") or ""), reverse=True)[:5]
    ]
    return {
        "month": {
            "label": today.strftime("%m/%Y"),
            "status": None,
            "incomePlan": 0,
            "incomeActual": income,
            "spend": spend,
            "net": income - spend,
            "salaryRecorded": income > 0,
            "spendLiving": spend,
            "spendDebtTransfer": 0,
        },
        "envelopes": envelopes,
        "accounts": acct_rows,
        "recent": recent,
        "creditCardDebt": sum(c["debt"] for c in cards),
        "fund": {
            "allocated": sum(e["allocated"] for e in envelopes),
            "remaining": sum(e["remaining"] for e in envelopes),
        },
        "cards": cards,
        "source": "chillybank",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }


async def push_to_hub() -> bool:
    url, key = os.getenv("CHILLY_HUB_URL"), os.getenv("CHILLY_HUB_PUSH_KEY")
    if not url or not key:
        return False
    try:
        snapshot = build_snapshot()
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                url.rstrip("/") + "/api/finance/snapshot",
                json=snapshot,
                headers={"Authorization": f"Bearer {key}"},
            )
        r.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 — a missed push leaves the TV on the last snapshot
        logger.warning("hub_push: failed: %s", exc)
        return False

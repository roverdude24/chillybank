"""Push this month's money snapshot to the family TV hub (ChillyBank).

The hub's Tài chính screen used to read Notion. ChillyBank is now the book, so it sends the
hub a small snapshot in the hub's own ``/api/finance`` shape. Outbound only: the hub holds a
key that can write ONE thing (this snapshot) and nothing that can read or change the book.

Off unless ``CHILLY_HUB_URL`` and ``CHILLY_HUB_PUSH_KEY`` are both set.
"""

from __future__ import annotations

import os
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

import httpx

from backend.core.user_time import user_today
from backend.services import ledger as svc
from backend.services import planning as planning_service


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


# The hub's own rules (web/src/data.ts): living spend is these groups; a card's due date and minimum payment
# are typed into its note ("… hạn trả 26/10/2026 (tối thiểu 153.974đ) …"), "ước tính" marks an estimate.
LIVING_GROUPS = ("Thiết yếu", "Công việc")
_DUE = re.compile(r"hạn\s+(?:trả|thanh toán)?\s*(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", re.I)
_MIN = re.compile(r"tối thiểu\s*(\d{1,3}(?:[.,]\d{3})+|\d+)(?:\s*(?:đ|₫|vnđ|vnd))?(?![^\W_]|[.,]\d)", re.I)


def _card_info(note: str, today: date) -> dict:
    note = unicodedata.normalize("NFC", note or "")
    due_date = None
    if m := _DUE.search(note):
        try:
            due = date(int(m[3]) if m[3] else today.year, int(m[2]), int(m[1]))
            # No year written: this year's date, unless that is half a year gone (then it means next year).
            if not m[3] and due < today - timedelta(days=183):
                due = due.replace(year=today.year + 1)
            due_date = due.isoformat()
        except ValueError:
            pass
    mn = _MIN.search(note)
    return {
        "dueDate": due_date,
        "minPayment": int(re.sub(r"[.,]", "", mn[1])) if mn else None,
        "estimate": "ước tính" in note.lower(),
    }


def build_snapshot() -> dict:
    today = user_today()
    ym = today.strftime("%Y-%m")
    month = planning_service.budget_month(ym)
    txns = svc.transactions.all()
    envelopes, envelope_of = _envelopes(month, txns)

    accts = svc.accounts.query_accounts()
    notes = {a["id"]: a.get("note") or "" for a in accts}
    accounts = svc.accounts_with_balances(accts, txns, idx=svc.opening_index(txns, accts))
    acct_rows = [
        {
            "name": a.get("name") or "",
            "type": "Thẻ tín dụng" if a.get("type") == "credit" else a.get("type"),
            "balance": float(a.get("balance_base") if a.get("balance_base") is not None else a.get("balance") or 0),
            "note": notes.get(a.get("id"), ""),
        }
        for a in accounts
    ]
    cards = sorted(
        (
            {"name": a["name"], "debt": -a["balance"], **_card_info(a["note"], today)}
            for a in acct_rows
            if a["type"] == "Thẻ tín dụng"
        ),
        key=lambda c: (c["dueDate"] or "9", -c["debt"]),
    )
    for a in acct_rows:
        del a["note"]
    group_of = {
        cid: g.get("name")
        for g in month.get("groups") or []
        for c in g.get("categories") or []
        for cid in c.get("category_ids") or []
    }

    in_month = [t for t in txns if str(t.get("date") or "").startswith(ym)]
    income = sum(_amount(t) for t in in_month if t.get("kind") == "income")
    spend = sum(_amount(t) for t in in_month if t.get("kind") == "expense")
    living = sum(
        _amount(t) for t in in_month if t.get("kind") == "expense" and group_of.get(t.get("category")) in LIVING_GROUPS
    )
    kinds = {"expense": "Chi", "income": "Thu", "transfer": "Chuyển nội bộ"}
    shown = [t for t in txns if t.get("kind") in kinds]
    recent = [
        {
            "date": str(t.get("date") or "")[:10],
            "title": t.get("description") or t.get("memo") or "",
            "amount": _amount(t) * (-1 if t.get("kind") == "expense" else 1),
            "kind": kinds[t["kind"]],
            "envelope": envelope_of.get(t.get("category") or ""),
        }
        for t in sorted(shown, key=lambda t: str(t.get("date") or ""), reverse=True)[:5]
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
            "spendLiving": living,
            "spendDebtTransfer": spend - living,
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
    """Send the snapshot; False when not configured. A failed push raises (chilly_jobs reports it);
    the TV keeps showing the last snapshot meanwhile."""
    url, key = os.getenv("CHILLY_HUB_URL"), os.getenv("CHILLY_HUB_PUSH_KEY")
    if not url or not key:
        return False
    snapshot = build_snapshot()
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            url.rstrip("/") + "/api/finance/snapshot",
            json=snapshot,
            headers={"Authorization": f"Bearer {key}"},
        )
    r.raise_for_status()
    return True

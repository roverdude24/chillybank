"""Notion "Tài chính" -> ChillyBank, one way: the family's input channel.

Rows typed into the Notion finance workspace flow into the book. Nothing is ever written to Notion,
and nothing created in the book is pushed to it. ``sync_notion_input()`` does nothing without
``NOTION_TOKEN``. It reads the four data sources in full before it writes anything (a failed read
leaves the book untouched), then applies them through the services the REST API and Lucius use.

Identity (re-running never duplicates):
- accounts      ``external_ids``   ``{source: "notion", ref: <page id>}``
- transactions  ``external_refs``  ``{source: "notion", ref: <page id>}``, writer ``feed``
- envelopes     the book has no external id for them, so the ``notion_input`` settings namespace keeps
                page id -> envelope id; an unlinked envelope of the same name is adopted.
That namespace also keeps a fingerprint of every row as last imported. A row is rewritten only when
Notion's version changed since, so an edit made in ChillyBank survives every later sync, and a row
deleted in ChillyBank stays deleted.

Mapping (Notion property -> ChillyBank):
- account   Tài khoản -> name; Ghi chú -> note; currency VND. Loại -> type: Thẻ tín dụng credit,
            Tiết kiệm / Sinh lời fixed_deposit, Đầu tư investment, the rest (Thanh toán, Ví điện
            tử, Tiền mặt) cash; Notion has no loan Loại, so Thanh khoản "Nợ phải trả" on a
            non-card is loan. Số dư is the balance as of Cập nhật (else today); it is booked
            once, as the Starting Balance, backed out of the rows Notion already counted in it,
            so the account reads Số dư after the first sync and every imported row still counts.
- envelope  Ngăn tiền -> envelope, with a category of the same name (kind expense) that its rows
            are booked under; Nhóm -> envelope group. A rename or regroup in Notion follows; the
            envelope's categories keep their labels. Tổng phân bổ (Từ quỹ 1 tỷ + Bổ sung từ thu nhập,
            summed if the formula is empty) becomes the envelope's assignment in the plan's start
            month, the earliest of the Month table's Kỳ and the first imported row, so that with
            rollover its available equals Notion's Còn lại. When it changes in Notion that one
            assignment follows; no other month is touched. Mục tiêu/tháng -> monthly target (one
            removed in Notion is not removed here).
- row       Nội dung -> description; Ghi chú -> memo; Ngày -> date; Thu minus Chi -> income or
            expense (a row with neither, such as Chuyển nội bộ, has no single account to book on
            and is skipped); Ngăn tiền -> category; Tài khoản -> account.
A row with no Tài khoản, or whose account is gone from the book, goes to one cash account named
"Notion", opened at zero balance so it adds nothing to net worth on the first sync. Everything is
in VND. Not imported: the rest of the month table, income-source columns, interest rates, and
deletions made in Notion.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import unicodedata
from collections import defaultdict
from datetime import date

import httpx

from backend.core.audit_context import clear_audit_scope, set_audit_scope_if_absent
from backend.core.errors import OpenNetWorthError, PreviouslyDeletedError
from backend.core.user_time import user_today
from backend.db.payload_store import NamespacedSettingsStore
from backend.services import accounts as accounts_service
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import ledger as ledger_service
from backend.services import planning

logger = logging.getLogger(__name__)

SOURCE = "notion"
CURRENCY = "VND"
FALLBACK_REF, FALLBACK_NAME = "fallback", "Notion"
DATA_SOURCES = {
    "month": "fb106c98-5875-49a8-b3f9-008ae7e6c895",
    "account": "b431cd69-988c-4a78-81f9-214937e2ed82",
    "envelope": "f7b2be42-2ae0-4fa4-8eee-f56415410e17",
    "tx": "b540b4e2-f94e-490f-aea8-760ed047d12d",
}
_QUERY = "https://api.notion.com/v1/data_sources/{}/query"
_NOTION_VERSION = "2025-09-03"
_links = NamespacedSettingsStore("notion_input")
# Loại keyword -> type for the non-card, non-debt accounts; anything else (bank, ví, tiền mặt) is spendable cash.
_ACCOUNT_TYPES = (("tiết kiệm", "fixed_deposit"), ("đầu tư", "investment"))


# ── Notion ──────────────────────────────────────────────────────────────────────


async def _query(client: httpx.AsyncClient, data_source: str) -> list[dict]:
    pages: list[dict] = []
    cursor = None
    while True:
        body = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
        response = await client.post(_QUERY.format(data_source), json=body)
        response.raise_for_status()
        page = response.json()
        pages += page["results"]
        cursor = page.get("next_cursor")
        if not page.get("has_more") or not cursor:
            return pages


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _key(name: str) -> str:
    return _nfc(name).strip().casefold()


def _val(prop: dict):
    kind = prop.get("type")
    value = prop.get(kind)
    if kind in ("title", "rich_text"):
        return _nfc("".join(part.get("plain_text", "") for part in value or []))
    if kind in ("select", "status"):
        return _nfc(value["name"]) if value else None
    if kind == "date":
        return value["start"] if value else None
    if kind == "relation":
        return [rel["id"] for rel in value or []]
    if kind == "formula":
        return value.get(value.get("type")) if value else None
    return value


def _props(page: dict) -> dict:
    """Plain values by NFC-normalised property name, so Vietnamese names match either Unicode form."""
    return {_nfc(name): _val(prop) for name, prop in page["properties"].items()}


def _num(value) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _day(value) -> str | None:
    try:
        return date.fromisoformat((value or "")[:10]).isoformat()
    except ValueError:
        return None


def _first(value) -> str | None:
    return (value or [None])[0]


def _fp(want: dict) -> str:
    return hashlib.sha256(
        json.dumps(want, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]


# ── Book ────────────────────────────────────────────────────────────────────────


def _counts() -> dict:
    return {"created": 0, "updated": 0, "skipped": 0}


def _refresh(counts: dict, fps: dict, ref: str, want: dict, write) -> None:
    """Re-assert Notion's version of a row already in the book, but only when Notion changed it since
    the last import: ``write()`` applies the difference and says whether it changed anything."""
    fp = _fp(want)
    if fps.get(ref) == fp:
        counts["skipped"] += 1
        return
    counts["updated" if write() else "skipped"] += 1
    fps[ref] = fp


def _patch(existing: dict, want: dict, update) -> bool:
    patch = {field: value for field, value in want.items() if existing.get(field) != value}
    return bool(patch) and update(patch) is not None


def _account_type(props: dict) -> str:
    """Loại gives cards, savings and investments; Notion has no loan Loại, so debt is Thanh khoản
    "Nợ phải trả" on anything that is not a card."""
    loai = (props.get("Loại") or "").casefold()
    if "thẻ tín dụng" in loai:
        return "credit"
    if "nợ phải trả" in (props.get("Thanh khoản") or "").casefold():
        return "loan"
    return next((t for k, t in _ACCOUNT_TYPES if k in loai), "cash")


def _opening(balance: float, as_of: str, rows: list[tuple[str, float]]) -> tuple[float, str]:
    """The Starting Balance (amount, date) that makes an account read ``balance`` once ``rows``
    (date, signed amount) are booked: rows dated up to ``as_of`` are already inside the balance."""
    counted = [(day, amount) for day, amount in rows if day <= as_of]
    return round(balance - sum(amount for _, amount in counted), 2), min(
        [as_of, *(d for d, _ in counted)]
    )


def _new_account(name, type_, note, ref, balance, as_of, rows) -> dict:
    opening, since = _opening(balance, as_of, rows)
    return accounts_service.create_account(
        {
            "name": name,
            "type": type_,
            "currency": CURRENCY,
            "note": note,
            "initial_balance": opening,
            "initial_balance_date": since,
            "external_ids": [{"source": SOURCE, "ref": ref, "name": name}],
        }
    )


def _accounts(pages: list[dict], txs: list[dict], fps: dict) -> tuple[dict, dict]:
    counts, ids = _counts(), {}
    by_ref = {
        entry["ref"]: account
        for account in ledger_service.accounts.all()
        for entry in account.get("external_ids") or []
        if entry.get("source") == SOURCE
    }
    today = user_today().isoformat()
    rows_of = defaultdict(list)  # per account page: the rows still to be booked on it
    for tx in txs:
        if tx["booked"] or tx["ref"] not in fps:  # not one deleted here since the import
            rows_of[tx["account"]].append((tx["date"], tx["amount"]))
    for page in pages:
        props, ref = _props(page), page["id"]
        name = (props.get("Tài khoản") or "").strip()
        want = {"name": name, "note": props.get("Ghi chú") or ""}
        existing = by_ref.get(ref)
        try:
            if not name:
                counts["skipped"] += 1
            elif existing is not None:
                ids[ref] = existing["id"]
                _refresh(
                    counts,
                    fps,
                    ref,
                    want,
                    lambda: _patch(
                        existing, want, lambda p: accounts_service.update_account(existing["id"], p)
                    ),
                )
            elif ref in fps:  # imported before, deleted here since: it stays deleted
                counts["skipped"] += 1
            else:
                type_ = _account_type(props)
                balance = _num(props.get("Số dư"))
                account = _new_account(
                    name,
                    type_,
                    want["note"],
                    ref,
                    -abs(balance) if type_ == "loan" else balance,
                    _day(props.get("Cập nhật")) or today,
                    rows_of[ref],
                )
                ids[ref] = account["id"]
                counts["created"] += 1
                fps[ref] = _fp(want)
        except (OpenNetWorthError, ValueError) as exc:
            logger.warning("notion_input: account %r not imported: %s", name, exc)
            counts["skipped"] += 1
    orphans = [row for key, rows in rows_of.items() if key not in ids for row in rows]
    if orphans and FALLBACK_REF in by_ref:
        ids[FALLBACK_REF] = by_ref[FALLBACK_REF]["id"]
    elif orphans and FALLBACK_REF not in fps:  # (else deleted here since: no new one)
        try:
            account = _new_account(FALLBACK_NAME, "cash", "", FALLBACK_REF, 0.0, today, orphans)
            ids[FALLBACK_REF] = account["id"]
            counts["created"] += 1
            fps[FALLBACK_REF] = _fp({"name": FALLBACK_NAME})
        except (OpenNetWorthError, ValueError) as exc:
            logger.warning("notion_input: the %r account was not created: %s", FALLBACK_NAME, exc)
    return counts, ids


def _group_id(name: str | None, groups: dict) -> str:
    """The book's group for a Notion Nhóm, made when missing; "" files the envelope under Others."""
    if not name:
        return ""
    if _key(name) not in groups:
        try:
            created = planning.apply_budget_change({"type": "group_create", "name": name})
            groups[_key(name)] = created["group"]["id"]
        except OpenNetWorthError:  # a name the app keeps for itself
            groups[_key(name)] = ""
    return groups[_key(name)]


def _category(envelope: dict) -> str:
    """The category Notion's rows for this envelope are booked under: its own, else one named like it."""
    own = [
        c for c in envelope.get("category_ids") or [] if categories_service.resolve_category_ref(c)
    ]
    if own:
        return own[0]
    category = (
        categories_service.resolve_category_ref(envelope["name"])
        or planning.apply_budget_change(
            {"type": "category_create", "label": envelope["name"], "kind": "expense"}
        )["id"]
    )
    if category not in envelopes_service.env_of():
        planning.apply_budget_change(
            {
                "type": "envelope_add_category",
                "envelope_id": envelope["id"],
                "category_id": category,
            }
        )
    return category


def _money(envelope: dict, assigned: float, target: float | None, start: str | None) -> bool:
    """Put Notion's allocation in the plan's start month and its monthly target on the envelope. Only
    that one month's assignment is touched; whatever is budgeted in any other month is the owner's."""
    changed = False
    if start and planning.assignments_store.month_map(start).get(envelope["id"], 0.0) != assigned:
        planning.apply_budget_change(
            {"type": "assign", "month": start, "envelope_id": envelope["id"], "amount": assigned}
        )
        changed = True
    now = planning.read_category_targets().get(envelope["id"]) or {}
    if target and (now.get("type"), now.get("amount")) != ("monthly", target):
        planning.apply_budget_change(
            {
                "type": "set_target",
                "envelope_id": envelope["id"],
                "target_type": "monthly",
                "amount": target,
            }
        )
        changed = True
    return changed


def _retitle(envelope: dict, name: str, group_id: str) -> bool:
    """Follow a rename or regroup made in Notion. Only the envelope moves: its categories keep their
    labels, as when an envelope is renamed in the app."""
    changed = False
    if envelope["name"] != name:
        planning.apply_budget_change(
            {"type": "envelope_rename", "envelope_id": envelope["id"], "name": name}
        )
        changed = True
    if group_id and envelope.get("group_id") != group_id:
        planning.apply_budget_change(
            {"type": "move_category", "category": envelope["id"], "group_id": group_id}
        )
        changed = True
    return changed


def _allocation(props: dict) -> float:
    """Tổng phân bổ is a formula (Từ quỹ 1 tỷ + Bổ sung từ thu nhập); if it comes back empty, add the two."""
    total = props.get("Tổng phân bổ")
    if not isinstance(total, (int, float)) or isinstance(total, bool):
        total = _num(props.get("Từ quỹ 1 tỷ")) + _num(props.get("Bổ sung từ thu nhập"))
    return max(0.0, round(float(total), 2))


def _start_month(months: list[dict], txs: list[dict]) -> str | None:
    """The plan's first month: the earliest Notion month (Kỳ), or an earlier transaction, so that no
    imported spend falls before the month the allocation sits in."""
    days = [d for d in (_day(_props(m).get("Kỳ")) for m in months) if d]
    days += [tx["date"] for tx in txs]
    return min(days)[:7] if days else None


def _envelopes(pages: list[dict], envs: dict, fps: dict, start: str | None) -> tuple[dict, dict]:
    counts, categories = _counts(), {}
    live = {e["id"]: e for e in envelopes_service.all_envelopes() if not e.get("archived")}
    groups = {_key(g["name"]): g["id"] for g in envelopes_service.groups_store.list_ordered()}
    for page in pages:
        props, ref = _props(page), page["id"]
        name = (props.get("Ngăn tiền") or "").strip()
        group = props.get("Nhóm")
        assigned, target = _allocation(props), _num(props.get("Mục tiêu/tháng")) or None
        want = {
            "name": name,
            "group": group,
            "assigned": assigned,
            "target": target,
            "start": start,
        }
        try:
            envelope = live.get(envs.get(ref)) or next(
                (e for e in live.values() if _key(e["name"]) == _key(name)), None
            )
            gone = envelope is None and ref in fps  # imported before, deleted here since
            if not name or gone:
                counts["skipped"] += 1
            elif envelope is None:
                created = planning.apply_budget_change(
                    {"type": "envelope_create", "name": name, "group_id": _group_id(group, groups)}
                )
                envs[ref] = created["envelope"]["id"]
                categories[ref] = _category(created["envelope"])
                counts["created"] += 1
                _money(created["envelope"], assigned, target, start)
                fps[ref] = _fp(want)
            else:
                envs[ref] = envelope["id"]
                categories[ref] = _category(envelope)
                _refresh(
                    counts,
                    fps,
                    ref,
                    want,
                    lambda: (
                        _retitle(envelope, name, _group_id(group, groups))
                        | _money(envelope, assigned, target, start)
                    ),
                )
        except (OpenNetWorthError, ValueError) as exc:
            logger.warning("notion_input: envelope %r not imported: %s", name, exc)
            counts["skipped"] += 1
    return counts, categories


def _tx(page: dict) -> dict | None:
    props = _props(page)
    day, amount = _day(props.get("Ngày")), _num(props.get("Thu")) - _num(props.get("Chi"))
    if day is None or amount == 0:
        return None
    return {
        "ref": page["id"],
        "date": day,
        "amount": amount,
        "description": props.get("Nội dung") or FALLBACK_NAME,
        "memo": props.get("Ghi chú") or "",
        "account": _first(props.get("Tài khoản")),
        "envelope": _first(props.get("Ngăn tiền")),
        "source": props.get("Nguồn thu"),
        "funding": props.get("Nguồn tiền"),
    }


def _income_category(tx: dict) -> str:
    """Income has no envelope in Notion; its category is the row's Nguồn thu. An untagged row titled
    "Lãi …" is interest (the TV hub reads it the same way); anything else untagged is other income."""
    source = tx.get("source")
    if not source or source == "Khác":
        source = "Lãi tiền gửi" if _nfc(tx["description"]).lower().startswith("lãi") else "Thu nhập khác"
    return categories_service.resolve_category_ref(source) or categories_service.create_category(source, "income")["id"]


def _booking(tx: dict, account: str | None, categories: dict) -> dict:
    """The ledger fields a Notion row means. The unused leg is cleared, so a row whose Chi became a Thu
    flips cleanly. A row's category is left as it is when its envelope no longer exists here."""
    side, other = ("to", "from") if tx["amount"] > 0 else ("from", "to")
    row = {
        "date": tx["date"],
        "kind": "income" if tx["amount"] > 0 else "expense",
        "description": tx["description"],
        "memo": tx["memo"],
        f"{side}_account_id": account,
        f"{side}_amount": abs(tx["amount"]),
        f"{side}_currency": CURRENCY,
        f"{other}_account_id": None,
        f"{other}_amount": None,
        f"{other}_currency": None,
    }
    if not tx["envelope"] or tx["envelope"] in categories:
        row["category"] = categories.get(tx["envelope"])
    if row["kind"] == "income" and not row.get("category"):
        row["category"] = _income_category(tx)
    if tx.get("funding"):
        row["tags"] = [f"Nguồn: {tx['funding']}"]
    return row


def _transactions(txs: list[dict], accounts: dict, categories: dict, fps: dict) -> dict:
    counts = _counts()
    account_rows = ledger_service.accounts.all()
    for tx in txs:
        ref = tx["ref"]
        data = _booking(tx, accounts.get(tx["account"], accounts.get(FALLBACK_REF)), categories)
        existing = tx["booked"]
        try:
            if existing is not None:
                _refresh(
                    counts,
                    fps,
                    ref,
                    data,
                    lambda: _patch(
                        existing,
                        data,
                        lambda p: ledger_service.update_transaction(
                            existing["id"], p, labels_as_given=True
                        ),
                    ),
                )
            elif ref in fps:  # booked before, deleted here since: it stays deleted
                counts["skipped"] += 1
            else:
                ledger_service.create_transaction(
                    {
                        **data,
                        "source": "feed",
                        "needs_review": False,  # typed by the family: no review queue for a whole history
                        "external_refs": [{"source": SOURCE, "ref": ref}],
                    },
                    allow_duplicate=True,
                    labels_as_given=True,
                    account_rows=account_rows,
                )
                counts["created"] += 1
                fps[ref] = _fp(data)
        except PreviouslyDeletedError:
            counts["skipped"] += 1
        except (OpenNetWorthError, ValueError) as exc:
            logger.warning("notion_input: transaction %s not booked: %s", ref, exc)
            counts["skipped"] += 1
    return counts


def _apply(pages: dict[str, list[dict]]) -> dict:
    owns_scope = set_audit_scope_if_absent("system", "import:notion", note="Notion Tài chính")
    doc = _links.get() or {}
    before = copy.deepcopy(doc)
    fps, envs = doc.setdefault("fp", {}), doc.setdefault("env", {})
    try:
        txs = [tx for tx in map(_tx, pages["tx"]) if tx]
        for tx in txs:
            tx["booked"] = ledger_service.transactions.by_external_ref(SOURCE, tx["ref"])
        accounts, account_ids = _accounts(pages["account"], txs, fps)
        start = _start_month(pages["month"], txs)
        envelopes, category_ids = _envelopes(pages["envelope"], envs, fps, start)
        transactions = _transactions(txs, account_ids, category_ids, fps)
        transactions["skipped"] += len(pages["tx"]) - len(txs)
    finally:
        if doc != before:
            _links.save(doc)
        if owns_scope:
            clear_audit_scope()
    return {"accounts": accounts, "envelopes": envelopes, "transactions": transactions}


async def sync_notion_input() -> dict:
    """Pull the Notion workspace into the book; returns what was created, updated and skipped."""
    token = os.environ.get("NOTION_TOKEN", "").strip()
    if not token:
        return {"skipped": "no token"}
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": _NOTION_VERSION}
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        pages = {kind: await _query(client, source) for kind, source in DATA_SOURCES.items()}
    # The book's writes are blocking: keep them off the event loop.
    result = await asyncio.to_thread(_apply, pages)
    logger.info("notion_input: %s", result)
    return result

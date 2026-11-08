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
in VND. Not imported: the rest of the month table, income-source columns, interest rates.

Deletions: after a complete read, an imported row whose Notion page is gone is deleted here too
(ledger delete, so it is checkpointed and its identity remembered), unless it was edited here since
the import; those are kept and counted. If over half the imported rows (or more than 20) vanish at
once, nothing is deleted. Balances: when an account's Số dư or Cập nhật changes in Notion, the gap
to the book's balance at Cập nhật is booked as one adjustment "Đối chiếu số dư Notion" (ref
``bal:<page>:<Cập nhật>``), after the rows are synced so a matching Thu row nets to zero.
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
BALANCE_PREFIX, BALANCE_DESCRIPTION = "bal:", "Đối chiếu số dư Notion"
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


def _linked_accounts() -> dict:
    """The book's accounts that came from Notion, by Notion page id."""
    return {
        entry["ref"]: account
        for account in ledger_service.accounts.all()
        for entry in account.get("external_ids") or []
        if entry.get("source") == SOURCE
    }


def _counted_transfers(pages: list[dict], txs: list[dict], fps: dict, seen: dict) -> None:
    """Mark the transfers a Số dư already contains, so they are recorded but not booked.

    An earlier version skipped every Chuyển nội bộ row, so an account it created holds a Starting
    Balance (Số dư as of its Cập nhật) that already includes any transfer up to then. Booking such a
    transfer would count it twice. An account is anchored to the Cập nhật its balance was last agreed
    to: the stored baseline, or on first sight the Cập nhật it has now (a blank one is today). A new
    transfer dated on or before the anchor of either existing account is already counted. When the
    pair changed this sync, ``_reconcile`` corrects the balance whichever way, so the transfer is
    booked; accounts created this sync are opened to count it and have no anchor."""
    anchor, today, linked = {}, user_today().isoformat(), _linked_accounts()
    for page in pages:
        ref = page["id"]
        if ref not in linked:
            continue
        props = _props(page)
        pair = [_num(props.get("Số dư")), _day(props.get("Cập nhật"))]
        if ref not in seen:
            anchor[ref] = pair[1] or today
        elif seen[ref] == pair:
            anchor[ref] = pair[1]
    for tx in txs:
        if tx["to_account"] and not tx["booked"] and tx["ref"] not in fps:
            tx["counted"] = any(
                anchor.get(a) is not None and tx["date"] <= anchor[a]
                for a in (tx["account"], tx["to_account"])
            )


def _accounts(pages: list[dict], txs: list[dict], fps: dict) -> tuple[dict, dict]:
    counts, ids = _counts(), {}
    by_ref = _linked_accounts()
    today = user_today().isoformat()
    rows_of = defaultdict(list)  # per account page: the rows still to be booked on it
    # Accounts this sync imports or keeps: one deleted here since the import is not among them.
    known = {
        page["id"]
        for page in pages
        if _props(page).get("Tài khoản") and (page["id"] in by_ref or page["id"] not in fps)
    }
    for tx in txs:
        if tx.get("counted") or not (tx["booked"] or tx["ref"] not in fps):  # or deleted here
            continue
        if tx["to_account"]:  # a transfer: out of one account, into the other
            if tx["account"] in known and tx["to_account"] in known:
                rows_of[tx["account"]].append((tx["date"], -tx["amount"]))
                rows_of[tx["to_account"]].append((tx["date"], tx["amount"]))
        else:
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
    source, target = _first(props.get("Tài khoản")), _first(props.get("Đến tài khoản"))
    # Chuyển nội bộ: Chi and Thu are 0, so the amount is Số tiền; it needs both accounts to be a transfer.
    transfer = props.get("Loại") == "Chuyển nội bộ" and source and target and source != target
    if transfer:
        amount = _num(props.get("Số tiền"))
    if day is None or (amount <= 0 if transfer else amount == 0):
        return None
    return {
        "ref": page["id"],
        "date": day,
        "amount": amount,
        "description": props.get("Nội dung") or FALLBACK_NAME,
        "memo": props.get("Ghi chú") or "",
        "account": source,
        "to_account": target if transfer else None,
        "envelope": _first(props.get("Ngăn tiền")),
        "source": props.get("Nguồn thu"),
        "funding": props.get("Nguồn tiền"),
    }


def _income_category(tx: dict) -> str:
    """Income has no envelope in Notion; its category is the row's Nguồn thu. An untagged row titled
    "Lãi …" is interest (the TV hub reads it the same way); anything else untagged is other income."""
    source = tx.get("source")
    if not source or source == "Khác":
        source = (
            "Lãi tiền gửi" if _nfc(tx["description"]).lower().startswith("lãi") else "Thu nhập khác"
        )
    return (
        categories_service.resolve_category_ref(source)
        or categories_service.create_category(source, "income")["id"]
    )


def _booking(
    tx: dict, account: str | None, categories: dict, to_account: str | None = None
) -> dict:
    """The ledger fields a Notion row means. The unused leg is cleared, so a row whose Chi became a Thu
    flips cleanly. A row's category is left as it is when its envelope no longer exists here."""
    if tx["to_account"]:  # a transfer has both legs
        side, other = "from", "to"
        legs = {
            "to_account_id": to_account,
            "to_amount": tx["amount"],
            "to_currency": CURRENCY,
        }
    else:
        side, other = ("to", "from") if tx["amount"] > 0 else ("from", "to")
        legs = {f"{other}_account_id": None, f"{other}_amount": None, f"{other}_currency": None}
    row = {
        "date": tx["date"],
        "kind": "transfer" if tx["to_account"] else "income" if tx["amount"] > 0 else "expense",
        "description": tx["description"],
        "memo": tx["memo"],
        f"{side}_account_id": account,
        f"{side}_amount": abs(tx["amount"]),
        f"{side}_currency": CURRENCY,
        **legs,
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
        if tx["to_account"]:
            source, target = accounts.get(tx["account"]), accounts.get(tx["to_account"])
            if source is None or target is None:  # an account that was not imported
                counts["skipped"] += 1
                continue
        else:
            source, target = accounts.get(tx["account"], accounts.get(FALLBACK_REF)), None
        data = _booking(tx, source, categories, target)
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
            elif tx.get("counted"):  # already inside an account's Số dư: noted, not booked again
                fps[ref] = _fp(data)
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


_BOOKING_KEYS = (
    "date",
    "kind",
    "description",
    "memo",
    "from_account_id",
    "from_amount",
    "from_currency",
    "to_account_id",
    "to_amount",
    "to_currency",
)
MAX_VANISHED = 20  # more rows than this gone from Notion in one sync is a bad read, not a clean-up


def _unchanged_since_import(row: dict, stored: str) -> bool:
    """Does the row still read exactly as the sync booked it? ``category`` and ``tags`` were part of
    the booking only sometimes (no envelope here, no Nguồn tiền), so each way of leaving them out
    is tried; a row someone gave tags to is never taken for untagged."""
    base = {key: row.get(key) for key in _BOOKING_KEYS}
    for with_category in (True, False):
        for with_tags in (True, False):
            if not with_tags and row.get("tags"):
                continue
            booking = dict(base)
            if with_category:
                booking["category"] = row.get("category")
            if with_tags:
                booking["tags"] = row.get("tags")
            if _fp(booking) == stored:
                return True
    return False


def _vanished(page_ids: set[str], fps: dict) -> dict:
    """Mirror rows deleted in Notion. Called after a complete read of the transaction source, so a
    row whose page is no longer in it was deleted there. It is deleted here too, through the ledger's
    own delete, unless someone edited it here since the import (it is kept and counted). Nothing is
    deleted when an implausible share of the imported rows has vanished at once."""
    out = {"deleted": 0, "kept_edited": 0}
    gone, imported = [], 0
    for row in ledger_service.transactions.all():
        if row.get("deleted"):
            continue
        ref = next(
            (
                e["ref"]
                for e in row.get("external_refs") or []
                if e.get("source") == SOURCE and not e["ref"].startswith(BALANCE_PREFIX)
            ),
            None,
        )
        if ref is None:
            continue
        imported += 1
        if ref not in page_ids:
            gone.append((ref, row))
    if not gone:
        return out
    if len(gone) > MAX_VANISHED or len(gone) * 2 > imported:
        logger.warning(
            "notion_input: %d of %d imported rows are gone from Notion; deleting none",
            len(gone),
            imported,
        )
        out["delete_blocked"] = len(gone)
        return out
    doomed = []
    for ref, row in gone:
        if fps.get(ref) and _unchanged_since_import(row, fps[ref]):
            doomed.append((ref, row["id"]))
        else:
            out["kept_edited"] += 1
    if doomed:
        # Not remembered as rejected: a page restored in Notion must be able to come back.
        ledger_service.bulk_delete_transactions(
            [txn_id for _, txn_id in doomed], reject_identities=False
        )
        for ref, _ in doomed:
            fps.pop(ref, None)
        out["deleted"] = len(doomed)
    return out


def _adjustment_legs(account: dict, delta: float) -> dict:
    side, other = ("to", "from") if delta > 0 else ("from", "to")
    return {
        f"{side}_account_id": account["id"],
        f"{side}_amount": abs(delta),
        f"{side}_currency": account.get("currency") or CURRENCY,
        f"{other}_account_id": None,
        f"{other}_amount": None,
        f"{other}_currency": None,
    }


def _reconcile_one(
    account: dict, page_ref: str, balance: float, day: str, *, refold: bool = False
) -> bool:
    """Make the account read ``balance`` on ``day`` with one adjustment, the row ``ledger.reconcile``
    books (an ``adjustment`` through ``create_transaction``). ``reconcile`` itself diffs the cleared
    balance as of today under a fixed description, so it cannot be dated, referenced or idempotent.

    ``refold`` re-checks an adjustment already booked for this pair (rows typed late or deleted since
    change the gap) and does nothing when there is none. Otherwise the pair is new, and adjustments
    of this account dated after the new day belong to a Cập nhật it no longer has: they are removed,
    or a Cập nhật corrected backwards would leave the old one beside the new."""
    txns = ledger_service.transactions.all()
    opened = ledger_service.opening_date(account, txns)
    day = max(day, opened or day)  # before its tracking start the book holds nothing to compare
    ref = f"{BALANCE_PREFIX}{page_ref}:{day}"
    existing = ledger_service.transactions.by_external_ref(SOURCE, ref)
    if refold and existing is None:
        return False
    if not refold:
        prefix = f"{BALANCE_PREFIX}{page_ref}:"
        later = [
            t["id"]
            for t in txns
            if not t.get("deleted")
            and any(
                e.get("source") == SOURCE
                and e["ref"].startswith(prefix)
                and e["ref"][len(prefix) :] > day
                for e in t.get("external_refs") or []
            )
        ]
        if later:
            ledger_service.bulk_delete_transactions(later, reject_identities=False)
            txns = ledger_service.transactions.all()
    current = ledger_service.account_balance_as_of(account, day, txns, opened)
    delta = round(balance - current, 2)
    if abs(delta) < 1:
        return False
    if existing is not None:  # the gap moved: change that adjustment by it
        signed = (existing.get("to_amount") or 0.0) - (existing.get("from_amount") or 0.0)
        total = round(signed + delta, 2)
        if total == 0:
            ledger_service.bulk_delete_transactions([existing["id"]], reject_identities=False)
        else:
            ledger_service.update_transaction(
                existing["id"], _adjustment_legs(account, total), labels_as_given=True
            )
        return True
    ledger_service.create_transaction(
        {
            "date": day,
            "kind": "adjustment",
            "description": BALANCE_DESCRIPTION,
            "source": "feed",
            "cleared": True,
            "needs_review": False,
            "external_refs": [{"source": SOURCE, "ref": ref}],
            **_adjustment_legs(account, delta),
        },
        allow_duplicate=True,
        labels_as_given=True,
    )
    return True


def _reconcile(pages: list[dict], ids: dict, seen: dict) -> int:
    """Keep each account at Notion's Số dư. The family types a new Số dư (and Cập nhật) by hand, and
    rows without a single account (Chuyển nội bộ) never reach the book, so balances drift. When the
    pair changes, the difference at Cập nhật is booked once. While an adjustment exists for the
    current pair it is re-checked every sync, so a row typed late (or deleted) that falls before
    Cập nhật changes the adjustment instead of the balance. The first sight of an account only
    records the pair; its opening already agrees with it."""
    today, reconciled = user_today().isoformat(), 0
    for page in pages:
        props, ref = _props(page), page["id"]
        account = ledger_service.accounts.find_by_id(ids[ref]) if ref in ids else None
        if account is None:
            continue
        pair = [_num(props.get("Số dư")), _day(props.get("Cập nhật"))]
        if ref not in seen:
            seen[ref] = pair
            continue
        balance = -abs(pair[0]) if account.get("type") == "loan" else pair[0]
        try:
            reconciled += _reconcile_one(
                account, ref, balance, pair[1] or today, refold=seen[ref] == pair
            )
        except (OpenNetWorthError, ValueError) as exc:
            logger.warning("notion_input: %r not reconciled: %s", account.get("name"), exc)
            continue
        seen[ref] = pair
    return reconciled


def _apply(pages: dict[str, list[dict]]) -> dict:
    owns_scope = set_audit_scope_if_absent("system", "import:notion", note="Notion Tài chính")
    doc = _links.get() or {}
    before = copy.deepcopy(doc)
    fps, envs = doc.setdefault("fp", {}), doc.setdefault("env", {})
    balances = doc.setdefault("bal", {})
    try:
        txs = [tx for tx in map(_tx, pages["tx"]) if tx]
        for tx in txs:
            tx["booked"] = ledger_service.transactions.by_external_ref(SOURCE, tx["ref"])
        _counted_transfers(pages["account"], txs, fps, balances)
        accounts, account_ids = _accounts(pages["account"], txs, fps)
        start = _start_month(pages["month"], txs)
        envelopes, category_ids = _envelopes(pages["envelope"], envs, fps, start)
        transactions = _transactions(txs, account_ids, category_ids, fps)
        transactions["skipped"] += len(pages["tx"]) - len(txs)
        transactions |= _vanished({p["id"] for p in pages["tx"]}, fps)
        accounts["reconciled"] = _reconcile(pages["account"], account_ids, balances)
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

"""The family's Notion finance workspace feeds the book one way: what is typed there is booked here, once."""

from __future__ import annotations

import json
import logging
import unicodedata
from datetime import date

import httpx
import pytest

from backend.core.fx import base_currency
from backend.db.payload_store import NamespacedSettingsStore
from backend.db.profile_store import ProfileStore
from backend.services import audit as audit_service
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import ledger as ledger_service
from backend.services import notion_input, planning

ACCOUNT, ENVELOPE, TX = (notion_input.DATA_SOURCES[k] for k in ("account", "envelope", "tx"))


def title(value):
    return {"type": "title", "title": [{"plain_text": value}]}


def text(value):
    return {"type": "rich_text", "rich_text": [{"plain_text": value}] if value else []}


def select(name):
    return {"type": "select", "select": {"name": name} if name else None}


def number(value):
    return {"type": "number", "number": value}


def formula(value):
    return {"type": "formula", "formula": {"type": "number", "number": value}}


def when(day):
    return {"type": "date", "date": {"start": day} if day else None}


def relation(*ids):
    return {"type": "relation", "relation": [{"id": i} for i in ids]}


def page(page_id, props):
    # Notion returns property names in whichever Unicode form they were typed; decomposed is the hard case.
    return {
        "id": page_id,
        "properties": {unicodedata.normalize("NFD", k): v for k, v in props.items()},
    }


def account(page_id, name, loai, balance, updated=None, note="", thanh_khoan=None):
    return page(
        page_id,
        {
            "Tài khoản": title(name),
            "Loại": select(loai),
            "Thanh khoản": select(thanh_khoan),
            "Số dư": number(balance),
            "Cập nhật": when(updated),
            "Ghi chú": text(note),
        },
    )


def envelope(page_id, name, group):
    return page(page_id, {"Ngăn tiền": title(name), "Nhóm": select(group)})


def row(page_id, name, day, *, chi=0, thu=0, bank=None, env=None, note=""):
    return page(
        page_id,
        {
            "Nội dung": title(name),
            "Ngày": when(day),
            "Chi": formula(chi),
            "Thu": formula(thu),
            "Loại": select("Chi" if chi else "Thu" if thu else "Chuyển nội bộ"),
            "Tài khoản": relation(*filter(None, [bank])),
            "Ngăn tiền": relation(*filter(None, [env])),
            "Ghi chú": text(note),
        },
    )


class FakeNotion:
    """Data sources by id, served two rows at a time so pagination is exercised."""

    def __init__(self) -> None:
        self.data = {ACCOUNT: [], ENVELOPE: [], TX: []}
        self.failing: str | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer secret"
        assert request.headers["Notion-Version"] == "2025-09-03"
        source = request.url.path.split("/")[-2]
        if source == self.failing:
            return httpx.Response(500, text="down")
        start = int(json.loads(request.content).get("start_cursor") or 0)
        more = start + 2 < len(self.data[source])
        return httpx.Response(
            200,
            json={
                "results": self.data[source][start : start + 2],
                "has_more": more,
                "next_cursor": str(start + 2) if more else None,
            },
        )


@pytest.fixture
def notion(db_isolated, monkeypatch: pytest.MonkeyPatch) -> FakeNotion:
    ProfileStore().set_raw({"base_currency": "VND"})
    assert base_currency() == "VND"
    monkeypatch.setenv("NOTION_TOKEN", "secret")
    monkeypatch.setattr(notion_input, "user_today", lambda: date(2026, 11, 20))
    fake = FakeNotion()
    real = httpx.AsyncClient
    monkeypatch.setattr(
        notion_input.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(fake), **kw),
    )
    fake.data[ACCOUNT] = [
        # Counted every row below in its Số dư (Cập nhật is after them all)...
        account("page-bank", "Ngân hàng", "Thanh toán", 10_000_000, "2026-11-10", "lương về đây"),
        # ...this one only up to 01/11, so its November row moves the balance.
        account("page-card", "Thẻ Visa", "Thẻ tín dụng", -2_000_000, "2026-11-01"),
        account("page-save", "Sổ tiết kiệm", "Tiết kiệm / Sinh lời", 100_000_000),
    ]
    fake.data[ENVELOPE] = [
        envelope("page-food", "Ăn uống", "Thiết yếu"),
        envelope("page-reserve", "Dự phòng", "Dự phòng"),
    ]
    fake.data[TX] = [
        row("tx-lunch", "Cơm trưa", "2026-11-03", chi=50_000, bank="page-bank", env="page-food"),
        row("tx-salary", "Lương", "2026-11-05", thu=20_000_000, bank="page-bank"),
        row("tx-coffee", "Cà phê", "2026-11-06", chi=45_000, bank="page-card", env="page-food"),
        row("tx-gift", "Quà", "2026-11-07", chi=100_000, env="page-reserve", note="sinh nhật"),
        row("tx-move", "Chuyển quỹ", "2026-11-08"),  # Chuyển nội bộ: neither Chi nor Thu
    ]
    return fake


def accounts_by_name() -> dict:
    return {a["name"]: a for a in ledger_service.accounts.all()}


def imported() -> dict:
    return {
        t["description"]: t
        for t in ledger_service.transactions.all()
        if t.get("external_refs") and not t.get("deleted")
    }


def edit(fake: FakeNotion, source: str, page_id: str, **props) -> None:
    """Retype some properties of one Notion row."""
    target = next(p for p in fake.data[source] if p["id"] == page_id)
    target["properties"].update({unicodedata.normalize("NFD", k): v for k, v in props.items()})


@pytest.mark.parametrize("token", [None, "", "  "])
async def test_without_a_token_nothing_happens(db_isolated, monkeypatch, token) -> None:
    if token is None:
        monkeypatch.delenv("NOTION_TOKEN", raising=False)
    else:
        monkeypatch.setenv("NOTION_TOKEN", token)

    def no_client(**_):
        raise AssertionError("no request may leave without a token")

    monkeypatch.setattr(notion_input.httpx, "AsyncClient", no_client)

    assert await notion_input.sync_notion_input() == {"skipped": "no token"}
    assert ledger_service.accounts.all() == []


async def test_first_sync_books_accounts_envelopes_and_rows(notion: FakeNotion) -> None:
    result = await notion_input.sync_notion_input()

    assert result == {
        "accounts": {
            "created": 4,
            "updated": 0,
            "skipped": 0,
        },  # three typed + the "Notion" catch-all
        "envelopes": {"created": 2, "updated": 0, "skipped": 0},
        "transactions": {
            "created": 4,
            "updated": 0,
            "skipped": 1,
        },  # the Chuyển nội bộ row has no direction
    }

    accounts = accounts_by_name()
    assert {n: a["type"] for n, a in accounts.items()} == {
        "Ngân hàng": "cash",
        "Thẻ Visa": "credit",
        "Sổ tiết kiệm": "fixed_deposit",
        "Notion": "cash",
    }
    assert {a["currency"] for a in accounts.values()} == {"VND"}
    assert accounts["Ngân hàng"]["note"] == "lương về đây"
    assert accounts["Ngân hàng"]["external_ids"][0]["source"] == "notion"
    balances = ledger_service.balances()
    assert {n: balances[a["id"]] for n, a in accounts.items()} == {
        "Ngân hàng": 10_000_000,  # Số dư, with the rows it already counted backed out of the opening
        "Thẻ Visa": -2_045_000,  # Số dư as of 01/11, then the coffee after it
        "Sổ tiết kiệm": 100_000_000,
        "Notion": 0,  # the catch-all adds nothing to net worth on day one
    }
    # Every imported row is dated on or after its account's tracking start, so none is curtained off.
    assert {n: ledger_service.opening_date(a) for n, a in accounts.items()} == {
        "Ngân hàng": "2026-11-03",  # the earliest row its Số dư already counted
        "Thẻ Visa": "2026-11-01",  # Cập nhật; its one row is later
        "Sổ tiết kiệm": "2026-11-20",  # no Cập nhật: today
        "Notion": "2026-11-07",
    }

    groups = {g["id"]: g["name"] for g in envelopes_service.groups_store.all()}
    envelopes = {e["name"]: e for e in envelopes_service.all_envelopes()}
    assert groups[envelopes["Ăn uống"]["group_id"]] == "Thiết yếu"
    assert groups[envelopes["Dự phòng"]["group_id"]] == "Dự phòng"
    food = categories_service.resolve_category_ref("Ăn uống")
    assert envelopes["Ăn uống"]["category_ids"] == [food]

    rows = imported()
    lunch = rows["Cơm trưa"]
    assert (lunch["kind"], lunch["from_amount"], lunch["from_currency"]) == (
        "expense",
        50_000,
        "VND",
    )
    assert lunch["from_account_id"] == accounts["Ngân hàng"]["id"]
    assert (lunch["category"], lunch["date"], lunch["source"], lunch["needs_review"]) == (
        food,
        "2026-11-03",
        "feed",
        False,
    )
    assert lunch["external_refs"] == [{"source": "notion", "ref": "tx-lunch"}]
    assert rows["Cà phê"]["category"] == food
    assert rows["Cà phê"]["from_account_id"] == accounts["Thẻ Visa"]["id"]
    assert (rows["Lương"]["kind"], rows["Lương"]["to_amount"], rows["Lương"]["category"]) == (
        "income",
        20_000_000,
        None,
    )
    assert rows["Quà"]["from_account_id"] == accounts["Notion"]["id"]
    assert (rows["Quà"]["memo"], rows["Quà"]["category"]) == (
        "sinh nhật",
        categories_service.resolve_category_ref("Dự phòng"),
    )


async def test_second_sync_changes_nothing(notion: FakeNotion) -> None:
    await notion_input.sync_notion_input()
    before = (
        ledger_service.accounts.all(),
        ledger_service.transactions.all(),
        envelopes_service.all_envelopes(),
    )

    again = await notion_input.sync_notion_input()

    assert again == {
        "accounts": {"created": 0, "updated": 0, "skipped": 3},
        "envelopes": {"created": 0, "updated": 0, "skipped": 2},
        "transactions": {"created": 0, "updated": 0, "skipped": 5},
    }
    assert (
        ledger_service.accounts.all(),
        ledger_service.transactions.all(),
        envelopes_service.all_envelopes(),
    ) == before


async def test_a_book_restored_without_the_sync_state_does_not_duplicate(
    notion: FakeNotion,
) -> None:
    await notion_input.sync_notion_input()
    before = (
        len(ledger_service.accounts.all()),
        len(ledger_service.transactions.all()),
        len(envelopes_service.all_envelopes()),
    )
    NamespacedSettingsStore("notion_input").save({})

    result = await notion_input.sync_notion_input()

    assert all(c["created"] == 0 for c in result.values())
    assert (
        len(ledger_service.accounts.all()),
        len(ledger_service.transactions.all()),
        len(envelopes_service.all_envelopes()),
    ) == before


async def test_an_edited_row_updates_in_place(notion: FakeNotion) -> None:
    await notion_input.sync_notion_input()
    lunch = imported()["Cơm trưa"]
    count = len(ledger_service.transactions.all())

    edit(
        notion,
        TX,
        "tx-lunch",
        **{
            "Nội dung": title("Cơm trưa + nước"),
            "Chi": formula(65_000),
            "Ghi chú": text("văn phòng"),
        },
    )
    result = await notion_input.sync_notion_input()

    assert result["transactions"] == {"created": 0, "updated": 1, "skipped": 4}
    edited = imported()["Cơm trưa + nước"]
    assert edited["id"] == lunch["id"]
    assert (edited["from_amount"], edited["memo"]) == (65_000, "văn phòng")
    assert len(ledger_service.transactions.all()) == count


async def test_a_chi_retyped_as_thu_flips_the_row(notion: FakeNotion) -> None:
    await notion_input.sync_notion_input()
    lunch = imported()["Cơm trưa"]

    edit(notion, TX, "tx-lunch", Chi=formula(0), Thu=formula(50_000))
    await notion_input.sync_notion_input()

    flipped = imported()["Cơm trưa"]
    assert flipped["id"] == lunch["id"]
    assert (
        flipped["kind"],
        flipped["to_amount"],
        flipped["from_account_id"],
        flipped["from_amount"],
    ) == (
        "income",
        50_000,
        None,
        None,
    )


async def test_a_row_moves_to_the_account_it_is_retyped_onto(notion: FakeNotion) -> None:
    await notion_input.sync_notion_input()

    edit(notion, TX, "tx-gift", **{"Tài khoản": relation("page-save")})
    await notion_input.sync_notion_input()

    assert imported()["Quà"]["from_account_id"] == accounts_by_name()["Sổ tiết kiệm"]["id"]


async def test_two_identical_rows_are_two_purchases(notion: FakeNotion) -> None:
    notion.data[TX].append(
        row("tx-coffee-2", "Cà phê", "2026-11-06", chi=45_000, bank="page-card", env="page-food")
    )

    result = await notion_input.sync_notion_input()

    assert result["transactions"]["created"] == 5
    assert [t["description"] for t in ledger_service.transactions.all()].count("Cà phê") == 2


async def test_a_local_edit_survives_after_notion_updated_the_row(notion: FakeNotion) -> None:
    await notion_input.sync_notion_input()
    edit(notion, TX, "tx-salary", **{"Nội dung": title("Lương tháng 11")})
    await notion_input.sync_notion_input()
    salary = imported()["Lương tháng 11"]
    ledger_service.update_transaction(salary["id"], {"memo": "đã đối chiếu"}, labels_as_given=True)

    result = await notion_input.sync_notion_input()

    assert result["transactions"]["updated"] == 0
    assert imported()["Lương tháng 11"]["memo"] == "đã đối chiếu"


async def test_rows_are_stamped_as_a_notion_import(notion: FakeNotion) -> None:
    await notion_input.sync_notion_input()

    events = audit_service.trace(imported()["Cơm trưa"]["id"])["events"]

    assert [(e["op"], e["actor"], e["surface"]) for e in events] == [
        ("create", "system", "import:notion")
    ]


async def test_a_local_edit_survives_while_notion_is_unchanged(notion: FakeNotion) -> None:
    await notion_input.sync_notion_input()
    salary = imported()["Lương"]
    ledger_service.update_transaction(salary["id"], {"memo": "đã đối chiếu"}, labels_as_given=True)

    result = await notion_input.sync_notion_input()

    assert result["transactions"]["updated"] == 0
    assert imported()["Lương"]["memo"] == "đã đối chiếu"


async def test_a_deleted_row_stays_deleted(notion: FakeNotion) -> None:
    await notion_input.sync_notion_input()
    ledger_service.bulk_delete_transactions([imported()["Quà"]["id"]])

    result = await notion_input.sync_notion_input()

    assert result["transactions"] == {"created": 0, "updated": 0, "skipped": 5}
    assert "Quà" not in imported()


async def test_a_deleted_envelope_is_not_made_again(notion: FakeNotion) -> None:
    await notion_input.sync_notion_input()
    envelopes_service.delete_envelope(
        next(e["id"] for e in envelopes_service.all_envelopes() if e["name"] == "Dự phòng")
    )

    result = await notion_input.sync_notion_input()

    assert result["envelopes"] == {"created": 0, "updated": 0, "skipped": 2}
    assert "Dự phòng" not in {e["name"] for e in envelopes_service.all_envelopes()}


async def test_a_deleted_envelope_leaves_booked_categories_alone(notion: FakeNotion) -> None:
    await notion_input.sync_notion_input()
    gift_category = imported()["Quà"]["category"]
    envelopes_service.delete_envelope(
        next(e["id"] for e in envelopes_service.all_envelopes() if e["name"] == "Dự phòng")
    )
    edit(notion, TX, "tx-gift", **{"Nội dung": title("Quà sinh nhật")})

    result = await notion_input.sync_notion_input()

    assert result["transactions"]["updated"] == 1  # the new wording lands; the category stays
    kept = imported()["Quà sinh nhật"]["category"]
    assert kept is not None
    assert kept == gift_category


async def test_a_row_cleared_of_its_envelope_in_notion_loses_its_category(
    notion: FakeNotion,
) -> None:
    await notion_input.sync_notion_input()

    edit(notion, TX, "tx-lunch", **{"Ngăn tiền": relation()})
    await notion_input.sync_notion_input()

    assert imported()["Cơm trưa"]["category"] is None


async def test_a_new_row_on_a_deleted_account_goes_to_the_catch_all(notion: FakeNotion) -> None:
    await notion_input.sync_notion_input()
    ledger_service.delete_account(accounts_by_name()["Thẻ Visa"]["id"], force=True)

    notion.data[TX].append(row("tx-late", "Trà sữa", "2026-11-15", chi=60_000, bank="page-card"))
    result = await notion_input.sync_notion_input()

    assert result["transactions"]["created"] == 1
    assert imported()["Trà sữa"]["from_account_id"] == accounts_by_name()["Notion"]["id"]
    assert "Thẻ Visa" not in accounts_by_name()


async def test_a_deleted_account_stays_deleted_with_its_rows(notion: FakeNotion, caplog) -> None:
    notion.data[TX] = [
        t for t in notion.data[TX] if t["id"] in ("tx-lunch", "tx-salary", "tx-coffee")
    ]
    await notion_input.sync_notion_input()
    ledger_service.delete_account(accounts_by_name()["Ngân hàng"]["id"], force=True)

    caplog.clear()
    result = await notion_input.sync_notion_input()

    assert result["accounts"] == {"created": 0, "updated": 0, "skipped": 3}
    assert result["transactions"] == {"created": 0, "updated": 0, "skipped": 3}
    # Its rows are still in Notion, but they are not a reason to open the "Notion" catch-all.
    assert set(accounts_by_name()) == {"Thẻ Visa", "Sổ tiết kiệm"}
    assert set(imported()) == {"Cà phê"}
    # Its rows are skipped quietly: not retried, and not warned about on every sync.
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


async def test_an_envelope_renamed_here_is_not_duplicated(notion: FakeNotion) -> None:
    await notion_input.sync_notion_input()
    food = next(e for e in envelopes_service.all_envelopes() if e["name"] == "Ăn uống")
    planning.apply_budget_change(
        {"type": "envelope_rename", "envelope_id": food["id"], "name": "Ăn uống (nhà)"}
    )

    result = await notion_input.sync_notion_input()

    assert result["envelopes"] == {"created": 0, "updated": 0, "skipped": 2}
    assert sorted(e["name"] for e in envelopes_service.all_envelopes() if "Ăn" in e["name"]) == [
        "Ăn uống (nhà)"
    ]


async def test_an_envelope_renamed_and_regrouped_in_notion_follows(notion: FakeNotion) -> None:
    await notion_input.sync_notion_input()
    food = next(e for e in envelopes_service.all_envelopes() if e["name"] == "Ăn uống")

    edit(
        notion,
        ENVELOPE,
        "page-food",
        **{"Ngăn tiền": title("Ăn ngoài"), "Nhóm": select("Công việc")},
    )
    result = await notion_input.sync_notion_input()

    assert result["envelopes"] == {"created": 0, "updated": 1, "skipped": 1}
    renamed = envelopes_service.envelope_store.find_by_id(food["id"])
    groups = {g["id"]: g["name"] for g in envelopes_service.groups_store.all()}
    assert (renamed["name"], groups[renamed["group_id"]]) == ("Ăn ngoài", "Công việc")
    assert renamed["category_ids"] == food["category_ids"]
    assert [
        categories_service.resolve_category_ref(label) for label in ("Ăn uống", "Ăn ngoài")
    ] == [
        food["category_ids"][0],
        None,
    ]  # only the envelope was renamed
    assert imported()["Cơm trưa"]["category"] == food["category_ids"][0]


async def test_the_opening_balance_is_taken_once(notion: FakeNotion) -> None:
    await notion_input.sync_notion_input()

    edit(
        notion,
        ACCOUNT,
        "page-save",
        **{"Số dư": number(120_000_000), "Ghi chú": text("kỳ hạn 6 tháng")},
    )
    result = await notion_input.sync_notion_input()

    assert result["accounts"]["updated"] == 1  # the note follows; the balance does not
    saving = accounts_by_name()["Sổ tiết kiệm"]
    assert saving["note"] == "kỳ hạn 6 tháng"
    assert ledger_service.balances()[saving["id"]] == 100_000_000


async def test_nothing_is_written_when_notion_cannot_be_read(notion: FakeNotion) -> None:
    notion.failing = TX

    with pytest.raises(httpx.HTTPStatusError):
        await notion_input.sync_notion_input()

    assert ledger_service.accounts.all() == []
    assert envelopes_service.all_envelopes() == []


@pytest.mark.parametrize(
    ("loai", "thanh_khoan", "expected"),
    [
        ("Thanh toán", None, "cash"),
        ("Ví điện tử", None, "cash"),
        ("Tiền mặt", None, "cash"),
        ("Tiết kiệm / Sinh lời", "Có kỳ hạn", "fixed_deposit"),
        ("Đầu tư", None, "investment"),
        ("Thẻ tín dụng", "Nợ phải trả", "credit"),  # a card is a card, not a loan
        ("Thanh toán", "Nợ phải trả", "loan"),
    ],
)
async def test_account_type_follows_loai(
    notion: FakeNotion, loai: str, thanh_khoan: str | None, expected: str
) -> None:
    notion.data[ACCOUNT] = [
        account("page-x", "Tài khoản thử", loai, 5_000_000, thanh_khoan=thanh_khoan)
    ]
    notion.data[TX] = []

    await notion_input.sync_notion_input()

    made = accounts_by_name()["Tài khoản thử"]
    assert made["type"] == expected
    # A loan is a debt whichever sign Notion typed it with; every other Số dư is taken as typed.
    assert ledger_service.balances()[made["id"]] == (
        -5_000_000 if expected == "loan" else 5_000_000
    )
